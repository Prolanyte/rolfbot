"""Offline gateway events through the real AI SDK and SQLite dispatch barrier."""

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

from rolfbot import discord_bot, discord_messages
from rolfbot.config import guild_channels
from rolfbot.conversation import ConversationMemory, IDLE_SECONDS
from rolfbot.usage import Code, Result
from rolfbot.usage_store import UsageStore
from test_ai_service import Harness, response
from test_discord_bot import Interaction, assert_no_mentions, settings_for

BOT = 888888


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "usage.sqlite3")


class Message:
    def __init__(self, text=f"<@{BOT}> hello", *, user=1, guild=10, channel=100, id=1000, reply_to=None):
        self.id, self.content = id, text
        self.guild = SimpleNamespace(id=guild, me=SimpleNamespace(id=BOT))
        self.permissions = discord.Permissions(view_channel=True, send_messages=True, read_message_history=True)
        self.channel = SimpleNamespace(
            id=channel, type=discord.ChannelType.text, permissions_for=lambda member: self.permissions,
            typing=AsyncMock(),
        )
        self.author = SimpleNamespace(id=user, bot=False, name="private-username")
        self.webhook_id = None
        self.type = discord.MessageType.reply if reply_to is not None else discord.MessageType.default
        self.reference = None if reply_to is None else discord.MessageReference(
            message_id=reply_to, guild_id=guild, channel_id=channel,
        )
        self.mentions = [SimpleNamespace(id=BOT)]
        self.reply = AsyncMock(return_value=SimpleNamespace(id=id + 100_000))
        self.delete = AsyncMock()
        self.edit = AsyncMock()

    @property
    def attachments(self):
        raise AssertionError("Attachments must never be inspected")

    @property
    def embeds(self):
        raise AssertionError("Embeds must never be inspected")


def client_for(harness):
    client = discord_bot.create_client(settings_for(harness), harness.service())
    client._connection.user = SimpleNamespace(id=BOT)
    return client


def inputs(harness, index=-1):
    return json.loads(harness.requests[index].content)["input"]


@pytest.mark.parametrize("text,clean", [
    (f"<@{BOT}> hello", "hello"), (f"<@!{BOT}> hello", "hello"),
    (f"hello <@{BOT}>", "hello"), (f"hey <@!{BOT}>, hello", "hey , hello"),
    (f"<@{BOT}> <@!{BOT}> hello", "hello"),
    (f"<@{BOT}> " + "😀" * 1000, "😀" * 1000),
])
def test_mentions_route_once_strip_bot_and_reply_safely(harness, text, clean):
    async def handler(request):
        return httpx2.Response(200, json=response("Hello @everyone <@123> <@&456>"))

    harness.handler = handler

    async def scenario():
        async with client_for(harness) as client:
            message = Message(text)
            await client.on_message(message)
            assert len(harness.requests) == 1
            assert inputs(harness) == [{"role": "user", "content": "Participant 1:\n" + clean}]
            message.reply.assert_awaited_once()
            kwargs = message.reply.call_args.kwargs
            assert kwargs["mention_author"] is False and kwargs["suppress_embeds"] is True
            assert_no_mentions(kwargs)
            assert message.reply.call_args.args == ("Hello @everyone <@123> <@&456>",)
            message.delete.assert_not_awaited()
            message.edit.assert_not_awaited()
            assert message.content == text
            assert len(client.message_handler.memory) == 1
            assert not client.intents.message_content and not client.intents.members and not client.intents.presences
            assert client.intents.guild_messages and not client.intents.dm_messages
            assert not client.message_handler._active and not client.message_handler._parents
            message.channel.typing.assert_awaited()
            with closing(sqlite3.connect(harness.store.path)) as db:
                assert db.execute("SELECT attempt_id,user_id,guild_id,state FROM attempts").fetchall() == [
                    ("message:1000", "1", "10", "completed"),
                ]
            assert "private-username" not in harness.requests[0].content.decode()
        assert len(client.message_handler.memory) == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", [
    "ordinary", "other_mention", "metadata_only", "syntax_only", "bot", "self", "webhook",
    "dm", "guild", "channel", "cross_guild", "thread", "system", "empty", "missing_content",
    "permission", "history_permission", "identity", "no_identity", "unknown_reply",
])
def test_ignored_events_never_reserve_fetch_or_respond(harness, kind):
    async def scenario():
        async with client_for(harness) as client:
            message = Message()
            if kind == "ordinary":
                message.content, message.mentions = "nearby ordinary message", []
            elif kind == "other_mention":
                message.content, message.mentions = "<@999> hello", [SimpleNamespace(id=999)]
            elif kind == "metadata_only":
                message.content = "hello"
            elif kind == "syntax_only":
                message.mentions = []
            elif kind == "bot":
                message.author.bot = True
            elif kind == "self":
                message.author.id = BOT
            elif kind == "webhook":
                message.webhook_id = 999
            elif kind == "dm":
                message.guild = None
            elif kind == "guild":
                message.guild.id = 30
            elif kind == "channel":
                message.channel.id = 300
            elif kind == "cross_guild":
                message.channel.id = 200
            elif kind == "thread":
                message.channel.type = discord.ChannelType.public_thread
            elif kind == "system":
                message.type = discord.MessageType.pins_add
            elif kind == "empty":
                message.content = ""
            elif kind == "missing_content":
                message.content = None
            elif kind == "permission":
                message.permissions.send_messages = False
            elif kind == "history_permission":
                message.permissions.read_message_history = False
            elif kind == "identity":
                message.author.id = True
            elif kind == "no_identity":
                client._connection.user = None
            else:
                message = Message("unrelated reply", reply_to=123)
            await client.on_message(message)
            message.reply.assert_not_awaited()
            message.channel.typing.assert_not_awaited()
        assert harness.rows() == [] and harness.requests == []

    asyncio.run(scenario())


