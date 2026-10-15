import asyncio
import io
import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from rolfbot import ai_service, openai_client
from rolfbot.ai_service import AIService
from rolfbot.config import ConfigurationError, load_settings
from rolfbot.logging_setup import configure_logging
from rolfbot.openai_client import Outcome, PERSONALITY, SCHEDULED_PROMPT
from rolfbot.usage import BlockReason, Code, Result, TokenUsage
from rolfbot.usage_store import UsageStore

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)
SYNTHETIC_KEY = "synthetic-offline-credential"


def response(text="Hei!", **changes):
    return {
        "id": "resp_synthetic", "object": "response", "created_at": NOW.timestamp(),
        "model": "gpt-6-luna", "status": "completed", "error": None,
        "output": [{"id": "msg_synthetic", "type": "message", "role": "assistant",
                    "status": "completed", "content": [
                        {"type": "output_text", "text": text, "annotations": []},
                    ]}],
        "usage": {"input_tokens": 42, "output_tokens": 9, "total_tokens": 51,
                  "input_tokens_details": {"cached_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}},
        **changes,
    }


class Clock:
    def __init__(self):
        self.now = NOW
        self.elapsed = 0.0

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)
        self.elapsed += seconds


class Harness:
    def __init__(self, path):
        self.store = UsageStore(path)
        assert asyncio.run(self.store.initialize()).code == Code.OK
        self.settings = load_settings({"OPENAI_API_KEY": SYNTHETIC_KEY})
        self.clock = Clock()
        self.requests = []
        self.claimed = set()
        self.handler = None

    def service(self):
        return AIService(
            self.settings, self.store, http_transport=httpx2.MockTransport(self.handle),
            clock=lambda: self.clock.now, monotonic=lambda: self.clock.elapsed,
        )

    async def handle(self, request):
        self.requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://api.openai.com/v1/responses"
        # Every SDK invocation in these tests must own a fresh committed dispatch.
        # Acquiring a writer also proves no storage transaction spans API work.
        # Another admitted request may briefly be writing on its worker thread.
        with closing(sqlite3.connect(self.store.path, timeout=0.25, isolation_level=None)) as db:
            db.execute("BEGIN IMMEDIATE")
            available = {row[0] for row in db.execute("SELECT attempt_id FROM attempts WHERE state IN ('dispatched', 'completed')")} - self.claimed
            assert available, "HTTP call without a fresh SQLite dispatch grant"
            self.claimed.add(sorted(available)[0])
            db.execute("ROLLBACK")
        if self.handler is not None:
            return await self.handler(request)
        return httpx2.Response(200, json=response())

    def rows(self):
        with closing(sqlite3.connect(self.store.path)) as db:
            return db.execute("SELECT attempt_id, state, input_tokens, output_tokens FROM attempts ORDER BY attempt_id").fetchall()


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "usage.sqlite3")


def test_exact_request_contract_one_send_usage_persistence_and_cleanup(harness):
    async def scenario():
        async with harness.service() as service:
            assert not harness.requests
            result = await service.generate_user("1", "interaction", "private user prompt", guild_id="10")
            assert result.outcome == Outcome.SUCCESS and result.text == "Hei!"
            assert result.accounting == Code.COMPLETED
            assert result.usage == TokenUsage(42, 9)
            assert "Hei!" not in repr(result)
            assert service._transport._sdk.max_retries == 0
            assert not service._transport._sdk._client.follow_redirects
            assert not service._transport._sdk._client.trust_env
        assert service._transport._sdk.is_closed()
        assert (await service.generate_scheduled("closed")).outcome == Outcome.CLOSED
        await service.aclose()  # Idempotent.

    asyncio.run(scenario())
    assert len(harness.requests) == 1
    request = harness.requests[0]
    assert request.extensions["timeout"] == {"connect": 5, "read": 25, "write": 5, "pool": 1}
    assert json.loads(request.content) == {
        "model": "gpt-6-luna", "reasoning": {"effort": "none"},
        "instructions": PERSONALITY, "input": [{"role": "user", "content": "private user prompt"}],
        "max_output_tokens": 512, "store": False, "stream": False, "background": False,
        "tools": [], "tool_choice": "none", "text": {"format": {"type": "text"}},
        "truncation": "disabled",
    }
    assert len(PERSONALITY) <= 1000
    assert harness.rows() == [("interaction", "completed", 42, 9)]


