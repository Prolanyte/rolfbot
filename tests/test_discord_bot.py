"""Real command tree + mocked interactions + real AI service/SDK/SQLite barrier."""

import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import httpx2
import pytest

from rolfbot import discord_bot
from rolfbot.config import guild_channels
from rolfbot.usage import Code, Result
from rolfbot.usage_store import UsageStore
from test_ai_service import Harness, response


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "usage.sqlite3")


def settings_for(harness):
    return replace(
        harness.settings, discord_bot_token="synthetic-discord-token",
        guild_channels=guild_channels('{"10":["100"],"20":["200"]}'),
    )


class Interaction:
    def __init__(self, *, user=1, guild=10, channel=100, attempt=1000, text="hello"):
        self.type = discord.InteractionType.application_command
        self.id = attempt
        self.guild_id = guild
        self.channel_id = channel
        self.user = SimpleNamespace(id=user, bot=False)
        self.guild = SimpleNamespace(id=guild)
        self.channel = SimpleNamespace(type=discord.ChannelType.text)
        self.app_permissions = discord.Permissions(view_channel=True, send_messages=True)
        self.data = {"name": "rolfbot", "type": 1, "guild_id": str(guild),
                     "options": [{"name": "message", "type": 3, "value": text}]}
        self._state = None
        self.command_failed = False
        self.expired = False
        self.done = False
        self.events = []
        self.response = SimpleNamespace(
            is_done=lambda: self.done,
            defer=AsyncMock(side_effect=self._defer),
            send_message=AsyncMock(),
        )
        self.edit_original_response = AsyncMock(side_effect=self._edit)

    def is_expired(self):
        return self.expired

    async def _defer(self, **kwargs):
        self.events.append("defer")
        self.done = True

    async def _edit(self, **kwargs):
        self.events.append("edit")


def assert_no_mentions(kwargs):
    mentions = kwargs["allowed_mentions"]
    assert mentions.to_dict() == {"parse": []}
    assert not any((mentions.everyone, mentions.roles, mentions.users, mentions.replied_user))


def test_registered_command_uses_authenticated_identity_defers_and_accounts(harness):
    async def scenario():
        service = harness.service()
        client = discord_bot.create_client(settings_for(harness), service)
        async with client:
            assert client.intents.value == discord.Intents(guilds=True, guild_messages=True).value
            assert client.tree.get_commands() == []
            assert not client.cached_messages
            for guild in (10, 20):
                commands = client.tree.get_commands(guild=discord.Object(id=guild))
                assert [command.name for command in commands] == ["rolfbot"]
                command = commands[0]
                assert command.guild_only
                assert [parameter.name for parameter in command.parameters] == ["message"]
            interaction = Interaction(text="Speak to @everyone <@123> <@&456>")

            async def handle(request):
                assert interaction.events == ["defer"]
                assert json.loads(request.content)["input"][0]["content"] == interaction.data["options"][0]["value"]
                return httpx2.Response(200, json=response("Hello @everyone <@123> <@&456>"))

            harness.handler = handle
            await client.tree._call(interaction)
            assert interaction.events == ["defer", "edit"]
            interaction.response.defer.assert_awaited_once_with(thinking=True)
            assert_no_mentions(interaction.edit_original_response.call_args.kwargs)
            assert interaction.edit_original_response.call_args.kwargs["content"] == "Hello @everyone <@123> <@&456>"
            with closing(sqlite3.connect(harness.store.path)) as db:
                assert db.execute("SELECT attempt_id,user_id,guild_id,state FROM attempts").fetchall() == [
                    ("1000", "1", "10", "completed"),
                ]
            assert len(harness.requests) == 1
        assert service._transport._sdk.is_closed()
        assert client.is_closed()
        await client.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["dm", "guild", "channel", "cross_guild", "bot", "identity", "permissions", "thread"])