@pytest.mark.parametrize("text", [f"<@{BOT}>", f" <@!{BOT}> \n ", f"<@{BOT}> " + "x" * 1001, f"<@{BOT}> \ud800", f"<@{BOT}> \x00"])
def test_invalid_stripped_prompt_gets_safe_reply_before_quota(harness, text):
    async def scenario():
        async with client_for(harness) as client:
            message = Message(text)
            await client.on_message(message)
            message.reply.assert_awaited_once()
            assert_no_mentions(message.reply.call_args.kwargs)
            assert len(client.message_handler.memory) == 0
        assert harness.rows() == [] and harness.requests == []

    asyncio.run(scenario())


def test_multiuser_chain_old_reply_branches_and_standalone_mentions(harness):
    async def scenario():
        async with client_for(harness) as client:
            await client.on_message(Message(f"<@{BOT}> root"))
            await client.on_message(Message("left branch", id=1001, user=2, reply_to=101000))
            assert inputs(harness) == [
                {"role": "user", "content": "Participant 1:\nroot"},
                {"role": "assistant", "content": "Hei!"},
                {"role": "user", "content": "Participant 2:\nleft branch"},
            ]
            await client.on_message(Message("right branch", id=1002, user=3, reply_to=101000))
            assert "left branch" not in json.dumps(inputs(harness))
            harness.clock.advance(31)
            await client.on_message(Message(f"<@!{BOT}> back again", id=1003, reply_to=101001))
            assert inputs(harness)[-1]["content"] == "Participant 1:\nback again"
            assert len(inputs(harness)) == 5
            await client.on_message(Message(f"<@{BOT}> separate", id=1004, user=4))
            assert inputs(harness) == [{"role": "user", "content": "Participant 1:\nseparate"}]
            hidden = Message("", id=1005, user=5, reply_to=101004)
            await client.on_message(hidden)
            hidden.reply.assert_not_awaited()
            assert len(harness.requests) == 5

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["unknown", "cross_scope", "forged_channel", "forged_guild", "forward", "expired", "restart"])
def test_missing_or_unrelated_reference_with_mention_starts_fresh(harness, kind):
    async def scenario():
        client = client_for(harness)
        elapsed = [0.0]
        client.message_handler.memory = ConversationMemory(clock=lambda: elapsed[0])
        async with client:
            await client.on_message(Message(f"<@{BOT}> private-root"))
            followup = Message(f"<@{BOT}> new", id=1001, user=2, reply_to=101000)
            if kind == "unknown":
                followup.reference.message_id = 123
            elif kind == "cross_scope":
                followup = Message(f"<@{BOT}> new", guild=20, channel=200, id=1001, user=2, reply_to=101000)
            elif kind == "forged_channel":
                followup.reference.channel_id = 200
            elif kind == "forged_guild":
                followup.reference.guild_id = 20
            elif kind == "forward":
                followup.reference.type = discord.MessageReferenceType.forward
            elif kind == "expired":
                elapsed[0] = IDLE_SECONDS
            elif kind == "restart":
                await client.close()
                client = client_for(harness)
            try:
                await client.on_message(followup)
                assert inputs(harness) == [{"role": "user", "content": "Participant 1:\nnew"}]
                assert len(harness.requests) == 2
            finally:
                if kind == "restart":
                    await client.close()

    asyncio.run(scenario())