@pytest.mark.parametrize("kind", ["cooldown", "user", "global", "duplicate", "storage", "invalid_user"])
def test_admission_denials_never_invoke_openai(harness, kind):
    async def scenario():
        when = harness.clock.now
        expected = {
            "cooldown": Code.COOLDOWN, "user": Code.USER_LIMIT, "global": Code.GLOBAL_LIMIT,
            "duplicate": Code.DUPLICATE_ATTEMPT, "storage": Code.STORAGE_UNAVAILABLE,
            "invalid_user": Code.INVALID_IDENTIFIER,
        }[kind]
        if kind in ("cooldown", "duplicate"):
            await harness.store.reserve_user("1", "old", "hello", now=when)
        elif kind == "user":
            for index in range(20):
                await harness.store.reserve_user("1", f"old-{index}", "hello", now=when - timedelta(seconds=30 * (20 - index)))
        elif kind == "global":
            for index in range(100):
                await harness.store.reserve_scheduled(f"old-{index}", now=when)
        elif kind == "storage":
            harness.store.path.unlink()
        async with harness.service() as service:
            result = await service.generate_user(None if kind == "invalid_user" else "1", "old" if kind == "duplicate" else "new", "hello")
            assert result.outcome == Outcome.DENIED and result.denial.code == expected
        assert harness.requests == []

    asyncio.run(scenario())


def test_scheduled_and_user_requests_share_global_allowance(harness):
    async def scenario():
        for index in range(98):
            await harness.store.reserve_scheduled(f"old-{index}", now=NOW)
        async with harness.service() as service:
            assert (await service.generate_scheduled("job")).outcome == Outcome.SUCCESS
            assert (await service.generate_user("1", "user", "hello")).outcome == Outcome.SUCCESS
            assert (await service.generate_scheduled("exhausted")).denial.code == Code.GLOBAL_LIMIT
            assert (await service.generate_scheduled("job")).denial.code == Code.DUPLICATE_ATTEMPT
        status = (await harness.store.status(now=NOW, user_id="1")).quota
        assert status.global_used == 100 and status.user_used == 1
        assert json.loads(harness.requests[0].content)["input"] == [{"role": "user", "content": SCHEDULED_PROMPT}]
        assert len(harness.requests) == 2

    asyncio.run(scenario())


def test_duplicate_calls_and_restart_never_replay(harness):
    async def scenario():
        async with harness.service() as service:
            results = await asyncio.gather(
                service.generate_user("1", "duplicate", "hello"),
                service.generate_user("1", "duplicate", "hello"),
            )
            assert {item.outcome for item in results} == {Outcome.SUCCESS, Outcome.DENIED}
            assert next(item for item in results if item.denial).denial.code == Code.DUPLICATE_ATTEMPT
        harness.clock.advance(31)
        await harness.store.reserve_scheduled("unfinished", now=harness.clock.now)
        await harness.store.mark_dispatched("unfinished", now=harness.clock.now)
        async with harness.service() as restarted:
            assert (await restarted.generate_scheduled("unfinished")).denial.code == Code.DUPLICATE_ATTEMPT
            assert (await restarted.generate_user("1", "duplicate", "hello")).denial.code == Code.DUPLICATE_ATTEMPT
        assert len(harness.requests) == 1

    asyncio.run(scenario())


def test_two_slots_no_queue_no_quota_spent_on_busy_requests(harness):
    async def scenario():
        both_entered = asyncio.Event()
        release = asyncio.Event()

        async def wait(request):
            if len(harness.requests) == 2:
                both_entered.set()
            await release.wait()
            return httpx2.Response(200, json=response())

        harness.handler = wait
        async with harness.service() as service:
            first = asyncio.create_task(service.generate_user("1", "one", "hello"))
            second = asyncio.create_task(service.generate_scheduled("two"))
            await asyncio.wait_for(both_entered.wait(), 3)
            third = await service.generate_user("3", "three", "hello")
            assert third.outcome == Outcome.BUSY
            status = (await harness.store.status(now=NOW, user_id="3")).quota
            assert status.global_used == 2 and status.user_used == 0 and status.cooldown_until is None
            release.set()
            assert all(item.outcome == Outcome.SUCCESS for item in await asyncio.gather(first, second))

    asyncio.run(scenario())


