import asyncio
import json
import os
import sqlite3
import stat
from contextlib import closing
from pathlib import Path

import discord
import httpx2
import pytest

from rolfbot import cli, runtime
from rolfbot.ai_service import AIService
from rolfbot.config import ConfigurationError, load_settings
from rolfbot.usage import BlockReason, Code, TokenUsage
from test_ai_service import Harness, response


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "usage.sqlite3")


def environment(path):
    return {
        "ROLFBOT_DATABASE_PATH": str(path),
        "OPENAI_API_KEY": "synthetic-openai-value",
        "DISCORD_BOT_TOKEN": "synthetic-discord-value",
        "ROLFBOT_GUILD_CHANNELS": '{"10":["100"]}',
    }


def inject_transport(monkeypatch, harness):
    services = []

    def factory(settings, store):
        service = AIService(
            settings, store, http_transport=httpx2.MockTransport(harness.handle),
            clock=lambda: harness.clock.now, monotonic=lambda: harness.clock.elapsed,
        )
        services.append(service)
        return service

    monkeypatch.setattr(runtime, "AIService", factory)
    return services


@pytest.mark.parametrize("arguments", [
    ["smoke-test"], ["smoke-test", "--confirm"],
    ["smoke-test", "--confirm-billable=false"], ["run", "--confirm-billable"],
])
def test_billable_confirmation_must_be_explicit_and_exact(cli_process, tmp_path, arguments):
    path = tmp_path / "missing.sqlite3"
    result = cli_process(*arguments, extra_env=environment(path))
    assert result.returncode == 2
    assert not path.exists()


@pytest.mark.parametrize("command", ["run", "smoke-test"])
def test_missing_existing_storage_prevents_clients(cli_process, tmp_path, command):
    path = tmp_path / "missing.sqlite3"
    args = [command] + (["--confirm-billable"] if command == "smoke-test" else [])
    result = cli_process(*args, extra_env=environment(path))
    assert result.returncode == 3
    assert "Usage database" in result.stderr
    assert not path.exists()
    assert "synthetic-" not in result.stdout + result.stderr


@pytest.mark.parametrize("variable,value", [
    ("OPENAI_API_KEY", ""), ("OPENAI_API_KEY", "replace_with_key"),
    ("DISCORD_BOT_TOKEN", ""), ("DISCORD_BOT_TOKEN", "replace_with_token"),
    ("ROLFBOT_GUILD_CHANNELS", None),
])
def test_run_validates_credentials_and_mapping_before_clients(cli_process, tmp_path, variable, value):
    source = environment(tmp_path / "missing.sqlite3")
    if value is None:
        del source[variable]
    else:
        source[variable] = value
    result = cli_process("run", extra_env=source)
    assert result.returncode == 2
    assert variable in result.stderr
    assert "synthetic-" not in result.stdout + result.stderr


def test_explicit_provisioning_is_offline_private_repeat_safe_and_preserves_blocks(cli_process, tmp_path):
    path = tmp_path / "usage.sqlite3"
    source = {"ROLFBOT_DATABASE_PATH": str(path)}
    assert cli_process("check-config", extra_env=source).returncode == 0
    assert not path.exists()
    assert cli_process("init-db", extra_env=source).returncode == 0
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        db.execute("UPDATE metadata SET ai_block='project_limit'")
        db.commit()
    assert cli_process("init-db", extra_env=source).returncode == 0
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT ai_block FROM metadata").fetchone() == ("project_limit",)
    assert cli_process("run", extra_env=environment(path)).returncode == 3
    assert cli_process("smoke-test", "--confirm-billable", extra_env=environment(path)).returncode == 3


@pytest.mark.parametrize("kind", ["empty", "corrupt", "old", "future", "timezone"])
def test_bad_storage_never_replaced_or_migrated(cli_process, tmp_path, kind):
    path = tmp_path / "usage.sqlite3"
    if kind in ("empty", "corrupt"):
        path.write_bytes(b"" if kind == "empty" else b"not a sqlite database")
    else:
        with closing(sqlite3.connect(path)) as db:
            if kind == "old":
                fixture = Path(__file__).parent / "fixtures" / "usage_v1.sql"
                db.executescript(fixture.read_text())
                db.execute("PRAGMA user_version=1")
                db.commit()
            elif kind == "future":
                db.execute("PRAGMA user_version=99")
        if kind == "timezone":
            path.unlink()
            assert cli_process("init-db", extra_env={"ROLFBOT_DATABASE_PATH": str(path), "ROLFBOT_TIMEZONE": "UTC"}).returncode == 0
    original = path.read_bytes()
    for args in (("init-db",), ("run",), ("smoke-test", "--confirm-billable")):
        assert cli_process(*args, extra_env=environment(path)).returncode == 3
        assert path.read_bytes() == original


