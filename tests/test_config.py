from dataclasses import FrozenInstanceError

import pytest

from rolfbot.config import ConfigurationError, load_settings


def test_defaults_and_immutable_settings():
    settings = load_settings({})
    assert settings.log_level == "INFO"
    assert settings.timezone.key == "Europe/Oslo"
    assert settings.openai_api_key is None
    assert settings.discord_bot_token is None
    with pytest.raises(FrozenInstanceError):
        settings.log_level = "DEBUG"


def test_explicit_environment_and_normalized_level(monkeypatch):
    monkeypatch.setenv("ROLFBOT_LOG_LEVEL", " debug ")
    monkeypatch.setenv("ROLFBOT_TIMEZONE", "UTC")
    assert load_settings().log_level == "DEBUG"
    assert load_settings().timezone.key == "UTC"
    # An explicit empty mapping must not fall through to os.environ.
    assert load_settings({}).timezone.key == "Europe/Oslo"


@pytest.mark.parametrize("value", ["", "TRACE", "20", "private-value\nINFO"])
def test_invalid_log_levels_do_not_echo_values(value):
    with pytest.raises(ConfigurationError, match="ROLFBOT_LOG_LEVEL") as caught:
        load_settings({"ROLFBOT_LOG_LEVEL": value})
    if value:
        assert value not in str(caught.value)


@pytest.mark.parametrize("value", ["", "Not/AZone", "/etc/passwd", "../UTC", "UTC\x00private"])
def test_invalid_timezones_do_not_echo_values(value):
    with pytest.raises(ConfigurationError, match="installed IANA timezone") as caught:
        load_settings({"ROLFBOT_TIMEZONE": value})
    if value:
        assert value not in str(caught.value)


def test_missing_timezone_data_is_actionable(monkeypatch):
    from zoneinfo import ZoneInfoNotFoundError
    from rolfbot import config

    def missing(name):
        raise ZoneInfoNotFoundError("private underlying detail")

    monkeypatch.setattr(config, "ZoneInfo", missing)
    with pytest.raises(ConfigurationError, match="tzdata") as caught:
        load_settings({})
    assert "private underlying detail" not in str(caught.value)


@pytest.mark.parametrize(
    ("name", "requirement"),
    [("OPENAI_API_KEY", "require_openai"), ("DISCORD_BOT_TOKEN", "require_discord")],
)
def test_missing_credentials_only_fail_when_required(name, requirement):
    load_settings({})
    with pytest.raises(ConfigurationError, match=name):
        load_settings({}, **{requirement: True})
    for value in ("", "   "):
        with pytest.raises(ConfigurationError, match="required"):
            load_settings({name: value}, **{requirement: True})


@pytest.mark.parametrize(
    "value",
    ["replace_with_your_key", "YOUR_DISCORD_BOT_TOKEN", "sk-placeholder", "changeme", "<token>"],
)
def test_placeholders_are_allowed_offline_but_rejected_when_required(value):
    environment = {"OPENAI_API_KEY": value, "DISCORD_BOT_TOKEN": value}
    settings = load_settings(environment)
    assert value not in repr(settings)
    for requirement in ("require_openai", "require_discord"):
        with pytest.raises(ConfigurationError, match="placeholder") as caught:
            load_settings(environment, **{requirement: True})
        assert value not in str(caught.value)


def test_credentials_validate_independently_and_remain_hidden():
    # Deliberately synthetic values; no provider format or remote validity check.
    environment = {"OPENAI_API_KEY": "synthetic-openai-value"}
    settings = load_settings(environment, require_openai=True)
    assert settings.openai_api_key == environment["OPENAI_API_KEY"]
    assert settings.discord_bot_token is None
    assert environment["OPENAI_API_KEY"] not in str(settings)
    with pytest.raises(ConfigurationError, match="DISCORD_BOT_TOKEN"):
        load_settings(environment, require_discord=True)


@pytest.mark.parametrize("value", [" leading", "trailing ", "two words", "line\nbreak"])
def test_required_credentials_reject_whitespace_without_echo(value):
    with pytest.raises(ConfigurationError, match="whitespace") as caught:
        load_settings({"DISCORD_BOT_TOKEN": value}, require_discord=True)
    assert value not in str(caught.value)


def test_future_settings_do_not_enable_or_configure_integrations():
    settings = load_settings({
        "OPENAI_MODEL": "unapproved-expensive-model",
        "OPENAI_REASONING_EFFORT": "max",
        "ROLFBOT_GUILD_CHANNELS": "not a mapping",
        "ROLFBOT_DATABASE_PATH": "/must/not/be/created.sqlite3",
    })
    assert settings == load_settings({})