@pytest.mark.parametrize("grant", [Code.STORAGE_UNAVAILABLE, Code.DUPLICATE_ATTEMPT, Code.OK, "dispatch_granted"])
def test_only_exact_dispatch_enum_grants_permission(harness, monkeypatch, grant):
    async def ambiguous(self, attempt_id, *, now):
        # The real commit happened; acknowledgement was replaced/lost.
        assert (await original(self, attempt_id, now=now)).code == Code.DISPATCH_GRANTED
        return Result(grant)

    original = UsageStore.mark_dispatched
    monkeypatch.setattr(UsageStore, "mark_dispatched", ambiguous)

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("ambiguous")
            assert result.outcome == Outcome.DENIED
        assert harness.requests == []
        assert harness.rows() == [("ambiguous", "dispatched", None, None)]

    asyncio.run(scenario())


def test_local_preparation_failure_refunds_without_erasing_cooldown(harness, monkeypatch):
    def fail(text):
        raise RuntimeError("private prompt and secret exception")

    monkeypatch.setattr(openai_client, "_prepare", fail)

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_user("1", "failed", "hello")
            assert result.outcome == Outcome.PREPARATION_FAILED and result.accounting == Code.REFUNDED
            assert (await service.generate_user("1", "next", "hello")).denial.code == Code.COOLDOWN
        assert harness.requests == []
        assert harness.rows() == [("failed", "refunded", None, None)]

    asyncio.run(scenario())


@pytest.mark.parametrize("point", ["reservation", "preparation", "dispatch", "network", "completion"])
def test_cancellation_at_every_lifecycle_boundary(harness, monkeypatch, point):
    entered = asyncio.Event()
    original_reserve = UsageStore.reserve_user
    original_dispatch = UsageStore.mark_dispatched
    original_complete = UsageStore.complete
    original_prepare = openai_client._prepare

    async def lose_reservation(self, *args, **kwargs):
        await original_reserve(self, *args, **kwargs)
        asyncio.current_task().cancel()
        await asyncio.sleep(0)

    def cancel_preparation(text):
        asyncio.current_task().cancel()
        return original_prepare(text)

    async def lose_dispatch(self, *args, **kwargs):
        result = await original_dispatch(self, *args, **kwargs)
        asyncio.current_task().cancel()
        return result

    async def lose_completion(self, *args, **kwargs):
        await original_complete(self, *args, **kwargs)
        asyncio.current_task().cancel()
        await asyncio.sleep(0)

    async def wait(request):
        entered.set()
        await asyncio.Event().wait()

    if point == "reservation":
        monkeypatch.setattr(UsageStore, "reserve_user", lose_reservation)
    elif point == "preparation":
        monkeypatch.setattr(openai_client, "_prepare", cancel_preparation)
    elif point == "dispatch":
        monkeypatch.setattr(UsageStore, "mark_dispatched", lose_dispatch)
    elif point == "network":
        harness.handler = wait
    else:
        monkeypatch.setattr(UsageStore, "complete", lose_completion)

    async def scenario():
        async with harness.service() as service:
            task = asyncio.create_task(service.generate_user("1", "cancelled", "hello"))
            if point == "network":
                await asyncio.wait_for(entered.wait(), 3)
                task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not service._active
        state = {"reservation": "reserved", "preparation": "refunded", "dispatch": "dispatched", "network": "dispatched", "completion": "completed"}[point]
        assert harness.rows()[0][1] == state
        assert len(harness.requests) == (1 if point in ("network", "completion") else 0)

    asyncio.run(scenario())


def test_shutdown_cancels_inflight_request_and_closes_resources(harness):
    async def scenario():
        entered = asyncio.Event()

        async def wait(request):
            entered.set()
            await asyncio.Event().wait()

        harness.handler = wait
        service = harness.service()
        task = asyncio.create_task(service.generate_scheduled("shutdown"))
        await asyncio.wait_for(entered.wait(), 3)
        await service.aclose()
        assert task.cancelled() and service._transport._sdk.is_closed()
        assert harness.rows() == [("shutdown", "dispatched", None, None)]

    asyncio.run(scenario())


