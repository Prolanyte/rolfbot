import asyncio
import importlib.metadata
import signal
import subprocess
import sys

import pytest

from rolfbot import cli


def test_help_needs_no_configuration_or_credentials(cli_process):
    result = cli_process("--help", extra_env={"ROLFBOT_TIMEZONE": "invalid"})
    assert result.returncode == 0
    assert "check-config" in result.stdout
    assert "disabled" in result.stdout
    assert result.stderr == ""


def test_offline_check_from_outside_checkout(cli_process):
    result = cli_process("check-config")
    assert result.returncode == 0
    assert "Configuration OK (offline)" in result.stdout
    assert "event=startup" in result.stderr
    assert "event=shutdown" in result.stderr


def test_placeholder_credentials_do_not_block_offline_checks(cli_process):
    result = cli_process("check-config", extra_env={
        "OPENAI_API_KEY": "replace_with_your_openai_api_key",
        "DISCORD_BOT_TOKEN": "replace_with_your_discord_bot_token",
    })
    assert result.returncode == 0
    assert "replace_with" not in result.stdout + result.stderr


def test_invalid_configuration_has_safe_error_and_exit_code(cli_process):
    secret = "synthetic-private-value"
    result = cli_process("check-config", extra_env={
        "OPENAI_API_KEY": secret, "ROLFBOT_TIMEZONE": secret,
    })
    assert result.returncode == 2
    assert "ROLFBOT_TIMEZONE" in result.stderr
    assert secret not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("arguments", [(), ("unknown",), ("check-config", "synthetic-secret")])
def test_argument_errors_never_echo_arbitrary_input(cli_process, arguments):
    result = cli_process(*arguments)
    assert result.returncode == 2
    assert "Invalid arguments" in result.stderr
    assert "synthetic-secret" not in result.stdout + result.stderr


def test_run_always_refuses_even_with_credentials_and_future_settings(cli_process):
    result = cli_process("run", extra_env={
        "OPENAI_API_KEY": "synthetic-openai-value",
        "DISCORD_BOT_TOKEN": "synthetic-discord-value",
        "OPENAI_MODEL": "gpt-6-luna",
        "ROLFBOT_GUILD_CHANNELS": "{}",
    })
    assert result.returncode == 3
    assert "startup_refused" in result.stderr
    assert "Milestones 3–4" in result.stderr
    assert "event=shutdown" in result.stderr
    assert "synthetic-" not in result.stdout + result.stderr


def test_log_level_controls_lifecycle_messages(cli_process):
    result = cli_process("check-config", extra_env={"ROLFBOT_LOG_LEVEL": "ERROR"})
    assert result.returncode == 0
    assert result.stderr == ""
    assert "Configuration OK" in result.stdout


def test_dotenv_is_not_loaded(cli_process, tmp_path):
    (tmp_path / ".env").write_text("ROLFBOT_TIMEZONE=not-a-timezone\n")
    assert cli_process("check-config").returncode == 0


def test_imports_are_quiet_and_do_not_load_integrations(child_environment, tmp_path):
    script = """
import importlib
import logging
import pkgutil
import sys
def forbid_database(event, args):
    if event == 'sqlite3.connect':
        raise AssertionError('Database opened during import')
sys.addaudithook(forbid_database)
import rolfbot
root_handlers = list(logging.getLogger().handlers)
for module in pkgutil.walk_packages(rolfbot.__path__, 'rolfbot.'):
    importlib.import_module(module.name)
assert logging.getLogger().handlers == root_handlers
assert not logging.getLogger('rolfbot').handlers
assert not {'discord', 'openai', 'youtube_dl', 'yt_dlp'} & sys.modules.keys()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=child_environment,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""


def test_check_config_and_run_never_create_database(cli_process, tmp_path):
    path = tmp_path / "unused.sqlite3"
    for command, code in (("check-config", 0), ("run", 3)):
        result = cli_process(command, extra_env={"ROLFBOT_DATABASE_PATH": str(path)})
        assert result.returncode == code
        assert str(path) not in result.stdout + result.stderr
        assert not path.exists()


def test_invalid_database_path_diagnostic_is_safe(cli_process):
    result = cli_process("check-config", extra_env={"ROLFBOT_DATABASE_PATH": "private-relative-path"})
    assert result.returncode == 2
    assert "ROLFBOT_DATABASE_PATH" in result.stderr
    assert "private-relative-path" not in result.stdout + result.stderr


def test_installed_package_has_no_runtime_dependencies():
    requirements = importlib.metadata.requires("rolfbot") or []
    assert all('extra == "dev"' in requirement for requirement in requirements)
    for name in ("discord.py", "openai", "yt-dlp", "youtube_dl"):
        with pytest.raises(importlib.metadata.PackageNotFoundError):
            importlib.metadata.distribution(name)


def test_unexpected_failure_is_safe_and_restores_sigterm(monkeypatch, capsys):
    async def broken(command):
        raise RuntimeError("private failure detail")

    monkeypatch.setattr(cli, "_execute", broken)
    previous = signal.getsignal(signal.SIGTERM)
    assert cli.main(["check-config"], environ={}) == 1
    assert signal.getsignal(signal.SIGTERM) == previous
    assert "private failure detail" not in capsys.readouterr().err


def test_cancellation_runs_cleanup_and_propagates(capsys):
    cli.configure_logging()

    async def exercise():
        task = asyncio.create_task(cli._execute("check-config"))
        await asyncio.sleep(0)  # The task has entered its try/finally block.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())
    assert "event=shutdown" in capsys.readouterr().err


@pytest.mark.parametrize("signal_name, expected", [("SIGINT", 130), ("SIGTERM", 143)])
def test_signals_cancel_async_work_and_restore_handlers(
    child_environment, tmp_path, signal_name, expected
):
    # Make the finite diagnostic wait for a signal without exposing a daemon mode.
    script = f"""
import asyncio
import os
import signal
from rolfbot import cli
previous = signal.getsignal(signal.SIGTERM)
original_sleep = asyncio.sleep
async def interrupted_sleep(delay):
    asyncio.get_running_loop().call_soon(os.kill, os.getpid(), signal.{signal_name})
    await original_sleep(60)
cli.asyncio.sleep = interrupted_sleep
result = cli.main(['check-config'], environ={{}})
assert signal.getsignal(signal.SIGTERM) == previous
raise SystemExit(result)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=child_environment,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == expected, result.stderr
    assert "event=shutdown" in result.stderr
    assert "Traceback" not in result.stderr
