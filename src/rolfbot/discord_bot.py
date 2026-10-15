"""Discord client and slash commands. SDK imports and clients are explicit and lazy."""

import asyncio
import logging

from rolfbot.ai_service import AIService
from rolfbot.config import Settings, require_discord_settings
from rolfbot.discord_messages import MessageHandler
from rolfbot.openai_client import GenerationResult, Outcome, MAX_OUTPUT_UTF16_UNITS
from rolfbot.usage import Code, valid_discord_id

DEFER_TIMEOUT_SECONDS = 2
DELIVERY_TIMEOUT_SECONDS = 10
SYNC_TIMEOUT_SECONDS = 30
_LOG = logging.getLogger("rolfbot.discord")
_UNAVAILABLE = "Rolfbot is unavailable right now. Please let the owner know."
_DENIALS = {
    Code.INVALID_INPUT: "Please send a nonempty message without invalid characters.",
    Code.INPUT_TOO_LONG: "Please keep your message to 1,000 characters or fewer.",
    Code.COOLDOWN: "Please wait for your 30-second cooldown before asking again.",
    Code.USER_LIMIT: "You've reached today's 20-request allowance. It resets at local midnight.",
    Code.GLOBAL_LIMIT: "Rolfbot has reached today's shared 100-request allowance. It resets at local midnight.",
    Code.DUPLICATE_ATTEMPT: "This interaction has already been handled; it won't generate another answer.",
    Code.STORAGE_UNAVAILABLE: _UNAVAILABLE,
    Code.SERVICE_DISABLED: _UNAVAILABLE,
}


def result_message(result: GenerationResult) -> str:
    if result.outcome is Outcome.SUCCESS and isinstance(result.text, str):
        try:
            if result.text.strip() and len(result.text.encode("utf-16-le")) // 2 <= MAX_OUTPUT_UTF16_UNITS:
                return result.text
        except UnicodeError:
            pass
    if result.denial is not None:
        return _DENIALS.get(result.denial.code, _UNAVAILABLE)
    if result.outcome is Outcome.BUSY:
        return "Rolfbot is answering two requests already. Please try again later."
    if result.outcome is Outcome.INVALID_CONTEXT:
        return "Rolfbot couldn't use that conversation. Please start fresh with a mention."
    if result.outcome in (Outcome.BACKOFF, Outcome.RATE_LIMIT):
        return "Rolfbot is taking a short break after a service error. Please try again later."
    if result.outcome in (Outcome.TIMEOUT, Outcome.CONNECTION, Outcome.SERVER_ERROR):
        return "Rolfbot couldn't finish this request. It may still count toward your allowance."
    if result.outcome in (Outcome.INCOMPLETE, Outcome.EMPTY_RESPONSE, Outcome.INVALID_RESPONSE, Outcome.OUTPUT_TOO_LONG):
        return "Rolfbot couldn't produce a complete, usable answer. This request still counts toward your allowance."
    if result.outcome is Outcome.REFUSED:
        return "Rolfbot couldn't answer that request."
    return _UNAVAILABLE


def _silence_discord_logs() -> None:
    # The SDK logs gateway payloads (including interaction tokens/content) at
    # DEBUG. Keep only our fixed application events, including on SDK errors.
    namespaces = ("discord", "aiohttp")
    names = set(namespaces) | {
        name for name in logging.Logger.manager.loggerDict
        if any(name.startswith(prefix + ".") for prefix in namespaces)
    }
    for name in names:
        logger = logging.getLogger(name)
        logger.disabled = True
        logger.setLevel(logging.CRITICAL + 1)
    for name in namespaces:
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False


