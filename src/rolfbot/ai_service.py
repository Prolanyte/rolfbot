"""The only public AI generation interface; every send requires SQLite admission."""

import asyncio
import hashlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from rolfbot import openai_client
from rolfbot.config import APPROVED_MODEL, Settings
from rolfbot.conversation import Context, validate_context
from rolfbot.openai_client import GenerationResult, Outcome, SCHEDULED_PROMPT
from rolfbot.usage import BlockReason, Code, valid_attempt_id, validate_input
from rolfbot.usage_store import UsageStore

REQUEST_DEADLINE_SECONDS = 30
MAX_IN_FLIGHT = 2
_PERMANENT = {Outcome(reason.value): reason for reason in BlockReason}
_TRANSIENT = {
    Outcome.RATE_LIMIT, Outcome.CONNECTION, Outcome.TIMEOUT, Outcome.SERVER_ERROR,
    Outcome.INVALID_RESPONSE, Outcome.INCOMPLETE, Outcome.EMPTY_RESPONSE,
    Outcome.OUTPUT_TOO_LONG, Outcome.INTERNAL_ERROR,
}


class AIService:
    """One service per bot process/event loop, shared by user and scheduled work.

    Construction explicitly owns an AsyncOpenAI client but sends nothing. Tests
    may inject an HTTP transport; injection cannot replace admission or request
    settings. Normal handlers use only generate_user/generate_scheduled. Use an
    async context manager or aclose() at shutdown. Never recover by replaying.
    """

    def __init__(
        self, settings: Settings, store: UsageStore, *, http_transport=None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(store, UsageStore):
            raise TypeError("AIService requires the SQLite usage barrier.")
        self._store = store
        self._transport = openai_client._Transport(settings, http_transport)
        self._clock = clock
        self._monotonic = monotonic
        self._active: set[asyncio.Task] = set()
        self._loop = None
        self._closing = False
        self._closed = False
        self._shutdown_task: asyncio.Task | None = None
        self._disabled: BlockReason | None = None
        self._backoff_until = 0.0
        self._failures = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    async def aclose(self) -> None:
        """Stop admission, cancel active generations, then close client resources."""
        self._closing = True
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._shutdown())
        try:
            await asyncio.shield(self._shutdown_task)
        except asyncio.CancelledError:
            # Keep ownership of cleanup even when the shutdown caller is cancelled.
            await asyncio.shield(self._shutdown_task)
            raise

    async def _shutdown(self) -> None:
        current = asyncio.current_task()
        tasks = [task for task in self._active if task is not current]
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            if not self._closed:
                await self._transport.aclose()
                self._closed = True

    async def generate_user(
        self, user_id: str, attempt_id: str, text: str, *,
        now: datetime | None = None, guild_id: str | None = None,
        context: Context | None = None,
    ) -> GenerationResult:
        return await self._generate(attempt_id, text, user_id, guild_id, now, scheduled=False, context=context)

    async def generate_scheduled(
        self, attempt_id: str, *, now: datetime | None = None,
    ) -> GenerationResult:
        return await self._generate(attempt_id, SCHEDULED_PROMPT, None, None, now, scheduled=True)

    def _unavailable(self) -> GenerationResult | None:
        if self._closing:
            return GenerationResult(Outcome.CLOSED)
        if self._disabled is not None:
            return GenerationResult(Outcome.SERVICE_DISABLED, block_reason=self._disabled)
        remaining = self._backoff_until - self._monotonic()
        if remaining > 0:
            return GenerationResult(Outcome.BACKOFF, retry_after_seconds=math.ceil(remaining))
        return None

    def _observe(self, result: GenerationResult) -> None:
        if result.outcome in _PERMANENT:
            self._disabled = _PERMANENT[result.outcome]
        elif result.outcome in _TRANSIENT:
            self._failures = min(self._failures + 1, 5)
            delay = min(30 * 2 ** (self._failures - 1), 300)
            self._backoff_until = max(self._backoff_until, self._monotonic() + delay)
        elif result.outcome == Outcome.SUCCESS:
            self._failures = 0

    async def _refund(self, attempt_id, now) -> Code:
        try:
            return (await self._store.refund_undispatched(attempt_id, now=now)).code
        except Exception:
            return Code.STORAGE_UNAVAILABLE

    async def _generate(self, attempt_id, text, user_id, guild_id, now, *, scheduled, context=None) -> GenerationResult:
        validation = validate_input(text)
        if validation.code != Code.OK:
            return GenerationResult(Outcome.DENIED, denial=validation)
        try:
            validate_context(context)
        except ValueError:
            return GenerationResult(Outcome.INVALID_CONTEXT)
        unavailable = self._unavailable()
        if unavailable is not None:
            return unavailable
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RuntimeError("AIService must be used on one event loop.")
        self._loop = loop
        task = asyncio.current_task()
        assert task is not None
        # No await between checking capacity and claiming a slot: no queue, and
        # busy/cancelled waiting work cannot spend personal allowance.
        if len(self._active) >= MAX_IN_FLIGHT or task in self._active:
            return GenerationResult(Outcome.BUSY)
        self._active.add(task)
        started = self._monotonic()
        reserved = False
        dispatch_started = False
        result = GenerationResult(Outcome.INTERNAL_ERROR)
        timestamp = now
        try:
            timestamp = self._clock() if now is None else now
            if scheduled:
                admission = await self._store.reserve_scheduled(attempt_id, now=timestamp)
            else:
                admission = await self._store.reserve_user(
                    user_id, attempt_id, text, now=timestamp, guild_id=guild_id,
                )
            if admission.code is not Code.RESERVED:
                result = GenerationResult(Outcome.DENIED, denial=admission)
                return result
            reserved = True
            try:
                payload = openai_client._prepare(text) if context is None else openai_client._prepare(text, context=context)
            except Exception:
                accounting = await self._refund(attempt_id, timestamp)
                result = GenerationResult(Outcome.PREPARATION_FAILED, accounting=accounting)
                return result
            await asyncio.sleep(0)  # Observe cancellation before starting dispatch.
            unavailable = self._unavailable()
            if unavailable is not None:
                result = replace(unavailable, accounting=await self._refund(attempt_id, timestamp))
                return result
            # Set BEFORE awaiting: cancellation/failed acknowledgement is ambiguous.
            dispatch_started = True
            grant = await self._store.mark_dispatched(attempt_id, now=self._clock())
            if grant.code is not Code.DISPATCH_GRANTED:
                result = GenerationResult(Outcome.DENIED, denial=grant)
                return result
            await asyncio.sleep(0)  # A pending cancellation must never send.
            unavailable = self._unavailable()
            if unavailable is not None:
                # Another in-flight request may have just reported a billing
                # failure. This granted attempt stays counted, but is not sent.
                result = unavailable
                return result
            try:
                async with asyncio.timeout(REQUEST_DEADLINE_SECONDS):
                    result = await self._transport._send(payload)
            except TimeoutError:
                result = GenerationResult(Outcome.TIMEOUT)
            self._observe(result)  # Block further admission before awaiting storage.
            try:
                completed = await self._store.complete(
                    attempt_id, now=self._clock(), usage=result.usage,
                    block_reason=_PERMANENT.get(result.outcome),
                )
                accounting = completed.code
            except Exception:
                accounting = Code.STORAGE_UNAVAILABLE
            result = replace(result, accounting=accounting)
            return result
        except asyncio.CancelledError:
            result = replace(result, outcome=Outcome.CANCELLED, text=None)
            if reserved and not dispatch_started:
                # This handler has never initiated the dispatch transition.
                # Failed/interrupted cleanup simply leaves the capacity consumed.
                await self._refund(attempt_id, timestamp)
            raise
        except Exception:
            accounting = None
            if reserved and not dispatch_started:
                accounting = await self._refund(attempt_id, timestamp)
            result = GenerationResult(Outcome.INTERNAL_ERROR, accounting=accounting)
            self._observe(result)
            return result
        finally:
            self._active.discard(task)
            correlation = hashlib.sha256(attempt_id.encode("ascii")).hexdigest()[:16] if valid_attempt_id(attempt_id) else "invalid"
            logging.getLogger("rolfbot.ai").info(
                "event=generation correlation=%s outcome=%s model=%s duration_ms=%d "
                "input_tokens=%s output_tokens=%s http_status=%s accounting=%s",
                correlation, result.outcome.value, APPROVED_MODEL,
                max(0, int((self._monotonic() - started) * 1000)),
                result.usage.input_tokens, result.usage.output_tokens,
                result.http_status, result.accounting,
            )
