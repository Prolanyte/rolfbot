"""Offline CLI and a small asyncio lifecycle; external services stay disabled."""

import argparse
import asyncio
import logging
import os
import signal
from collections.abc import Mapping, Sequence

from rolfbot.config import CREDENTIAL_NAMES, ConfigurationError, load_settings
from rolfbot.logging_setup import configure_logging


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default error can echo arbitrary argv, including secrets.
        self.print_usage()
        self.exit(2, "Invalid arguments. Use 'python -m rolfbot --help'.\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="python -m rolfbot",
        description="Rolfbot runtime foundation. All external integrations are disabled.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="validate configuration offline (no credentials needed)")
    commands.add_parser("run", help="refuse startup until external integrations are implemented")
    return parser


async def _execute(command: str) -> int:
    logger = logging.getLogger("rolfbot")
    logger.info("event=startup command=%s", command)
    try:
        # Yield once so pending cancellation is observed before diagnostics.
        # Future asynchronous work can live inside this same cleanup boundary.
        await asyncio.sleep(0)
        if command == "check-config":
            print(
                "Configuration OK (offline). Discord, OpenAI, scheduling and music "
                "are disabled. No credentials were verified with a service."
            )
            return 0
        logger.error(
            "event=startup_refused Bot runtime is unavailable: implement the "
            "OpenAI/Discord integrations first (Milestones 3–4)."
        )
        return 3
    finally:
        logger.info("event=shutdown command=%s", command)


async def _async_main(command: str) -> int:
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
        return await _execute(command)
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
        settings = load_settings(source)
        logger.setLevel(settings.log_level)
        return asyncio.run(_async_main(arguments.command))
    except ConfigurationError as error:
        logger.error("event=configuration_error %s", error)
        return 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("event=cancelled signal=SIGINT_or_task_cancel")
        return 130
    except Exception:
        logger.error("event=internal_error Unexpected failure; no exception details logged.")
        return 1
