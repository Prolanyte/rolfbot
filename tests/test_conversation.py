import asyncio
from dataclasses import replace

import pytest

from rolfbot.conversation import (
    Context, ConversationMemory, Exchange, IDLE_SECONDS, MAX_HISTORY_BYTES,
    MAX_RECORDS, MAX_REQUEST_TEXT_BYTES, history_bytes, prepare_turn, request_messages,
)


def add(memory, number, snapshot=None, author="private-user-id", user="question", answer="answer"):
    turn = prepare_turn(snapshot, author)
    key = (10, 100, number)
    memory.remember(key, turn, user, answer)
    return memory.lookup(key)


def test_complete_pair_limit_and_participants_remain_bounded():
    memory = ConversationMemory()
    snapshot = None
    for number in range(100):
        snapshot = add(memory, number, snapshot, author=f"private-{number}", user=f"question-{number}")
        assert len(snapshot.exchanges) <= 4
        assert len(snapshot.participants) <= 4
    assert [pair.user for pair in snapshot.exchanges] == [f"question-{n}" for n in range(96, 100)]
    assert [pair.participant for pair in snapshot.exchanges] == [97, 98, 99, 100]
    assert len(memory) == MAX_RECORDS
    assert "question" not in repr(snapshot) and "private" not in repr(snapshot)


def test_snapshot_branches_and_scope_never_include_future_turns():
    memory = ConversationMemory()
    root = add(memory, 1, user="root")
    left = add(memory, 2, root, user="left")
    right = add(memory, 3, root, author="another", user="right")
    assert [pair.user for pair in root.exchanges] == ["root"]
    assert [pair.user for pair in left.exchanges] == ["root", "left"]
    assert [pair.user for pair in right.exchanges] == ["root", "right"]
    assert [pair.participant for pair in right.exchanges] == [1, 2]
    assert prepare_turn(right, "private-user-id").context.participant == 1
    assert memory.lookup((20, 100, 1)) is None
    assert memory.lookup((10, 200, 1)) is None
    add(memory, 1, right, user="duplicate")
    assert memory.lookup((10, 100, 1)) is root


def test_utf8_limit_discards_whole_pairs_and_preserves_unicode():
    memory = ConversationMemory()
    snapshot = None
    for number in range(8):
        snapshot = add(memory, number, snapshot, user="😀" * 500, answer="Æ" * 400)
        assert history_bytes(snapshot.exchanges) <= MAX_HISTORY_BYTES
        assert all(pair.user == "😀" * 500 and pair.assistant == "Æ" * 400 for pair in snapshot.exchanges)
    assert len(snapshot.exchanges) == 2
    # One valid exchange can exceed the history cap; retain the reply identity
    # with empty context, never a truncated user message or orphan assistant.
    large = add(memory, 9, snapshot, user="😀" * 1000, answer="界" * 1800)
    assert large.exchanges == () and large.participants == ()
    assert memory.lookup((10, 100, 9)) is large


def test_expiry_and_lru_use_activity_not_unrelated_lookup():
    elapsed = [0.0]
    memory = ConversationMemory(clock=lambda: elapsed[0])
    for number in range(MAX_RECORDS):
        add(memory, number)
    elapsed[0] = 10
    memory.touch((10, 100, 0))
    memory.lookup((10, 100, 1))
    add(memory, 64)
    assert memory.lookup((10, 100, 1)) is None
    assert memory.lookup((10, 100, 0)) is not None
    elapsed[0] = IDLE_SECONDS
    memory.expire()
    assert len(memory) == 2
    elapsed[0] += 10
    assert len(memory) == 0
    assert prepare_turn(memory.lookup((10, 100, 0)), "new").context == Context()


def test_idle_timer_clears_text_without_traffic_and_close_cancels_timer():
    async def scenario():
        elapsed = [0.0]
        memory = ConversationMemory(clock=lambda: elapsed[0])
        add(memory, 1)
        timer = memory._timer
        assert timer is not None
        elapsed[0] = IDLE_SECONDS
        # Invoke the scheduled callback using a fake clock; no hour-long sleep.
        timer._callback(*timer._args)
        assert not memory._records and memory._timer is None
        add(memory, 2)
        timer = memory._timer
        memory.close()
        assert timer.cancelled() and not memory._records
        add(memory, 3)
        assert len(memory) == 0

    asyncio.run(scenario())


def test_combined_text_budget_drops_oldest_complete_pair_preserves_current():
    context = Context(tuple(Exchange(1, "界" * 400, "界" * 80) for _ in range(4)), participant=2)
    current, instructions = "😀" * 1000, "😀" * 1000
    messages = request_messages(current, context, instructions)
    size = len(instructions.encode()) + sum(len(item["content"].encode()) for item in messages)
    assert size <= MAX_REQUEST_TEXT_BYTES
    assert len(messages) == 5  # Two oldest pairs removed to fit the text budget.
    assert [item["role"] for item in messages] == ["user", "assistant", "user", "assistant", "user"]
    assert messages[-1]["content"] == "Participant 2:\n" + current
    assert len(context.exchanges) == 4
    with pytest.raises(ValueError):
        request_messages(current, replace(context, exchanges=context.exchanges * 2), instructions)


def test_participant_exhaustion_starts_fresh_and_never_reuses_old_label():
    memory = ConversationMemory()
    old = add(memory, 1)
    exhausted = replace(old, next_participant=1_000_000)
    fresh = prepare_turn(exhausted, "new")
    assert fresh.context == Context()
    assert fresh.participants == (("new", 1),)
