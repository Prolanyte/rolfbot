"""Explicitly addressed guild messages, using the shared quota-protected service."""

import asyncio
import logging
import re

from rolfbot.ai_service import AIService, MAX_IN_FLIGHT
from rolfbot.config import Settings
from rolfbot.conversation import ConversationMemory, prepare_turn
from rolfbot.openai_client import GenerationResult, Outcome
from rolfbot.usage import Code, valid_discord_id, validate_input

DELIVERY_TIMEOUT_SECONDS = 10
TYPING_TIMEOUT_SECONDS = 2
TYPING_INTERVAL_SECONDS = 5
_LOG = logging.getLogger("rolfbot.discord")


def _valid_id(value) -> bool:
    return type(value) is int and valid_discord_id(str(value))


class MessageHandler:
    def __init__(self, settings: Settings, service: AIService):
        if not isinstance(service, AIService):
            raise TypeError("Discord messages require the SQLite-protected AIService.")
        self._channels = {entry.guild_id: entry.channel_ids for entry in settings.guild_channels}
        self._service = service
        self.memory = ConversationMemory()
        self._active: dict[int, asyncio.Task] = {}
        self._parents: set[tuple[int, int, int]] = set()
        self._closing = False

    async def _reply(self, message, text):
        import discord

        try:
            async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):
                return await message.reply(
                    text, mention_author=False, allowed_mentions=discord.AllowedMentions.none(),
                    suppress_embeds=True,
                )
        except Exception:
            # An ambiguous acknowledgement never authorizes another generation,
            # retry, refund, or history insertion.
            _LOG.info("event=discord_answer_delivery_failed")
            return None

    async def _typing(self, channel):
        try:
            while True:
                # Awaiting typing() sends one indicator. Own this loop rather
                # than the SDK context manager's unjoined background task.
                async with asyncio.timeout(TYPING_TIMEOUT_SECONDS):
                    await channel.typing()
                await asyncio.sleep(TYPING_INTERVAL_SECONDS)
        except Exception:
            _LOG.info("event=discord_typing_failed")

    async def handle(self, message, bot_user) -> None:
        import discord
        from rolfbot.discord_bot import result_message

        if self._closing or bot_user is None or not _valid_id(bot_user.id):
            return
        guild, channel, author = message.guild, message.channel, message.author
        if (
            guild is None or channel is None or author.bot or author.id == bot_user.id
            or message.webhook_id is not None
            or any(not _valid_id(value) for value in (guild.id, channel.id, author.id, message.id))
            or str(channel.id) not in self._channels.get(str(guild.id), ())
            or channel.type is not discord.ChannelType.text
            or message.type not in (discord.MessageType.default, discord.MessageType.reply)
            or guild.me is None
        ):
            return
        permissions = channel.permissions_for(guild.me)
        # Discord requires Read Message History to SEND a reply. This adapter
        # never reads/fetches history, attachments, embeds or resolved references.
        if not (permissions.view_channel and permissions.send_messages and permissions.read_message_history):
            return
        content = message.content
        if type(content) is not str or not content:
            return  # Discord may omit reply text without a fresh explicit mention.
        mention = re.compile(rf"<@!?{bot_user.id}>")
        explicit = bool(mention.search(content)) and any(user.id == bot_user.id for user in message.mentions)
        parent = None
        reference = message.reference
        if (
            message.type is discord.MessageType.reply and reference is not None
            and reference.type is discord.MessageReferenceType.default
            and reference.guild_id == guild.id and reference.channel_id == channel.id
            and _valid_id(reference.message_id)
        ):
            parent = (guild.id, channel.id, reference.message_id)
        snapshot = self.memory.lookup(parent) if parent is not None else None
        if not explicit and snapshot is None:
            return
        if snapshot is None:
            parent = None  # An explicit mention with forgotten context starts fresh.
        # Bound the entire message lifecycle, including delivery, not just AI.
        # Excess events and active duplicates are ignored; there is no queue.
        if message.id in self._active or len(self._active) >= MAX_IN_FLIGHT:
            return
        task = asyncio.current_task()
        assert task is not None
        self._active[message.id] = task
        claimed_parent = False
        typing = None
        try:
            text = mention.sub("", content).strip()
            validation = validate_input(text)
            if validation.code is not Code.OK:
                await self._reply(message, result_message(GenerationResult(Outcome.DENIED, denial=validation)))
                return
            if parent is not None:
                if parent in self._parents:
                    await self._reply(message, "Rolfbot is answering a reply to that message. Please try again shortly.")
                    return
                self._parents.add(parent)
                claimed_parent = True
                self.memory.touch(parent)
            turn = prepare_turn(snapshot, str(author.id))
            typing = asyncio.create_task(self._typing(channel))
            result = await self._service.generate_user(
                str(author.id), f"message:{message.id}", text, guild_id=str(guild.id), context=turn.context,
            )
            if result.denial is not None and result.denial.code is Code.DUPLICATE_ATTEMPT:
                return
            sent = await self._reply(message, result_message(result))
            if result.outcome is Outcome.SUCCESS and sent is not None and _valid_id(sent.id):
                self.memory.remember((guild.id, channel.id, sent.id), turn, text, result.text)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never expose content-bearing Discord/SDK objects or exceptions.
            _LOG.error("event=discord_message_failed")
        finally:
            try:
                if typing is not None:
                    typing.cancel()
                    await asyncio.gather(typing, return_exceptions=True)
            finally:
                if claimed_parent:
                    self._parents.discard(parent)
                self._active.pop(message.id, None)

    async def aclose(self) -> None:
        self._closing = True
        tasks = [task for task in self._active.values() if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self.memory.close()
            self._parents.clear()
