"""One-line stderr logs for journald; configure explicitly, never on import."""

import logging
import sys
from collections.abc import Iterable
from typing import TextIO


class RedactingFormatter(logging.Formatter):
    """Redact configured credentials after interpolation, without mutating records."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__("level=%(levelname)s logger=%(name)s %(message)s")
        variants = set()
        for secret in secrets:
            if secret:
                variants.update((secret, repr(secret)[1:-1]))
                if secret.strip():
                    variants.add(secret.strip())
        self._secrets = tuple(sorted(variants, key=len, reverse=True))

    def formatException(self, exc_info: tuple) -> str:
        # Exception text, traceback source lines and locals may contain prompts
        # or credentials. Omit those details even at DEBUG level.
        return "exception_details=omitted"

    def formatStack(self, stack_info: str) -> str:
        return "stack_details=omitted"

    def format(self, record: logging.LogRecord) -> str:
        # A previous handler can cache an unredacted traceback in exc_text.
        safe_record = logging.makeLogRecord(record.__dict__.copy())
        safe_record.exc_text = None
        try:
            message = super().format(safe_record)
        except Exception:
            return "level=ERROR logger=rolfbot event=log_format_error"
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        return message.replace("\r", r"\r").replace("\n", r"\n")


def configure_logging(
    level: str = "INFO",
    *,
    secrets: Iterable[str] = (),
    stream: TextIO | None = None,
) -> logging.Logger:
    """Own the rolfbot logger only; leave host/root loggers untouched."""
    logger = logging.getLogger("rolfbot")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(RedactingFormatter(secrets))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
