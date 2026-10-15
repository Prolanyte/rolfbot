"""Offline usage policy and safe results; no storage or integration side effects."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

USER_DAILY_LIMIT = 20
GLOBAL_DAILY_LIMIT = 100
COOLDOWN = timedelta(seconds=30)
MAX_INPUT_CODE_POINTS = 1_000
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class Code(StrEnum):
    OK = "ok"
    RESERVED = "reserved"
    DISPATCH_GRANTED = "dispatch_granted"
    COMPLETED = "completed"
    REFUNDED = "refunded"
    ALREADY_COMPLETED = "already_completed"
    ALREADY_REFUNDED = "already_refunded"
    INVALID_INPUT = "invalid_input"
    INPUT_TOO_LONG = "input_too_long"
    INVALID_IDENTIFIER = "invalid_identifier"
    INVALID_TIME = "invalid_time"
    COOLDOWN = "cooldown"
    USER_LIMIT = "user_limit"
    GLOBAL_LIMIT = "global_limit"
    DUPLICATE_ATTEMPT = "duplicate_attempt"
    NOT_FOUND = "not_found"
    INVALID_TRANSITION = "invalid_transition"
    STORAGE_UNAVAILABLE = "storage_unavailable"
    SERVICE_DISABLED = "service_disabled"


class BlockReason(StrEnum):
    PROJECT_LIMIT = "project_limit"
    ORGANIZATION_LIMIT = "organization_limit"
    CREDIT_EXHAUSTED = "credit_exhausted"
    BILLING_LIMIT = "billing_limit"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    CONFIGURATION = "configuration"


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Reported counts only; unknown is None, never an invented zero."""

    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for value in (self.input_tokens, self.output_tokens):
            if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
                raise ValueError("Token counts must be nonnegative SQLite integers or unknown.")


class State(StrEnum):
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    REFUNDED = "refunded"


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    day: str
    global_used: int
    user_used: int | None
    reset_at: datetime
    cooldown_until: datetime | None


@dataclass(frozen=True, slots=True)
class Result:
    """Compare code explicitly. Only DISPATCH_GRANTED authorizes an API attempt.

    No user input, identifiers, paths or underlying exception text are returned.
    Status/next_eligible_at are advisory snapshots, never dispatch permission.
    """

    code: Code
    state: State | None = None
    next_eligible_at: datetime | None = None
    quota: QuotaStatus | None = None
    block_reason: BlockReason | None = None


def validate_input(value: object) -> Result:
    """Count original Unicode code points, not bytes, graphemes or tokens."""
    if not isinstance(value, str) or not value.strip():
        return Result(Code.INVALID_INPUT)
    if len(value) > MAX_INPUT_CODE_POINTS:
        return Result(Code.INPUT_TOO_LONG)
    if "\x00" in value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return Result(Code.INVALID_INPUT)
    return Result(Code.OK)


def valid_discord_id(value: object) -> bool:
    """Canonical positive unsigned 64-bit decimal string; never guild-scoped."""
    return (
        isinstance(value, str)
        and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None
        and int(value) <= 2**64 - 1
    )


def valid_attempt_id(value: object) -> bool:
    # Shared namespace for user interactions and scheduled generations.
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value) is not None


@dataclass(frozen=True, slots=True)
class Moment:
    utc: datetime
    microseconds: int
    day: str
    reset_at: datetime


def moment(value: object, timezone: ZoneInfo) -> Moment | None:
    """Reject naive/nonexistent times and dates outside 1970–9998 in UTC/local time.

    Ambiguous local times must carry the intended fold; UTC is recommended.
    Epoch microseconds avoid floating-point rounding at cooldown boundaries.
    """
    if not isinstance(value, datetime):
        return None
    try:
        if value.utcoffset() is None:
            return None
        utc = value.astimezone(UTC)
        if utc.astimezone(value.tzinfo).replace(tzinfo=None) != value.replace(tzinfo=None):
            return None
        local = utc.astimezone(timezone)
        if not (1970 <= utc.year <= 9998 and 1970 <= local.year <= 9998):
            return None
        reset = datetime.combine(local.date() + timedelta(days=1), time(), timezone)
        delta = utc - _EPOCH
        microseconds = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        return Moment(utc, microseconds, local.date().isoformat(), reset.astimezone(UTC))
    except (ValueError, OverflowError, TypeError):
        return None


def from_microseconds(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)
