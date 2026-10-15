import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rolfbot import usage_store
from rolfbot.usage import BlockReason, Code, TokenUsage
from rolfbot.usage_store import UsageStore

NOW = datetime(2026, 10, 15, 12, tzinfo=UTC)


@pytest.fixture
def version_one(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript((Path(__file__).parent / "fixtures/usage_v1.sql").read_text())
        timestamp = int(NOW.timestamp() * 1_000_000)
        rows = [
            ("reserved", "1", "10", "2026-10-15", "reserved", timestamp, None, None),
            ("dispatched", "2", "20", "2026-10-15", "dispatched", timestamp, timestamp, None),
            ("completed", "1", "10", "2026-10-14", "completed", timestamp, timestamp, timestamp),
            ("refunded", None, None, "2026-10-14", "refunded", timestamp, None, timestamp),
        ]
        db.executemany("INSERT INTO attempts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.execute("INSERT INTO cooldowns VALUES ('1', ?)", (timestamp + 30_000_000,))
        db.commit()
    return UsageStore(path), rows


def test_migration_preserves_every_v1_record_and_is_explicit_repeat_safe(version_one):
    store, original = version_one

    async def scenario():
        assert (await store.status(now=NOW)).code == Code.STORAGE_UNAVAILABLE
        assert (await store.initialize()).code == Code.STORAGE_UNAVAILABLE
        assert (await store.migrate()).code == Code.OK
        before = store.path.read_bytes()
        assert (await store.migrate()).code == Code.OK
        assert (await store.initialize()).code == Code.OK
        assert store.path.read_bytes() == before
        status = await store.status(now=NOW, user_id="1")
        assert status.quota.global_used == 2 and status.quota.user_used == 1
        assert (await store.reserve_user("1", "new", "hello", now=NOW)).code == Code.COOLDOWN

    asyncio.run(scenario())
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (2,)
        assert db.execute("SELECT attempt_id, user_id, guild_id, day, state, reserved_us, dispatched_us, finished_us FROM attempts").fetchall() == original
        assert db.execute("SELECT input_tokens, output_tokens FROM attempts").fetchall() == [(None, None)] * 4
        assert db.execute("SELECT timezone, ai_block FROM metadata").fetchone() == ("Europe/Oslo", None)


def test_migration_rolls_back_all_schema_and_version_changes(version_one, monkeypatch):
    store, original = version_one
    before = store.path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(usage_store, "_MIGRATION_2", usage_store._MIGRATION_2[:1] + ("INVALID SQL",))
        assert asyncio.run(store.migrate()).code == Code.STORAGE_UNAVAILABLE
    assert store.path.read_bytes() == before
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        assert db.execute("SELECT * FROM attempts").fetchall() == original
    assert asyncio.run(store.migrate()).code == Code.OK


@pytest.mark.parametrize("failure", ["missing", "empty", "corrupt", "future", "missing_table"])
def test_migration_never_repairs_invalid_or_unknown_storage(version_one, failure):
    store, _ = version_one
    if failure == "missing":
        store.path.unlink()
    elif failure in ("empty", "corrupt"):
        store.path.write_bytes(b"" if failure == "empty" else b"private corrupt storage")
    else:
        with closing(sqlite3.connect(store.path)) as db:
            db.execute("PRAGMA user_version = 99" if failure == "future" else "DROP TABLE cooldowns")
            db.commit()
    before = store.path.read_bytes() if store.path.exists() else None
    result = asyncio.run(store.migrate())
    assert result.code == Code.STORAGE_UNAVAILABLE
    assert "private" not in repr(result)
    assert (store.path.read_bytes() if store.path.exists() else None) == before


def test_usage_and_service_block_commit_together_and_survive_restart(version_one):
    store, _ = version_one

    async def scenario():
        assert (await store.migrate()).code == Code.OK
        counts = TokenUsage(12, 7)
        assert (await store.complete("dispatched", now=NOW, usage=counts, block_reason=BlockReason.PROJECT_LIMIT)).code == Code.COMPLETED
        assert (await store.complete("dispatched", now=NOW, usage=TokenUsage(99, 99))).code == Code.ALREADY_COMPLETED
        reopened = UsageStore(store.path)
        assert (await reopened.status(now=NOW)).block_reason == BlockReason.PROJECT_LIMIT
        assert (await reopened.reserve_scheduled("blocked", now=NOW)).code == Code.SERVICE_DISABLED
        assert (await reopened.mark_dispatched("reserved", now=NOW)).code == Code.SERVICE_DISABLED
        assert (await reopened.clear_ai_block()).code == Code.OK
        assert (await reopened.reserve_scheduled("recovered", now=NOW)).code == Code.RESERVED

    asyncio.run(scenario())
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT input_tokens, output_tokens FROM attempts WHERE attempt_id = 'dispatched'").fetchone() == (12, 7)


@pytest.mark.parametrize("value", [-1, True, "12", 1.5, 2**63])
def test_token_counts_reject_invalid_storage_values(value):
    with pytest.raises(ValueError, match="Token counts"):
        TokenUsage(value, None)


def test_completion_usage_and_block_all_roll_back_on_write_failure(version_one):
    store, _ = version_one
    assert asyncio.run(store.migrate()).code == Code.OK
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("CREATE TRIGGER deny_block BEFORE UPDATE ON metadata BEGIN SELECT RAISE(ABORT, 'private failure'); END")
        db.commit()
    result = asyncio.run(store.complete(
        "dispatched", now=NOW, usage=TokenUsage(12, 9), block_reason=BlockReason.PROJECT_LIMIT,
    ))
    assert result.code == Code.STORAGE_UNAVAILABLE
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT state, input_tokens, output_tokens FROM attempts WHERE attempt_id = 'dispatched'").fetchone() == ("dispatched", None, None)
        assert db.execute("SELECT ai_block FROM metadata").fetchone() == (None,)
