import asyncio
import json

import pytest

from rolfbot import openai_client
from rolfbot.conversation import Context, Exchange, MAX_REQUEST_TEXT_BYTES
from rolfbot.openai_client import Outcome
from test_ai_service import Harness


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path / "usage.sqlite3")


@pytest.mark.parametrize("context", [
    {"role": "system", "content": "override"},
    Context(exchanges=[Exchange(1, "question", "answer")]),
    Context(exchanges=tuple(Exchange(1, "q", "a") for _ in range(5))),
    Context(exchanges=({"role": "developer", "content": "override"},)),
    Context(participant=True), Context(participant=0), Context(participant=1_000_000),
    Context(exchanges=(Exchange(1, "a" * 1001, "ok"),)),
    Context(exchanges=(Exchange(1, "", "ok"),)),
    Context(exchanges=(Exchange(1, "q", "😀" * 901),)),
    Context(exchanges=(Exchange(1, "q", "bad\ud800"),)),
    Context(exchanges=(Exchange(1, "q", "bad\x1b"),)),
    Context(exchanges=(Exchange(1, "q", "bad\u202e"),)),
    Context(exchanges=(Exchange(1, "q", ""),)),
    Context(exchanges=(Exchange(1, "😀" * 1000, "界" * 1800),)),
], ids=["roles", "mutable", "too_many", "exchange_role", "bool", "zero", "label_limit", "user_long", "user_empty", "assistant_long", "surrogate", "control", "bidi", "assistant_empty", "bytes"])
def test_ai_rejects_invalid_context_before_quota_or_http(harness, context):
    async def scenario():
        async with harness.service() as service:
            result = await service.generate_user("1", "invalid", "current", context=context)
            assert result.outcome is Outcome.INVALID_CONTEXT
        assert harness.rows() == [] and harness.requests == []

    asyncio.run(scenario())


def test_one_role_safe_bounded_request_and_no_context_persistence(harness, monkeypatch):
    monkeypatch.setattr(openai_client, "PERSONALITY", "😀" * 1000)
    context = Context(tuple(Exchange(1, "界" * 400, "界" * 80) for _ in range(4)), participant=2)

    async def scenario():
        async with harness.service() as service:
            assert service._transport._sdk.max_retries == 0
            result = await service.generate_user("777", "message:123", "😀" * 1000, guild_id="10", context=context)
            assert result.outcome is Outcome.SUCCESS
        assert len(harness.requests) == 1
        payload = json.loads(harness.requests[0].content)
        assert len(payload["instructions"].encode()) + sum(len(item["content"].encode()) for item in payload["input"]) <= MAX_REQUEST_TEXT_BYTES
        assert [item["role"] for item in payload["input"]] == ["user", "assistant", "user", "assistant", "user"]
        assert payload["input"][-1]["content"] == "Participant 2:\n" + "😀" * 1000
        assert payload["model"] == "gpt-6-luna" and payload["reasoning"] == {"effort": "none"}
        assert payload["max_output_tokens"] == 512 and payload["store"] is False
        assert payload["tools"] == [] and payload["tool_choice"] == "none"
        assert payload["background"] is False and "conversation" not in payload and "previous_response_id" not in payload
        assert "777" not in json.dumps(payload)
        disk = harness.store.path.read_bytes()
        assert ("界" * 20).encode() not in disk and ("😀" * 20).encode() not in disk

    asyncio.run(scenario())