def test_slash_and_messages_share_cooldown_and_namespace_survives_restart(harness):
    async def scenario():
        async with client_for(harness) as client:
            await client.tree._call(Interaction())
            denied = Message(guild=20, channel=200)
            await client.on_message(denied)
            assert "cooldown" in denied.reply.call_args.args[0]
            assert len(client.message_handler.memory) == 0
            harness.clock.advance(31)
            await client.on_message(Message())
            assert len(harness.requests) == 2  # Same numeric ID, distinct namespace.
            duplicate = Message()
            await client.on_message(duplicate)
            duplicate.reply.assert_not_awaited()
            assert (await harness.store.status(now=harness.clock.now, user_id="1")).quota.user_used == 2
        async with client_for(harness) as client:
            await client.on_message(Message())
            assert len(client.message_handler.memory) == 0
        assert len(harness.requests) == 2
        assert {row[0] for row in harness.rows()} == {"1000", "message:1000"}

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["user", "global"])
def test_shared_limits_cannot_be_evaded_by_new_conversation_or_channel(harness, kind):
    async def scenario():
        now = harness.clock.now
        if kind == "user":
            for number in range(19):
                await harness.store.reserve_user("1", f"old-{number}", "q", now=now - timedelta(seconds=(20 - number) * 31))
        else:
            for number in range(99):
                await harness.store.reserve_scheduled(f"old-{number}", now=now)
        async with client_for(harness) as client:
            await client.tree._call(Interaction())
            harness.clock.advance(31)
            denied = Message(guild=20, channel=200)
            await client.on_message(denied)
            assert ("20-request" if kind == "user" else "100-request") in denied.reply.call_args.args[0]
            assert len(client.message_handler.memory) == 0
        assert len(harness.requests) == 1

    asyncio.run(scenario())


def test_concurrent_duplicate_and_same_parent_are_bounded_without_queue(harness):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait(request):
            entered.set()
            await release.wait()
            return httpx2.Response(200, json=response())

        async with client_for(harness) as client:
            await client.on_message(Message())
            harness.handler = wait
            first = asyncio.create_task(client.on_message(Message("first", id=1001, user=2, reply_to=101000)))
            await entered.wait()
            duplicate = Message("first", id=1001, user=2, reply_to=101000)
            busy = Message("second", id=1002, user=3, reply_to=101000)
            await client.on_message(duplicate)
            await client.on_message(busy)
            duplicate.reply.assert_not_awaited()
            assert "answering a reply" in busy.reply.call_args.args[0]
            assert len(harness.requests) == 2
            assert len(client.message_handler.memory) == 1
            release.set()
            await first
            assert len(client.message_handler.memory) == 2
            assert not client.message_handler._parents and not client.message_handler._active
            assert [pair.user for pair in client.message_handler.memory.lookup((10, 100, 101001)).exchanges] == ["hello", "first"]

    asyncio.run(scenario())


def test_unrelated_conversations_use_two_slots_and_overflow_is_ignored(harness):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def wait(request):
            if len(harness.requests) == 2:
                entered.set()
            await release.wait()
            return httpx2.Response(200, json=response())

        harness.handler = wait
        async with client_for(harness) as client:
            first = asyncio.create_task(client.on_message(Message()))
            second = asyncio.create_task(client.on_message(Message(id=1001, user=2)))
            await entered.wait()
            overflow = Message(id=1002, user=3)
            await client.on_message(overflow)
            overflow.reply.assert_not_awaited()
            assert len(client.message_handler._active) == 2
            assert len(harness.requests) == 2
            release.set()
            await asyncio.gather(first, second)
            assert len(client.message_handler.memory) == 2
        assert len(harness.rows()) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["error", "timeout", "cancel"])