def test_access_denials_before_admission(harness, kind):
    async def scenario():
        service = harness.service()
        async with discord_bot.create_client(settings_for(harness), service) as client:
            interaction = Interaction()
            if kind == "dm":
                interaction.guild_id = None
            elif kind == "guild":
                interaction.guild_id = 30
            elif kind == "channel":
                interaction.channel_id = 300
            elif kind == "cross_guild":
                interaction.channel_id = 200
            elif kind == "bot":
                interaction.user.bot = True
            elif kind == "identity":
                interaction.user.id = True
            elif kind == "permissions":
                interaction.app_permissions.send_messages = False
            else:
                interaction.channel.type = discord.ChannelType.public_thread
            # Also test runtime checks directly: Discord's server-side guild
            # registration must never be the only authorization boundary.
            await client.command_handler.handle(interaction, "hello")
            interaction.response.send_message.assert_awaited_once()
            kwargs = interaction.response.send_message.call_args.kwargs
            assert kwargs["ephemeral"]
            assert_no_mentions(kwargs)
            interaction.response.defer.assert_not_awaited()
        assert harness.rows() == [] and harness.requests == []

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["cooldown", "user", "global", "storage", "duplicate"])
def test_command_denials_make_no_api_calls(harness, kind):
    async def scenario():
        now = harness.clock.now
        if kind == "cooldown":
            await harness.store.reserve_user("1", "old", "hello", now=now)
        elif kind == "user":
            for index in range(20):
                await harness.store.reserve_user("1", f"old-{index}", "hello", now=now - timedelta(seconds=30 * (20 - index)))
        elif kind == "global":
            for index in range(100):
                await harness.store.reserve_scheduled(f"old-{index}", now=now)
        elif kind == "duplicate":
            await harness.store.reserve_user("1", "1000", "hello", now=now)
        else:
            harness.store.path.unlink()
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            interaction = Interaction()
            await client.tree._call(interaction)
            interaction.edit_original_response.assert_awaited_once()
            text = interaction.edit_original_response.call_args.kwargs["content"]
            assert {"cooldown": "cooldown", "user": "20-request", "global": "100-request", "storage": "unavailable", "duplicate": "already"}[kind] in text
        assert harness.requests == []

    asyncio.run(scenario())


def test_two_guilds_share_user_allowance_and_duplicate_delivery_is_single_use(harness):
    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            first, duplicate = Interaction(), Interaction()
            await asyncio.gather(client.tree._call(first), client.tree._call(duplicate))
            assert len(harness.requests) == 1
            second_guild = Interaction(guild=20, channel=200, attempt=1001)
            await client.tree._call(second_guild)
            assert "cooldown" in second_guild.edit_original_response.call_args.kwargs["content"]
            harness.clock.advance(31)
            second_guild = Interaction(guild=20, channel=200, attempt=1002)
            await client.tree._call(second_guild)
            assert len(harness.requests) == 2
            assert (await harness.store.status(now=harness.clock.now, user_id="1")).quota.user_used == 2
        # Recreating the Discord and AI clients never replays an interaction.
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            await client.tree._call(Interaction())
        assert len(harness.requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("ambiguous", [False, True])
def test_dispatch_failure_never_calls_api(harness, monkeypatch, ambiguous):
    original = UsageStore.mark_dispatched

    async def dispatch(store, *args, **kwargs):
        if ambiguous:
            assert (await original(store, *args, **kwargs)).code is Code.DISPATCH_GRANTED
        return Result(Code.STORAGE_UNAVAILABLE)

    monkeypatch.setattr(UsageStore, "mark_dispatched", dispatch)

    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            await client.tree._call(Interaction())
        assert harness.rows()[0][1] == ("dispatched" if ambiguous else "reserved")
        assert harness.requests == []

    asyncio.run(scenario())


@pytest.mark.parametrize("text,expected_calls", [("a" * 1000, 1), ("😀" * 1000, 1), ("a" * 1001, 0), ("", 0), ("\ud800", 0)], ids=["boundary", "unicode", "too_long", "empty", "surrogate"])
def test_command_input_uses_existing_validator(harness, text, expected_calls):
    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            await client.tree._call(Interaction(text=text))
        assert len(harness.requests) == expected_calls

    asyncio.run(scenario())


@pytest.mark.parametrize("body,expected", [
    (response("😀" * 900), "😀" * 900),
    (response("😀" * 901), "usable answer"),
    (response(""), "usable answer"),
    (response("unsafe\x1b[31m"), "usable answer"),
    (response("partial", status="incomplete"), "usable answer"),
    ({"unexpected": "private-body"}, "usable answer"),
], ids=["boundary", "too_long", "empty", "control", "incomplete", "malformed"])
def test_valid_and_invalid_generated_messages_never_regenerate(harness, body, expected):
    async def handle(request):
        return httpx2.Response(200, json=body)

    harness.handler = handle

    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            interaction = Interaction()
            await client.tree._call(interaction)
            content = interaction.edit_original_response.call_args.kwargs["content"]
            assert expected in content
            assert len(content.encode("utf-16-le")) // 2 <= 1800
            assert_no_mentions(interaction.edit_original_response.call_args.kwargs)
        assert len(harness.requests) == 1 and harness.rows()[0][1] == "completed"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["expired", "answered", "defer_error", "defer_timeout", "cancel_defer"])
