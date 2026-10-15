"""Explicit, environment-only settings; no file, database or network setup."""

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
CREDENTIAL_NAMES = ("OPENAI_API_KEY", "DISCORD_BOT_TOKEN")
APPROVED_MODEL = "gpt-6-luna"
APPROVED_REASONING = "none"


class ConfigurationError(ValueError):
    """A safe, actionable configuration error containing no supplied values."""


@dataclass(frozen=True, slots=True, repr=False)
class GuildChannels:
    guild_id: str
    channel_ids: frozenset[str]


@dataclass(frozen=True, slots=True)
class Settings:
    log_level: str
    timezone: ZoneInfo
    openai_model: str = APPROVED_MODEL
    openai_reasoning_effort: str = APPROVED_REASONING
    database_path: Path | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    discord_bot_token: str | None = field(default=None, repr=False)
    guild_channels: tuple[GuildChannels, ...] = field(default=(), repr=False)


def guild_channels(value: str) -> tuple[GuildChannels, ...]:
    """Private JSON mapping of canonical guild IDs to nonempty channel-ID lists."""
    from rolfbot.usage import valid_discord_id

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate key")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=unique_object)
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("Expected mapping")
        result = []
        all_channels = set()
        for guild, channels in parsed.items():
            if (
                not valid_discord_id(guild) or not isinstance(channels, list)
                or not channels or any(not valid_discord_id(item) for item in channels)
                or len(set(channels)) != len(channels)
                or all_channels.intersection(channels)
            ):
                raise ValueError("Invalid mapping")
            all_channels.update(channels)
            result.append(GuildChannels(guild, frozenset(channels)))
        return tuple(result)
    except (ValueError, TypeError, RecursionError):
        raise ConfigurationError(
            "ROLFBOT_GUILD_CHANNELS must be a nonempty JSON object mapping unique "
            "guild ID strings to lists of unique channel ID strings."
        ) from None


def database_path(value: str) -> Path:
    """Validate syntax only; never open, create or probe the configured file."""
    if (
        not isinstance(value, str)
        or not value
        or any(ord(character) < 32 for character in value)
        or value != value.strip()
        or value.endswith("/")
        or not Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ConfigurationError(
            "ROLFBOT_DATABASE_PATH must be an explicit absolute file path "
            "without control characters or parent traversal."
        )
    return Path(value)


def _credential(
    environ: Mapping[str, str], name: str, *, required: bool
) -> str | None:
    value = environ.get(name)
    if not required:
        return value or None
    if not value or not value.strip():
        raise ConfigurationError(f"{name} is required for this integration.")

    lowered = value.casefold()
    placeholder = (
        lowered in {"changeme", "change_me", "change-me", "todo", "test", "token", "api_key"}
        or any(
            marker in lowered
            for marker in (
                "replace_with", "your_", "your-", "insert_here",
                "placeholder", "example", "<", ">",
            )
        )
    )
    if placeholder:
        raise ConfigurationError(f"{name} must be a real credential, not a placeholder.")
    if any(character.isspace() for character in value):
        raise ConfigurationError(f"{name} must not contain whitespace.")
    return value


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    require_openai: bool = False,
    require_discord: bool = False,
) -> Settings:
    """Load only implemented settings; requirements validate but never enable clients.

    Offline callers leave both requirements false. Live integrations must
    explicitly request their credentials; loading settings creates no client.
    """
    source = os.environ if environ is None else environ
    model = source.get("OPENAI_MODEL", APPROVED_MODEL)
    reasoning = source.get("OPENAI_REASONING_EFFORT", APPROVED_REASONING)
    validate_openai_options(model, reasoning)
    level = source.get("ROLFBOT_LOG_LEVEL", "INFO").strip().upper()
    if level not in LOG_LEVELS:
        raise ConfigurationError(
            "ROLFBOT_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL."
        )

    timezone_name = source.get("ROLFBOT_TIMEZONE", "Europe/Oslo")
    try:
        timezone = ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ConfigurationError(
            "ROLFBOT_TIMEZONE must name an installed IANA timezone, such as "
            "Europe/Oslo or UTC. Check the name and the operating system's tzdata."
        ) from None

    return Settings(
        log_level=level,
        timezone=timezone,
        openai_model=model,
        openai_reasoning_effort=reasoning,
        database_path=(
            database_path(source["ROLFBOT_DATABASE_PATH"])
            if "ROLFBOT_DATABASE_PATH" in source else None
        ),
        openai_api_key=_credential(source, "OPENAI_API_KEY", required=require_openai),
        discord_bot_token=_credential(
            source, "DISCORD_BOT_TOKEN", required=require_discord
        ),
        guild_channels=(
            guild_channels(source["ROLFBOT_GUILD_CHANNELS"])
            if "ROLFBOT_GUILD_CHANNELS" in source else ()
        ),
    )


def validate_openai_options(model: str, reasoning: str) -> None:
    if model != APPROVED_MODEL:
        raise ConfigurationError("OPENAI_MODEL must be the approved gpt-6-luna model.")
    if reasoning != APPROVED_REASONING:
        raise ConfigurationError("OPENAI_REASONING_EFFORT must be none.")


def require_openai_key(settings: Settings) -> str:
    """Validate explicit settings again at client construction; never consult env."""
    validate_openai_options(settings.openai_model, settings.openai_reasoning_effort)
    key = _credential(
        {"OPENAI_API_KEY": settings.openai_api_key or ""}, "OPENAI_API_KEY", required=True
    )
    assert key is not None
    return key


def require_discord_settings(settings: Settings) -> str:
    token = _credential(
        {"DISCORD_BOT_TOKEN": settings.discord_bot_token or ""},
        "DISCORD_BOT_TOKEN", required=True,
    )
    if not settings.guild_channels:
        raise ConfigurationError("ROLFBOT_GUILD_CHANNELS is required to run Discord.")
    assert token is not None
    return token