def test_completion_write_failure_never_repeats_or_refunds_generation(harness, monkeypatch):
    async def failed_complete(self, *args, **kwargs):
        return Result(Code.STORAGE_UNAVAILABLE)

    monkeypatch.setattr(UsageStore, "complete", failed_complete)

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("completed-remotely")
            assert result.outcome == Outcome.SUCCESS
            assert result.accounting == Code.STORAGE_UNAVAILABLE
            assert (await service.generate_scheduled("completed-remotely")).denial.code == Code.DUPLICATE_ATTEMPT
        assert len(harness.requests) == 1
        assert harness.rows() == [("completed-remotely", "dispatched", None, None)]

    asyncio.run(scenario())


@pytest.mark.parametrize("text,code", [("😀" * 1000, None), ("😀" * 1001, Code.INPUT_TOO_LONG), ("", Code.INVALID_INPUT), (" \n", Code.INVALID_INPUT), ("\ud800", Code.INVALID_INPUT), ("a\x00", Code.INVALID_INPUT)])
def test_input_policy_is_reused_before_admission(harness, text, code):
    async def scenario():
        async with harness.service() as service:
            result = await service.generate_user("1", "input", text)
            if code:
                assert result.denial.code == code
                assert harness.requests == [] and harness.rows() == []
            else:
                assert result.outcome == Outcome.SUCCESS and len(harness.requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("status,error_code,expected,block", [
    (401, "invalid_api_key", Outcome.AUTHENTICATION, BlockReason.AUTHENTICATION),
    (403, "permission_denied", Outcome.PERMISSION, BlockReason.PERMISSION),
    (404, "model_not_found", Outcome.PERMISSION, BlockReason.PERMISSION),
    (400, "invalid_request_error", Outcome.CONFIGURATION, BlockReason.CONFIGURATION),
    (422, None, Outcome.CONFIGURATION, BlockReason.CONFIGURATION),
    (429, "rate_limit_exceeded", Outcome.RATE_LIMIT, None),
    (429, "project_spend_limit_exceeded", Outcome.PROJECT_LIMIT, BlockReason.PROJECT_LIMIT),
    (429, "organization_spend_limit_exceeded", Outcome.ORGANIZATION_LIMIT, BlockReason.ORGANIZATION_LIMIT),
    (429, "credit_balance_exhausted", Outcome.CREDIT_EXHAUSTED, BlockReason.CREDIT_EXHAUSTED),
    (429, "insufficient_quota", Outcome.BILLING_LIMIT, BlockReason.BILLING_LIMIT),
    (429, "organization_usage_limit_exceeded", Outcome.BILLING_LIMIT, BlockReason.BILLING_LIMIT),
    (500, None, Outcome.SERVER_ERROR, None),
    (503, None, Outcome.SERVER_ERROR, None),
    (408, None, Outcome.TIMEOUT, None),
    (409, None, Outcome.SERVER_ERROR, None),
    (504, None, Outcome.TIMEOUT, None),
])
def test_provider_errors_no_sdk_retries_and_service_blocks(harness, status, error_code, expected, block):
    async def fail(request):
        return httpx2.Response(status, json={"error": {
            "message": "private prompt " + SYNTHETIC_KEY, "type": "invalid_request_error",
            "code": error_code, "param": None,
        }}, headers={"x-should-retry": "true", "retry-after": "0"})

    harness.handler = fail

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("failed")
            assert result.outcome == expected and result.http_status == status
            assert result.accounting == Code.COMPLETED
            assert result.usage == TokenUsage()
            assert SYNTHETIC_KEY not in repr(result) and "private prompt" not in repr(result)
            for index in range(3):
                paused = await service.generate_scheduled(f"paused-{index}")
                assert paused.outcome == (Outcome.SERVICE_DISABLED if block else Outcome.BACKOFF)
            assert len(harness.requests) == 1
        assert harness.rows() == [("failed", "completed", None, None)]
        if block:
            async with harness.service() as restarted:
                result = await restarted.generate_scheduled("after-restart")
                assert result.denial.code == Code.SERVICE_DISABLED
                assert result.denial.block_reason == block
            assert len(harness.requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["connection", "read_timeout", "connect_timeout", "sdk_unexpected"])
def test_transport_failures_retain_capacity_with_no_retry(harness, kind):
    async def fail(request):
        if kind == "connection":
            raise httpx2.ConnectError("private detail", request=request)
        if kind == "read_timeout":
            raise httpx2.ReadTimeout("private detail", request=request)
        if kind == "connect_timeout":
            raise httpx2.ConnectTimeout("private detail", request=request)
        raise RuntimeError("private detail")

    harness.handler = fail

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_user("1", "failure", "hello")
            expected = Outcome.TIMEOUT if "timeout" in kind else Outcome.CONNECTION if kind == "connection" else Outcome.INVALID_RESPONSE
            assert result.outcome == expected
            assert result.accounting == Code.COMPLETED
        assert len(harness.requests) == 1
        assert harness.rows() == [("failure", "completed", None, None)]

    asyncio.run(scenario())


def test_overall_deadline_retains_dispatch_without_waiting_thirty_seconds(harness, monkeypatch):
    assert ai_service.REQUEST_DEADLINE_SECONDS == 30
    monkeypatch.setattr(ai_service, "REQUEST_DEADLINE_SECONDS", 0)

    async def wait(request):
        await asyncio.Event().wait()

    harness.handler = wait

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("deadline")
            assert result.outcome == Outcome.TIMEOUT
            assert result.accounting == Code.COMPLETED
        assert len(harness.requests) <= 1
        assert harness.rows() == [("deadline", "completed", None, None)]

    asyncio.run(scenario())


def test_transient_backoff_is_bounded_monotonic_and_never_retries_itself(harness):
    async def fail(request):
        return httpx2.Response(503, json={"error": {"code": "server_error", "message": "private"}})

    harness.handler = fail

    async def scenario():
        async with harness.service() as service:
            for index, delay in enumerate([30, 60, 120, 240, 300, 300]):
                assert (await service.generate_scheduled(f"attempt-{index}")).outcome == Outcome.SERVER_ERROR
                assert len(harness.requests) == index + 1
                # Wall time jumps forward; only elapsed monotonic time unblocks.
                harness.clock.now += timedelta(hours=1)
                denied = await service.generate_scheduled(f"paused-{index}")
                assert denied.outcome == Outcome.BACKOFF and denied.retry_after_seconds == delay
                harness.clock.advance(delay)
            harness.handler = None
            assert (await service.generate_scheduled("recovered")).outcome == Outcome.SUCCESS
        assert len(harness.requests) == 7

    asyncio.run(scenario())


def test_billing_recovery_is_explicit_and_preserves_accounting(harness):
    async def fail(request):
        return httpx2.Response(429, json={"error": {"code": "credit_balance_exhausted", "message": "private"}})

    harness.handler = fail

    async def scenario():
        async with harness.service() as service:
            assert (await service.generate_scheduled("billing")).outcome == Outcome.CREDIT_EXHAUSTED
            assert (await harness.store.clear_ai_block()).code == Code.OK
            # Clearing disk state alone cannot bypass the active process latch.
            assert (await service.generate_scheduled("still-paused")).outcome == Outcome.SERVICE_DISABLED
        harness.handler = None
        async with harness.service() as restarted:
            assert (await restarted.generate_scheduled("recovered")).outcome == Outcome.SUCCESS
        assert (await harness.store.status(now=NOW)).quota.global_used == 2
        assert len(harness.requests) == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("payload,expected", [
    (response(""), Outcome.EMPTY_RESPONSE),
    (response(" \n\t"), Outcome.EMPTY_RESPONSE),
    (response("\u200b"), Outcome.EMPTY_RESPONSE),
    (response(output=[]), Outcome.EMPTY_RESPONSE),
    (response(output=None), Outcome.INVALID_RESPONSE),
    (response(status="incomplete", incomplete_details={"reason": "max_output_tokens"}), Outcome.INCOMPLETE),
    (response(status="failed", error={"code": "server_error", "message": "private"}), Outcome.SERVER_ERROR),
    (response(status="queued"), Outcome.INVALID_RESPONSE),
    ({"wrong": "shape"}, Outcome.INVALID_RESPONSE),
    (response("a" * 1801), Outcome.OUTPUT_TOO_LONG),
    (response("😀" * 901), Outcome.OUTPUT_TOO_LONG),
    (response("private\x00text"), Outcome.INVALID_RESPONSE),
    (response("private\x1b[31m"), Outcome.INVALID_RESPONSE),
    (response("private\u202eoverride"), Outcome.INVALID_RESPONSE),
    (response("private\ud800"), Outcome.INVALID_RESPONSE),
    (response(output=[{"type": "function_call", "name": "tool", "call_id": "call", "arguments": "{}"}]), Outcome.INVALID_RESPONSE),
    (response(output=[{"id": "msg", "type": "message", "role": "assistant", "status": "completed", "content": [{"type": "refusal", "refusal": "private"}]}]), Outcome.REFUSED),
])
def test_bad_outputs_never_continue_or_release_capacity(harness, payload, expected):
    async def reply(request):
        return httpx2.Response(200, content=json.dumps(payload).encode(), headers={"content-type": "application/json"})

    harness.handler = reply

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("response")
            assert result.outcome == expected and result.text is None
            assert result.accounting == Code.COMPLETED
        assert len(harness.requests) == 1
        assert harness.rows()[0][1] == "completed"

    asyncio.run(scenario())


@pytest.mark.parametrize("text,expected", [("a" * 1800, "a" * 1800), ("😀" * 900, "😀" * 900), ("  Hei!\r\n👩‍💻\tÅ  ", "Hei!\n👩‍💻\tÅ")])
def test_valid_output_is_clean_and_bounded(harness, text, expected):
    async def reply(request):
        return httpx2.Response(200, json=response(text))

    harness.handler = reply

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("unicode")
            assert result.outcome == Outcome.SUCCESS and result.text == expected
            assert len(result.text.encode("utf-16-le")) // 2 <= 1800

    asyncio.run(scenario())


@pytest.mark.parametrize("usage,expected", [
    (None, TokenUsage()), ({}, TokenUsage()),
    ({"input_tokens": 0, "output_tokens": 0}, TokenUsage(0, 0)),
    ({"input_tokens": 12}, TokenUsage(12, None)),
    ({"input_tokens": -1, "output_tokens": 9}, TokenUsage(None, 9)),
    ({"input_tokens": True, "output_tokens": "bad"}, TokenUsage()),
    ({**response()["usage"], "input_tokens": True}, TokenUsage(None, 9)),
    ({**response()["usage"], "input_tokens": "12"}, TokenUsage(None, 9)),
    ({**response()["usage"], "input_tokens": 1.0}, TokenUsage(None, 9)),
    ({"input_tokens": 1.5, "output_tokens": 2**63}, TokenUsage()),
])
def test_unknown_or_invalid_usage_is_not_fabricated(harness, usage, expected):
    async def reply(request):
        return httpx2.Response(200, json=response(usage=usage))

    harness.handler = reply

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("tokens")
            assert result.outcome == Outcome.SUCCESS and result.usage == expected
        assert harness.rows() == [("tokens", "completed", expected.input_tokens, expected.output_tokens)]

    asyncio.run(scenario())


@pytest.mark.parametrize("settings", [
    load_settings({}), load_settings({"OPENAI_API_KEY": "replace_with_your_key"}),
    load_settings({"OPENAI_API_KEY": "line\nbreak"}),
    replace(load_settings({"OPENAI_API_KEY": SYNTHETIC_KEY}), openai_model="unapproved"),
    replace(load_settings({"OPENAI_API_KEY": SYNTHETIC_KEY}), openai_reasoning_effort="high"),
])
def test_explicit_client_initialization_revalidates_settings(harness, settings):
    with pytest.raises(ConfigurationError) as caught:
        AIService(settings, harness.store, http_transport=httpx2.MockTransport(harness.handle))
    assert SYNTHETIC_KEY not in str(caught.value)
    assert harness.requests == []


@pytest.mark.parametrize("variable", ["OPENAI_CUSTOM_HEADERS", "OPENAI_LOG"])
def test_unsafe_ambient_sdk_overrides_are_rejected(harness, monkeypatch, variable):
    monkeypatch.setenv(variable, "private value")
    with pytest.raises(ConfigurationError) as caught:
        harness.service()
    assert "private value" not in str(caught.value)
    assert harness.requests == []


def test_ambient_endpoint_credentials_and_proxy_never_override_explicit_policy(harness, monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "OPENAI_BASE_URL", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "private-ambient-value")

    async def scenario():
        async with harness.service() as service:
            assert (await service.generate_scheduled("explicit")).outcome == Outcome.SUCCESS
        headers = harness.requests[0].headers
        assert headers["authorization"] == "Bearer " + SYNTHETIC_KEY
        assert "openai-organization" not in headers
        assert "openai-project" not in headers
        assert "private-ambient-value" not in str(headers)

    asyncio.run(scenario())


def test_logs_contain_only_safe_fields_even_with_debug_transport_logging(harness):
    output = io.StringIO()
    raw = io.StringIO()
    logger = configure_logging("DEBUG", secrets=[SYNTHETIC_KEY], stream=output)
    sdk_logger = logging.getLogger("openai._base_client")
    old_handlers = sdk_logger.handlers[:]
    sdk_logger.handlers = [logging.StreamHandler(raw)]
    sdk_logger.setLevel(logging.DEBUG)
    sdk_logger.disabled = False

    async def reply(request):
        return httpx2.Response(200, json=response("private response body"), headers={"x-request-id": SYNTHETIC_KEY})

    harness.handler = reply

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_user("123456789123456789", "private-attempt", "private prompt body")
            assert result.outcome == Outcome.SUCCESS

    try:
        asyncio.run(scenario())
        logs = output.getvalue() + raw.getvalue()
        for private in (SYNTHETIC_KEY, "private response body", "private prompt body", "123456789123456789", "private-attempt"):
            assert private not in logs
        for field in ("correlation=", "outcome=success", "model=gpt-6-luna", "duration_ms=", "input_tokens=42", "output_tokens=9", "accounting=completed"):
            assert field in logs
        assert raw.getvalue() == ""
    finally:
        sdk_logger.handlers = old_handlers
        # Avoid leaving a handler pointing at a closed test stream.
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()


def test_shutdown_caller_cancellation_still_closes_client(harness, monkeypatch):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        service = harness.service()
        original = service._transport.aclose

        async def delayed_close():
            entered.set()
            await release.wait()
            await original()

        monkeypatch.setattr(service._transport, "aclose", delayed_close)
        task = asyncio.create_task(service.aclose())
        await entered.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service._transport._sdk.is_closed()
        await service.aclose()

    asyncio.run(scenario())


def test_billing_failure_detected_after_grant_prevents_another_send(harness, monkeypatch):
    original = UsageStore.mark_dispatched

    async def scenario():
        async with harness.service() as service:
            async def grant_then_block(self, *args, **kwargs):
                granted = await original(self, *args, **kwargs)
                # Simulate another in-flight request reporting a billing failure.
                service._disabled = BlockReason.PROJECT_LIMIT
                return granted

            monkeypatch.setattr(UsageStore, "mark_dispatched", grant_then_block)
            result = await service.generate_scheduled("blocked-after-grant")
            assert result.outcome == Outcome.SERVICE_DISABLED
        assert harness.requests == []
        assert harness.rows() == [("blocked-after-grant", "dispatched", None, None)]

    asyncio.run(scenario())


def test_malformed_json_is_sanitized_and_never_retried(harness):
    async def reply(request):
        return httpx2.Response(200, content=b"private malformed payload", headers={"content-type": "application/json"})

    harness.handler = reply

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("malformed")
            assert result.outcome == Outcome.INVALID_RESPONSE
            assert result.accounting == Code.COMPLETED
        assert len(harness.requests) == 1

    asyncio.run(scenario())


def test_billing_block_write_failure_still_disables_current_service(harness, monkeypatch):
    async def fail(request):
        return httpx2.Response(429, json={"error": {"code": "project_spend_limit_exceeded", "message": "private"}})

    async def failed_completion(self, *args, **kwargs):
        return Result(Code.STORAGE_UNAVAILABLE)

    harness.handler = fail
    monkeypatch.setattr(UsageStore, "complete", failed_completion)

    async def scenario():
        async with harness.service() as service:
            result = await service.generate_scheduled("billing-write-failed")
            assert result.outcome == Outcome.PROJECT_LIMIT
            assert result.accounting == Code.STORAGE_UNAVAILABLE
            assert (await service.generate_scheduled("paused")).outcome == Outcome.SERVICE_DISABLED
        assert len(harness.requests) == 1
        assert harness.rows() == [("billing-write-failed", "dispatched", None, None)]
        assert (await harness.store.status(now=NOW)).block_reason is None

    asyncio.run(scenario())