def test_provisioning_requires_existing_parent_and_explicit_path(cli_process, tmp_path):
    assert cli_process("init-db").returncode == 2
    path = tmp_path / "missing-parent" / "usage.sqlite3"
    assert cli_process("init-db", extra_env={"ROLFBOT_DATABASE_PATH": str(path)}).returncode == 3
    assert not path.parent.exists()


@pytest.mark.parametrize("target", ["file", "parent"])
def test_read_only_existing_database_refuses_startup(harness, cli_process, target):
    if os.geteuid() == 0:
        pytest.skip("Root bypasses file permissions")
    path = harness.store.path if target == "file" else harness.store.path.parent
    previous = stat.S_IMODE(path.stat().st_mode)
    path.chmod(0o400 if target == "file" else 0o500)
    try:
        result = cli_process("run", extra_env=environment(harness.store.path))
        assert result.returncode == 3
        assert "Usage database" in result.stderr
    finally:
        path.chmod(previous)


def test_cli_storage_must_live_outside_release_and_symlink_alias(monkeypatch, tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    (release / "pyproject.toml").write_text("# synthetic release marker\n")
    alias = tmp_path / "alias"
    alias.symlink_to(release, target_is_directory=True)
    monkeypatch.chdir(release)
    for path in (release / "usage.sqlite3", alias / "usage.sqlite3"):
        with pytest.raises(ConfigurationError, match="outside"):
            asyncio.run(runtime.open_store(load_settings({"ROLFBOT_DATABASE_PATH": str(path)}), provision=True))
        assert not path.exists()


def test_startup_write_probe_preserves_all_usage_and_block_metadata(harness):
    def snapshot():
        with closing(sqlite3.connect(harness.store.path)) as db:
            return tuple(db.iterdump())

    async def scenario():
        await harness.store.reserve_user("1", "probe", "hello", now=harness.clock.now, guild_id="10")
        await harness.store.mark_dispatched("probe", now=harness.clock.now)
        await harness.store.complete(
            "probe", now=harness.clock.now, usage=TokenUsage(12, 9),
            block_reason=BlockReason.PROJECT_LIMIT,
        )
        before = snapshot()
        result = await harness.store.validate()
        assert result.code is Code.OK and result.block_reason is BlockReason.PROJECT_LIMIT
        assert snapshot() == before

    asyncio.run(scenario())


def test_confirmed_smoke_uses_one_counted_attempt_and_sanitized_output(harness, monkeypatch, capsys):
    services = inject_transport(monkeypatch, harness)
    source = environment(harness.store.path)
    del source["DISCORD_BOT_TOKEN"]
    del source["ROLFBOT_GUILD_CHANNELS"]
    assert cli.main(["smoke-test", "--confirm-billable"], environ=source) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {"outcome": "success", "denial": None, "accounting": "completed",
                      "input_tokens": 42, "output_tokens": 9, "text": "Hei!"}
    assert len(harness.requests) == 1
    assert json.loads(harness.requests[0].content)["input"] == [{"role": "user", "content": runtime.SMOKE_PROMPT}]
    assert harness.rows()[0][0].startswith("smoke-")
    with closing(sqlite3.connect(harness.store.path)) as db:
        assert db.execute("SELECT user_id,guild_id FROM attempts").fetchone() == (runtime.SMOKE_USER_ID, None)
    assert services[0]._transport._sdk.is_closed()
    assert "synthetic-" not in captured.out + captured.err
    # A second explicit invocation obeys the synthetic user's normal cooldown.
    assert cli.main(["smoke-test", "--confirm-billable"], environ=source) == 4
    assert json.loads(capsys.readouterr().out)["denial"] == "cooldown"
    assert len(harness.requests) == 1


@pytest.mark.parametrize("status,code,outcome", [
    (401, "invalid_api_key", "authentication"),
    (403, "permission_denied", "permission"),
    (429, "project_spend_limit_exceeded", "project_limit"),
    (429, "organization_spend_limit_exceeded", "organization_limit"),
    (429, "credit_balance_exhausted", "credit_exhausted"),
    (500, "server_error", "server_error"),
])
def test_smoke_provider_errors_are_safe_single_attempt_and_cleaned_up(harness, monkeypatch, capsys, status, code, outcome):
    services = inject_transport(monkeypatch, harness)

    async def handle(request):
        return httpx2.Response(status, headers={"x-should-retry": "true"}, json={
            "error": {"message": "private-provider-detail", "code": code, "type": code},
        })

    harness.handler = handle
    assert cli.main(["smoke-test", "--confirm-billable"], environ=environment(harness.store.path)) == 4
    captured = capsys.readouterr()
    assert json.loads(captured.out)["outcome"] == outcome
    assert "private-provider" not in captured.out + captured.err
    assert len(harness.requests) == 1 and services[0]._transport._sdk.is_closed()


def test_smoke_timeout_reports_unknown_usage_without_retry(harness, monkeypatch, capsys):
    services = inject_transport(monkeypatch, harness)

    async def handle(request):
        raise httpx2.ReadTimeout("private-timeout-detail", request=request)

    harness.handler = handle
    assert cli.main(["smoke-test", "--confirm-billable"], environ=environment(harness.store.path)) == 4
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "timeout"
    assert result["input_tokens"] is result["output_tokens"] is None
    assert len(harness.requests) == 1 and services[0]._transport._sdk.is_closed()


def test_smoke_response_cannot_print_configured_credentials(harness, monkeypatch, capsys):
    inject_transport(monkeypatch, harness)

    async def handle(request):
        return httpx2.Response(200, json=response("synthetic-openai-value synthetic-discord-value"))

    harness.handler = handle
    assert cli.main(["smoke-test", "--confirm-billable"], environ=environment(harness.store.path)) == 0
    captured = capsys.readouterr()
    assert "synthetic-" not in captured.out + captured.err
    assert json.loads(captured.out)["text"] == "[REDACTED] [REDACTED]"


def test_run_starts_real_client_with_validated_settings_but_no_automatic_ai(harness, monkeypatch):
    services = inject_transport(monkeypatch, harness)
    clients = []

    async def start(client, token, *, reconnect):
        clients.append(client)
        assert token == "synthetic-discord-value" and reconnect is True
        assert len(client.tree.get_commands(guild=discord.Object(id=10))) == 1
        assert client.tree.get_commands(guild=discord.Object(id=20)) == []
        assert harness.requests == []

    monkeypatch.setattr(discord.Client, "start", start)
    assert cli.main(["run"], environ=environment(harness.store.path)) == 0
    assert len(clients) == 1 and clients[0].is_closed()
    assert services[0]._transport._sdk.is_closed()
    assert harness.rows() == []


def test_failed_discord_start_is_sanitized_and_closes_openai(harness, monkeypatch, capsys):
    services = inject_transport(monkeypatch, harness)
    clients = []

    async def start(client, *args, **kwargs):
        clients.append(client)
        raise discord.LoginFailure("private-token-detail")

    monkeypatch.setattr(discord.Client, "start", start)
    assert cli.main(["run"], environ=environment(harness.store.path)) == 3
    captured = capsys.readouterr()
    assert "Discord could not start" in captured.err
    assert "private-" not in captured.out + captured.err
    assert services[0]._transport._sdk.is_closed() and clients[0].is_closed()
    assert harness.requests == []


@pytest.mark.parametrize("command", ["run", "smoke-test"])
def test_cancelled_live_entry_point_closes_clients(harness, monkeypatch, command):
    services = inject_transport(monkeypatch, harness)

    async def scenario():
        entered = asyncio.Event()
        clients = []

        async def wait(*args, **kwargs):
            if command == "run":
                clients.append(args[0])
            entered.set()
            await asyncio.Event().wait()

        if command == "run":
            monkeypatch.setattr(discord.Client, "start", wait)
            function = runtime.run_discord
        else:
            harness.handler = wait
            function = runtime.smoke_test
        task = asyncio.create_task(function(load_settings(environment(harness.store.path)), harness.store))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert services[0]._transport._sdk.is_closed()
        if command == "run":
            assert clients[0].is_closed() and harness.requests == []
        else:
            assert len(harness.requests) == 1 and harness.rows()[0][1] == "dispatched"

    asyncio.run(scenario())
