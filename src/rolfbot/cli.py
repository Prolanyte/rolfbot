"""Offline diagnostics and explicit live commands with owned async cleanup."""

import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Mapping, Sequence

from rolfbot.config import CREDENTIAL_NAMES, ConfigurationError, load_settings, require_discord_settings
from rolfbot.logging_setup import configure_logging
from rolfbot.runtime import StartupError


class _Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, allow_abbrev=False)

    def error(self, message: str) -> None:
        # argparse's default error can echo arbitrary argv, including secrets.
        self.print_usage()
        self.exit(2, "Invalid arguments. Use 'python -m rolfbot --help'.\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m rolfbot",
        description="Rolfbot: offline diagnostics, opt-in API smoke test and Discord slash command.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="validate configuration offline (no credentials needed)")
    commands.add_parser("init-db", help="explicitly provision persistent usage storage offline")
    smoke = commands.add_parser("smoke-test", help="make one quota-protected, billable OpenAI attempt")
    smoke.add_argument("--confirm-billable", action="store_true", required=True,
                       help="explicitly authorize this single live OpenAI attempt")
    commands.add_parser("run", help="start the configured Discord slash-command bot")
    return parser


async def _execute(command: str, settings=None) -> int:
    logger = logging.getLogger("rolfbot")
    logger.info("event=startup command=%s", command)
    try:
        # Yield once so pending cancellation is observed before diagnostics.
        await asyncio.sleep(0)
        if command == "check-config":
            print(
                "Configuration OK (offline). No clients started or credentials verified "
                "with a service. Scheduling and music are disabled."
            )
            return 0
        from rolfbot.runtime import open_store, run_discord, smoke_test
        assert settings is not None
        store = await open_store(settings, provision=command == "init-db")
        if command == "init-db":
            print("Usage database ready (schema 2). Existing accounting and service blocks are preserved.")
            return 0
        if command == "smoke-test":
            return await smoke_test(settings, store)
        return await run_discord(settings, store)
    finally:
        logger.info("event=shutdown command=%s", command)


async def _async_main(command: str, settings=None) -> int:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    received_sigterm = False
    previous_handler = signal.getsignal(signal.SIGTERM)

    def request_shutdown() -> None:
        nonlocal received_sigterm
        received_sigterm = True
        task.cancel()

    # The supported platform is Linux, and the CLI owns its main thread.
    loop.add_signal_handler(signal.SIGTERM, request_shutdown)
    try:
        return await _execute(command, settings)
    except asyncio.CancelledError:
        if not received_sigterm:
            raise
        logging.getLogger("rolfbot").info("event=cancelled signal=SIGTERM")
        return 143
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        signal.signal(signal.SIGTERM, previous_handler)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Return a process exit code; only __main__ raises SystemExit."""
    try:
        arguments = _parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)

    source = os.environ if environ is None else environ
    # Bootstrap redaction before validating possibly-invalid configuration.
    secrets = tuple(source.get(name, "") for name in CREDENTIAL_NAMES)
    logger = configure_logging(secrets=secrets)
    try:
        settings = load_settings(
            source, require_openai=arguments.command in ("run", "smoke-test"),
            require_discord=arguments.command == "run",
        )
        if arguments.command == "run":
            require_discord_settings(settings)
        logger.setLevel(settings.log_level)
        return asyncio.run(_async_main(arguments.command, settings))
    except ConfigurationError as error:
        logger.error("event=configuration_error %s", error)
        return 2
    except StartupError as error:
        logger.error("event=startup_refused %s", error)
        return 3
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("event=cancelled signal=SIGINT_or_task_cancel")
        return 130
    except Exception:
        logger.error("event=internal_error Unexpected failure; no exception details logged.")
        return 1