def test_failed_or_cancelled_defer_never_reserves(harness, monkeypatch, kind):
    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            interaction = Interaction()
            pending = asyncio.Event()

            async def hang(**kwargs):
                pending.set()
                await asyncio.Event().wait()

            if kind == "expired":
                interaction.expired = True
            elif kind == "answered":
                interaction.done = True
            elif kind == "defer_error":
                interaction.response.defer.side_effect = RuntimeError("private-token")
            else:
                interaction.response.defer.side_effect = hang
                if kind == "defer_timeout":
                    monkeypatch.setattr(discord_bot, "DEFER_TIMEOUT_SECONDS", 0)
            task = asyncio.create_task(client.tree._call(interaction))
            if kind == "cancel_defer":
                await pending.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
        assert harness.requests == [] and harness.rows() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["delivery_error", "delivery_timeout", "cancel_network", "cancel_delivery", "shutdown_network"])
def test_delivery_and_cancellation_preserve_counted_attempt(harness, monkeypatch, kind):
    async def scenario():
        pending = asyncio.Event()

        async def hang(*args, **kwargs):
            pending.set()
            await asyncio.Event().wait()

        service = harness.service()
        client = discord_bot.create_client(settings_for(harness), service)
        async with client:
            interaction = Interaction()
            if kind in ("cancel_network", "shutdown_network"):
                harness.handler = hang
            elif kind == "delivery_error":
                interaction.edit_original_response.side_effect = RuntimeError("private-discord-token")
            else:
                interaction.edit_original_response.side_effect = hang
                if kind == "delivery_timeout":
                    monkeypatch.setattr(discord_bot, "DELIVERY_TIMEOUT_SECONDS", 0)
            task = asyncio.create_task(client.tree._call(interaction))
            if kind in ("cancel_network", "cancel_delivery", "shutdown_network"):
                await pending.wait()
                if kind == "shutdown_network":
                    await client.close()
                else:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
        assert service._transport._sdk.is_closed()
        assert client.is_closed()
        assert len(harness.requests) == 1
        assert harness.rows()[0][1] in ("dispatched", "completed")
        assert (await harness.store.status(now=harness.clock.now)).quota.global_used == 1

    asyncio.run(scenario())


def test_sync_once_per_guild_not_on_ready_or_reconnect(harness):
    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            client.tree.sync = AsyncMock()
            await client.setup_hook()
            await client.on_ready()
            await client.on_ready()
            await client.setup_hook()
            assert [call.kwargs["guild"].id for call in client.tree.sync.call_args_list] == [10, 20]
            assert harness.requests == [] and harness.rows() == []

    asyncio.run(scenario())


def test_partial_sync_failure_is_not_retried_and_clients_close(harness):
    async def scenario():
        service = harness.service()
        client = discord_bot.create_client(settings_for(harness), service)
        with pytest.raises(RuntimeError):
            async with client:
                client.tree.sync = AsyncMock(side_effect=[[], RuntimeError("private-detail")])
                await client.setup_hook()
        assert client.tree.sync.await_count == 2
        assert client.is_closed() and service._transport._sdk.is_closed()
        assert harness.requests == []

    asyncio.run(scenario())


def test_cancelled_shutdown_still_closes_owned_resources(harness, monkeypatch):
    async def scenario():
        service = harness.service()
        client = discord_bot.create_client(settings_for(harness), service)
        entered, release = asyncio.Event(), asyncio.Event()
        original_close = service.aclose

        async def delayed_close():
            entered.set()
            await release.wait()
            await original_close()

        monkeypatch.setattr(service, "aclose", delayed_close)
        async with client:
            task = asyncio.create_task(client.close())
            await entered.wait()
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert service._transport._sdk.is_closed() and client.is_closed()

    asyncio.run(scenario())


def test_adapter_requires_accounted_service_and_closed_client_denies_new_work(harness):
    with pytest.raises(TypeError, match="SQLite-protected"):
        discord_bot.CommandHandler(settings_for(harness), object())

    async def scenario():
        client = discord_bot.create_client(settings_for(harness), harness.service())
        async with client:
            await client.close()
            interaction = Interaction()
            await client.command_handler.handle(interaction, "hello")
            interaction.response.defer.assert_not_awaited()
        assert harness.requests == [] and harness.rows() == []

    asyncio.run(scenario())


def test_discord_errors_and_debug_logging_do_not_leak_content(harness, capsys):
    from rolfbot.logging_setup import configure_logging

    configure_logging("DEBUG")

    async def scenario():
        async with discord_bot.create_client(settings_for(harness), harness.service()) as client:
            interaction = Interaction(text="private-user-prompt")
            interaction.edit_original_response.side_effect = RuntimeError("private-interaction-token")
            await client.tree._call(interaction)
            await client.on_error("private-event", "private-payload")
            await client.tree.on_error(Interaction(), RuntimeError("private-command-error"))
            logging.getLogger("discord.gateway").debug("private-gateway-payload")
            logging.getLogger("discord.new_child").error("private-new-child")

    asyncio.run(scenario())
    captured = capsys.readouterr()
    assert "private-" not in captured.out + captured.err
    assert "discord_answer_delivery_failed" in captured.err
