"""Explicit database, smoke-test and Discord entry points; nothing runs on import."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

from rolfbot.ai_service import AIService
from rolfbot.config import ConfigurationError, Settings, require_discord_settings
from rolfbot.openai_client import Outcome
from rolfbot.usage import Code
from rolfbot.usage_store import UsageStore

SMOKE_USER_ID = "1"  # Identifiable synthetic account; normal personal/global quotas.
SMOKE_PROMPT = "Say one short, friendly hello for the Rolfbot smoke test."


class StartupError(RuntimeError):
    """Safe startup diagnostic; never wraps or prints an underlying exception."""


def _persistent_path(settings: Settings) -> Path:
    if settings.database_path is None:
        raise ConfigurationError("ROLFBOT_DATABASE_PATH is required for this command.")
    path = settings.database_path.resolve()
    # Resolve symlinks before comparing. Cover source checkouts, working release
    # trees and installed virtual environments without depending on the cwd.
    roots = {Path(sys.prefix).resolve(), Path(__file__).resolve().parent}
    for location in (Path(__file__).resolve().parent, Path.cwd()):
        for parent in (location, *location.parents):
            if (parent / ".git").exists() or (parent / "pyproject.toml").is_file():
                roots.add(parent.resolve())
    if any(path.is_relative_to(root) for root in roots):
        raise ConfigurationError(
            "ROLFBOT_DATABASE_PATH must be outside the checkout, release and Python environment."
        )
    return path


async def open_store(settings: Settings, *, provision: bool = False) -> UsageStore:
    path = await asyncio.to_thread(_persistent_path, settings)
    store = UsageStore(path, timezone=settings.timezone)
    result = await (store.initialize() if provision else store.validate())
    if result.code is not Code.OK:
        raise StartupError(
            "Usage database is unavailable, corrupt or incompatible. Check the configured "
            "path, permissions, schema and timezone; never replace existing accounting. "
            "Use init-db only for deliberate first-time provisioning."
        )
    if not provision and result.block_reason is not None:
        raise StartupError(
            "OpenAI service is persistently blocked. Resolve the provider/configuration "
            "cause and follow the documented operator recovery procedure."
        )
    return store


async def smoke_test(settings: Settings, store: UsageStore) -> int:
    """CLI calls this only after --confirm-billable and existing-storage checks."""
    async with AIService(settings, store) as service:
        result = await service.generate_user(
            SMOKE_USER_ID, "smoke-" + uuid4().hex, SMOKE_PROMPT,
        )
    text = result.text
    if text is not None:
        for secret in (settings.openai_api_key, settings.discord_bot_token):
            if secret:
                text = text.replace(secret, "[REDACTED]")
    # JSON escapes terminal controls and marks unknown counts explicitly as null.
    print(json.dumps({
        "outcome": result.outcome.value,
        "denial": result.denial.code.value if result.denial else None,
        "accounting": result.accounting.value if result.accounting else None,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "text": text,
    }, ensure_ascii=True))
    return 0 if result.outcome is Outcome.SUCCESS and result.accounting is Code.COMPLETED else 4


async def run_discord(settings: Settings, store: UsageStore) -> int:
    from rolfbot.discord_bot import create_client

    token = require_discord_settings(settings)
    async with AIService(settings, store) as service:
        client = create_client(settings, service)
        async with client:
            try:
                await client.start(token, reconnect=True)
            except Exception:
                raise StartupError(
                    "Discord could not start or maintain its connection. Check the bot "
                    "token, guild installation, command permissions and network access."
                ) from None
    return 0
