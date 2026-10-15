from dataclasses import FrozenInstanceError

import pytest

from rolfbot.config import ConfigurationError, load_settings


def test_defaults_and_immutable_settings():
    settings = load_settings({})
    assert settings.log_level == "INFO"
    assert settings.timezone.key == "Europe/Oslo"
    assert settings.openai_api_key is None
    assert settings.discord_bot_token is None
    assert settings.database_path is None
    assert settings.openai_model == "gpt-6-luna"
    assert settings.openai_reasoning_effort == "none"
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


def test_guild_mapping_is_private_immutable_and_supports_two_guilds():
    settings = load_settings({
        "ROLFBOT_GUILD_CHANNELS": '{"123456789":["234567890"],"345678901":["456789012","567890123"]}',
    })
    assert len(settings.guild_channels) == 2
    assert settings.guild_channels[0].channel_ids == frozenset({"234567890"})
    assert "123456789" not in repr(settings)
    assert "234567890" not in repr(settings.guild_channels)
    assert load_settings({}).guild_channels == ()


@pytest.mark.parametrize("value", [
    "not a mapping", "", "{}", "[]", "null", '{"1":[]}', '{"01":["2"]}',
    '{"1":"2"}', '{"1":[2]}', '{"1":[true]}', '{"1":["0"]}',
    '{"1":["2","2"]}', '{"1":["2"],"1":["3"]}',
    '{"1":["2"],"3":["2"]}', '{"1":["18446744073709551616"]}',
    '{"1":["private-value"]}',
])
def test_invalid_guild_mapping_fails_safely(value):
    with pytest.raises(ConfigurationError, match="ROLFBOT_GUILD_CHANNELS") as caught:
        load_settings({"ROLFBOT_GUILD_CHANNELS": value})
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize("name,value", [
    ("OPENAI_MODEL", "unapproved-expensive-model"), ("OPENAI_MODEL", ""),
    ("OPENAI_MODEL", "gpt-6-luna "), ("OPENAI_MODEL", "private\nmodel"),
    ("OPENAI_REASONING_EFFORT", "low"), ("OPENAI_REASONING_EFFORT", "max"),
    ("OPENAI_REASONING_EFFORT", ""), ("OPENAI_REASONING_EFFORT", "NONE"),
])
def test_openai_policy_settings_reject_unapproved_values(name, value):
    with pytest.raises(ConfigurationError, match=name) as caught:
        load_settings({name: value})
    assert "private" not in str(caught.value)


def test_database_path_is_validated_without_file_access(tmp_path):
    path = tmp_path / "missing-parent" / "usage.sqlite3"
    settings = load_settings({"ROLFBOT_DATABASE_PATH": str(path)})
    assert settings.database_path == path
    assert str(path) not in repr(settings)
    assert not path.parent.exists()


@pytest.mark.parametrize("value", ["", "relative.sqlite3", ":memory:", "file:/tmp/private", "/", "/tmp/", "/tmp/../private", "/tmp/private\x00", "/tmp/private\n", " /tmp/private"])
def test_invalid_database_paths_have_safe_errors(value):
    with pytest.raises(ConfigurationError, match="ROLFBOT_DATABASE_PATH") as caught:
        load_settings({"ROLFBOT_DATABASE_PATH": value})
    assert "private" not in str(caught.value)