class CommandHandler:
    def __init__(self, settings: Settings, service: AIService):
        if not isinstance(service, AIService):
            raise TypeError("Discord commands require the SQLite-protected AIService.")
        self._channels = {entry.guild_id: entry.channel_ids for entry in settings.guild_channels}
        self._service = service
        self._active: set[asyncio.Task] = set()
        self._closing = False

    async def reject(self, interaction, message: str) -> None:
        import discord

        try:
            if not interaction.is_expired() and not interaction.response.is_done():
                async with asyncio.timeout(DEFER_TIMEOUT_SECONDS):
                    await interaction.response.send_message(
                        message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
                    )
        except Exception:
            _LOG.info("event=discord_rejection_delivery_failed")

    async def handle(self, interaction, message: str) -> None:
        import discord

        if self._closing:
            return
        task = asyncio.current_task()
        assert task is not None
        self._active.add(task)
        try:
            # These fields come from Discord's authenticated interaction object,
            # never command arguments or the content of a message.
            identifiers = (interaction.guild_id, interaction.channel_id, interaction.user.id, interaction.id)
            if (
                interaction.type is not discord.InteractionType.application_command
                or any(type(item) is not int or not valid_discord_id(str(item)) for item in identifiers)
                or str(interaction.channel_id) not in self._channels.get(str(interaction.guild_id), ())
                or interaction.user.bot
            ):
                await self.reject(interaction, "Use /rolfbot in a configured server and AI channel.")
                return
            if (
                interaction.channel is None or interaction.channel.type is not discord.ChannelType.text
                or not interaction.app_permissions.view_channel
                or not interaction.app_permissions.send_messages
            ):
                await self.reject(interaction, "Rolfbot needs View Channel and Send Messages in a configured text channel.")
                return
            if interaction.is_expired() or interaction.response.is_done():
                return
            # No storage or model call until Discord confirms acknowledgement.
            # An ambiguous/failed defer never authorizes a paid generation.
            try:
                async with asyncio.timeout(DEFER_TIMEOUT_SECONDS):
                    await interaction.response.defer(thinking=True)
            except Exception:
                _LOG.info("event=discord_defer_failed")
                return
            result = await self._service.generate_user(
                str(interaction.user.id), str(interaction.id), message,
                guild_id=str(interaction.guild_id),
            )
            try:
                async with asyncio.timeout(DELIVERY_TIMEOUT_SECONDS):
                    await interaction.edit_original_response(
                        content=result_message(result), allowed_mentions=discord.AllowedMentions.none(),
                    )
            except Exception:
                # The attempt is already counted. Never regenerate or refund
                # when Discord delivery fails or its acknowledgement is lost.
                _LOG.info("event=discord_answer_delivery_failed")
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOG.error("event=discord_command_failed")
            await self.reject(interaction, _UNAVAILABLE)
        finally:
            self._active.discard(task)

    async def aclose(self) -> None:
        self._closing = True
        current = asyncio.current_task()
        tasks = [task for task in self._active if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def create_client(settings: Settings, service: AIService):
    """Construct one client/command tree without logging in or syncing anything."""
    require_discord_settings(settings)
    import discord
    from discord import app_commands

    _silence_discord_logs()
    handler = CommandHandler(settings, service)
    message_handler = MessageHandler(settings, service)

    class SafeTree(app_commands.CommandTree):
        async def on_error(self, interaction, error):
            _LOG.error("event=discord_command_error")
            await handler.reject(interaction, _UNAVAILABLE)

    class RolfbotClient(discord.Client):
        def __init__(self):
            intents = discord.Intents.none()
            intents.guilds = True
            intents.guild_messages = True
            super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none(), max_messages=None)
            self.tree = SafeTree(self, fallback_to_global=False)
            self.command_handler = handler
            self.message_handler = message_handler
            self._sync_started = False
            self._owned_close_task = None

            @app_commands.command(name="rolfbot", description="Ask Rolfbot a question or share a joke.")
            @app_commands.describe(message="Your message (up to 1,000 characters).")
            @app_commands.guild_only()
            async def rolfbot(interaction: discord.Interaction, message: str):
                await handler.handle(interaction, message)

            for entry in settings.guild_channels:
                self.tree.add_command(rolfbot, guild=discord.Object(id=int(entry.guild_id)))

        async def setup_hook(self):
            if self._sync_started:
                return
            self._sync_started = True
            for entry in settings.guild_channels:
                async with asyncio.timeout(SYNC_TIMEOUT_SECONDS):
                    await self.tree.sync(guild=discord.Object(id=int(entry.guild_id)))
            _LOG.info("event=discord_commands_synced")

        async def on_ready(self):
            _LOG.info("event=discord_ready")

        async def on_message(self, message):
            await message_handler.handle(message, self.user)

        async def on_error(self, event_method, *args, **kwargs):
            _LOG.error("event=discord_event_error")

        async def close(self):
            handler._closing = True  # Stop admission before scheduling cleanup.
            message_handler._closing = True
            if self._owned_close_task is None:
                self._owned_close_task = asyncio.create_task(self._close_owned())
            try:
                await asyncio.shield(self._owned_close_task)
            except asyncio.CancelledError:
                await asyncio.shield(self._owned_close_task)
                raise

        async def _close_owned(self):
            try:
                try:
                    await message_handler.aclose()
                finally:
                    await handler.aclose()
            finally:
                try:
                    await service.aclose()
                finally:
                    await super().close()

    return RolfbotClient()