def test_unconfirmed_delivery_never_regenerates_or_stores_history(harness, monkeypatch, kind):
    monkeypatch.setattr(discord_messages, "DELIVERY_TIMEOUT_SECONDS", 0.02)

    async def scenario():
        entered = asyncio.Event()

        async def delivery(*args, **kwargs):
            entered.set()
            if kind == "error":
                raise RuntimeError("private-provider-body")
            await asyncio.Event().wait()

        async with client_for(harness) as client:
            message = Message()
            message.reply.side_effect = delivery
            task = asyncio.create_task(client.on_message(message))
            await entered.wait()
            if kind == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
            assert len(client.message_handler.memory) == 0
            await client.on_message(Message())
            assert len(client.message_handler.memory) == 0
            assert not client.message_handler._active
        assert len(harness.requests) == 1 and harness.rows()[0][1] == "completed"

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["timeout", "billing", "incomplete"])
def test_provider_failures_count_once_and_never_become_history(harness, kind):
    async def handler(request):
        if kind == "timeout":
            raise httpx2.ReadTimeout("private-error", request=request)
        if kind == "billing":
            return httpx2.Response(429, json={"error": {"code": "insufficient_quota", "message": "private-error"}})
        return httpx2.Response(200, json=response("partial-private", status="incomplete"))

    harness.handler = handler

    async def scenario():
        async with client_for(harness) as client:
            first = Message()
            await client.on_message(first)
            first.reply.assert_awaited_once()
            assert "private" not in first.reply.call_args.args[0]
            assert len(client.message_handler.memory) == 0
            await client.on_message(Message(id=1001, user=2))
            assert len(client.message_handler.memory) == 0
        assert len(harness.requests) == 1 and len(harness.rows()) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("shutdown", [False, True])
def test_cancellation_and_shutdown_join_typing_clear_memory_and_keep_accounting(harness, shutdown):
    async def scenario():
        entered, typing_started, typing_stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def wait(request):
            entered.set()
            await asyncio.Event().wait()

        async def typing():
            typing_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                typing_stopped.set()

        async with client_for(harness) as client:
            await client.on_message(Message())
            harness.handler = wait
            message = Message("followup", id=1001, user=2, reply_to=101000)
            message.channel.typing.side_effect = typing
            task = asyncio.create_task(client.on_message(message))
            await entered.wait()
            await typing_started.wait()
            if shutdown:
                await client.close()
                assert len(client.message_handler.memory) == 0
            else:
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert typing_stopped.is_set()
            message.reply.assert_not_awaited()
            assert not client.message_handler._parents and not client.message_handler._active
            assert len(harness.requests) == 2 and harness.rows()[1][1] == "dispatched"
        assert len(client.message_handler.memory) == 0 and client.message_handler.memory._timer is None

    asyncio.run(scenario())


def test_typing_failure_is_optional_and_logs_never_include_content(harness, caplog):
    caplog.set_level(logging.DEBUG, logger="rolfbot")

    async def scenario():
        async with client_for(harness) as client:
            message = Message(f"<@{BOT}> private-prompt")
            message.channel.typing.side_effect = RuntimeError("private-typing-body")
            message.reply.side_effect = RuntimeError("private-delivery-body")
            await client.on_message(message)
            assert len(harness.requests) == 1
            assert len(client.message_handler.memory) == 0
        assert "discord_typing_failed" in caplog.text and "discord_answer_delivery_failed" in caplog.text
        assert "private" not in caplog.text and "Hei!" not in caplog.text
        assert "synthetic-offline-credential" not in caplog.text and "synthetic-discord-token" not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("scope", ["channel", "guild"])
def test_reference_ids_cannot_cross_allowed_scopes(harness, scope):
    async def scenario():
        settings = replace(settings_for(harness), guild_channels=guild_channels('{"10":["100","101"],"20":["200"]}'))
        async with discord_bot.create_client(settings, harness.service()) as client:
            client._connection.user = SimpleNamespace(id=BOT)
            await client.on_message(Message())
            guild, channel = (10, 101) if scope == "channel" else (20, 200)
            unmentioned = Message("followup", id=1001, user=2, guild=guild, channel=channel, reply_to=101000)
            await client.on_message(unmentioned)
            unmentioned.reply.assert_not_awaited()
            mentioned = Message(f"<@{BOT}> fresh", id=1002, user=2, guild=guild, channel=channel, reply_to=101000)
            await client.on_message(mentioned)
            assert inputs(harness) == [{"role": "user", "content": "Participant 1:\nfresh"}]
            assert len(harness.requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("ambiguous", [False, True])
def test_message_dispatch_requires_exact_committed_grant(harness, monkeypatch, ambiguous):
    original = UsageStore.mark_dispatched

    async def dispatch(store, *args, **kwargs):
        if ambiguous:
            assert (await original(store, *args, **kwargs)).code is Code.DISPATCH_GRANTED
        return Result(Code.STORAGE_UNAVAILABLE)

    monkeypatch.setattr(UsageStore, "mark_dispatched", dispatch)

    async def scenario():
        async with client_for(harness) as client:
            await client.on_message(Message())
            assert len(client.message_handler.memory) == 0
        assert not harness.requests
        assert harness.rows()[0][1] == ("dispatched" if ambiguous else "reserved")

    asyncio.run(scenario())
