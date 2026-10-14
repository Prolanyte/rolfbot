import asyncio
import os
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from rolfbot.config import ConfigurationError
from rolfbot.usage import Code, State
from rolfbot.usage_store import LOCK_TIMEOUT_SECONDS, SCHEMA_VERSION, UsageStore

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    store = UsageStore(tmp_path / "private-usage.sqlite3")
    assert asyncio.run(store.initialize()).code == Code.OK
    return store


async def reserve(store, attempt="interaction", user="1", now=NOW):
    return await store.reserve_user(user, attempt, "private prompt", now=now)


async def overlap(monkeypatch, *operations):
    """Force separate worker connections to coexist before either begins SQL."""
    barrier = threading.Barrier(len(operations), timeout=5)
    connections = []
    original = UsageStore._connect

    def connect(self, *, write):
        connection = original(self, write=write)
        connections.append(connection)
        try:
            barrier.wait()
        except BaseException:
            connection.close()
            raise
        return connection

    with monkeypatch.context() as patch:
        patch.setattr(UsageStore, "_connect", connect)
        results = await asyncio.wait_for(asyncio.gather(*operations), timeout=10)
    assert len({id(connection) for connection in connections}) == len(operations)
    return results


@pytest.mark.parametrize("limit", ["global", "user"])
def test_independent_connections_race_for_last_slot(store, monkeypatch, limit):
    async def scenario():
        if limit == "global":
            for index in range(99):
                assert (await store.reserve_scheduled(f"job-{index}", now=NOW)).code == Code.RESERVED
            contenders = [reserve(store, "last-a", "1"), reserve(UsageStore(store.path), "last-b", "2")]
            denied = Code.GLOBAL_LIMIT
        else:
            for index in range(19):
                assert (await reserve(store, f"request-{index}", now=NOW + timedelta(seconds=index * 30))).code == Code.RESERVED
            when = NOW + timedelta(minutes=10)
            contenders = [reserve(store, "last-a", now=when), reserve(UsageStore(store.path), "last-b", now=when)]
            denied = Code.USER_LIMIT
        results = await overlap(monkeypatch, *contenders)
        assert sorted(result.code for result in results) == sorted([Code.RESERVED, denied])
        quota = (await store.status(now=NOW, user_id="1")).quota
        assert quota.global_used == (100 if limit == "global" else 20)
        if limit == "user":
            assert quota.user_used == 20

    asyncio.run(scenario())


def test_concurrent_duplicate_reservation_and_dispatch_each_succeed_once(store, monkeypatch):
    async def scenario():
        other = UsageStore(store.path)
        results = await overlap(monkeypatch, reserve(store), reserve(other))
        assert sorted(result.code for result in results) == sorted([Code.RESERVED, Code.DUPLICATE_ATTEMPT])
        results = await overlap(monkeypatch, store.mark_dispatched("interaction", now=NOW), other.mark_dispatched("interaction", now=NOW))
        assert sorted(result.code for result in results) == sorted([Code.DISPATCH_GRANTED, Code.INVALID_TRANSITION])
        assert (await store.status(now=NOW)).quota.global_used == 1

    asyncio.run(scenario())


def test_refund_and_dispatch_race_cannot_both_succeed(store, monkeypatch):
    async def scenario():
        assert (await reserve(store)).code == Code.RESERVED
        dispatched, refunded = await overlap(
            monkeypatch, store.mark_dispatched("interaction", now=NOW),
            UsageStore(store.path).refund_undispatched("interaction", now=NOW),
        )
        assert (dispatched.code, refunded.code) in (
            (Code.DISPATCH_GRANTED, Code.INVALID_TRANSITION),
            (Code.INVALID_TRANSITION, Code.REFUNDED),
        )
        assert (await store.status(now=NOW)).quota.global_used == (1 if dispatched.code == Code.DISPATCH_GRANTED else 0)

    asyncio.run(scenario())


def test_busy_timeout_denies_without_blocking_event_loop(store, monkeypatch):
    entered = threading.Event()
    original = UsageStore._connect

    def connect(self, *, write):
        entered.set()
        return original(self, write=write)

    monkeypatch.setattr(UsageStore, "_connect", connect)

    async def scenario():
        with closing(sqlite3.connect(store.path, isolation_level=None)) as blocker:
            blocker.execute("BEGIN EXCLUSIVE")
            started = time.monotonic()
            task = asyncio.create_task(reserve(store))
            # This callback must run while the storage worker waits on SQLite.
            assert await asyncio.to_thread(entered.wait, 2)
            await asyncio.sleep(0.01)
            assert not task.done()
            result = await asyncio.wait_for(task, 3)
            elapsed = time.monotonic() - started
            assert result.code == Code.STORAGE_UNAVAILABLE
            assert LOCK_TIMEOUT_SECONDS * 0.8 <= elapsed < 3
        quota = (await store.status(now=NOW, user_id="1")).quota
        assert quota.global_used == 0
        assert quota.cooldown_until is None

    asyncio.run(scenario())


def test_commit_lock_failure_rolls_back_reservation_and_cooldown(store):
    async def scenario():
        with closing(sqlite3.connect(store.path, isolation_level=None)) as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM attempts").fetchall()
            # A shared reader allows BEGIN IMMEDIATE + writes, but blocks COMMIT.
            assert (await reserve(store)).code == Code.STORAGE_UNAVAILABLE
        quota = (await store.status(now=NOW, user_id="1")).quota
        assert quota.global_used == quota.user_used == 0
        assert quota.cooldown_until is None
        assert (await reserve(store)).code == Code.RESERVED

    asyncio.run(scenario())


def test_mid_transaction_failure_is_atomic_and_sanitized(store, caplog):
    with closing(sqlite3.connect(store.path)) as connection:
        connection.execute("CREATE TRIGGER deny_cooldown BEFORE INSERT ON cooldowns BEGIN SELECT RAISE(ABORT, 'private exception details'); END")
        connection.commit()
    result = asyncio.run(reserve(store))
    assert result.code == Code.STORAGE_UNAVAILABLE
    quota = asyncio.run(store.status(now=NOW, user_id="1")).quota
    assert quota.global_used == 0 and quota.cooldown_until is None
    assert "private" not in repr(result) + repr(store) + caplog.text


def test_lost_dispatch_commit_acknowledgement_never_grants_or_refunds(store, monkeypatch):
    asyncio.run(reserve(store))
    original = UsageStore._connect

    class LoseCommitAcknowledgement:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, parameters=()):
            result = self.connection.execute(statement, parameters)
            if statement == "COMMIT":
                raise sqlite3.OperationalError("private ambiguous commit detail")
            return result

        def close(self):
            self.connection.close()

    with monkeypatch.context() as patch:
        patch.setattr(UsageStore, "_connect", lambda self, *, write: LoseCommitAcknowledgement(original(self, write=write)))
        result = asyncio.run(store.mark_dispatched("interaction", now=NOW))
        assert result.code == Code.STORAGE_UNAVAILABLE
    reopened = UsageStore(store.path)
    assert asyncio.run(reopened.mark_dispatched("interaction", now=NOW)).code == Code.INVALID_TRANSITION
    assert asyncio.run(reopened.refund_undispatched("interaction", now=NOW)).code == Code.INVALID_TRANSITION
    assert asyncio.run(reopened.status(now=NOW)).quota.global_used == 1


def test_cancelled_worker_result_does_not_release_capacity_or_allow_redispatch(store, monkeypatch):
    asyncio.run(reserve(store))
    committed = threading.Event()
    release = threading.Event()
    original = UsageStore._transaction

    def delayed_result(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        committed.set()
        assert release.wait(5)
        return result

    async def scenario():
        with monkeypatch.context() as patch:
            patch.setattr(UsageStore, "_transaction", delayed_result)
            task = asyncio.create_task(store.mark_dispatched("interaction", now=NOW))
            try:
                assert await asyncio.to_thread(committed.wait, 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
        assert (await store.mark_dispatched("interaction", now=NOW)).code == Code.INVALID_TRANSITION
        assert (await store.refund_undispatched("interaction", now=NOW)).code == Code.INVALID_TRANSITION
        assert (await store.status(now=NOW)).quota.global_used == 1

    asyncio.run(scenario())


def test_restart_in_a_new_process_preserves_committed_states(store, child_environment, tmp_path):
    # Child exits without cleanup after both commits, mimicking process death.
    script = """
import asyncio, os, sys
from datetime import UTC, datetime
from pathlib import Path
from rolfbot.usage_store import UsageStore
from rolfbot.usage import Code
async def create():
    store = UsageStore(Path(sys.argv[1]))
    now = datetime(2026, 10, 14, 12, tzinfo=UTC)
    assert (await store.reserve_user('1', 'reserved', 'synthetic input', now=now)).code == Code.RESERVED
    assert (await store.reserve_user('2', 'dispatched', 'synthetic input', now=now)).code == Code.RESERVED
    assert (await store.mark_dispatched('dispatched', now=now)).code == Code.DISPATCH_GRANTED
    os._exit(0)
asyncio.run(create())
"""
    result = subprocess.run([sys.executable, "-c", script, str(store.path)], cwd=tmp_path, env=child_environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    reopened = UsageStore(store.path)
    assert asyncio.run(reopened.status(now=NOW)).quota.global_used == 2
    assert asyncio.run(reserve(reopened, "new")).code == Code.COOLDOWN
    assert asyncio.run(reopened.mark_dispatched("dispatched", now=NOW)).code == Code.INVALID_TRANSITION
    with closing(sqlite3.connect(store.path)) as connection:
        assert set(connection.execute("SELECT state FROM attempts")) == {(State.RESERVED,), (State.DISPATCHED,)}


@pytest.mark.parametrize("failure", ["missing", "empty", "corrupt", "directory", "unknown_version", "missing_table", "missing_cooldowns"])
def test_unusable_storage_fails_closed_without_repair_or_recreation(store, failure):
    if failure == "missing":
        store.path.unlink()
    elif failure == "empty":
        store.path.write_bytes(b"")
    elif failure == "corrupt":
        store.path.write_bytes(b"private corrupt content" * 200)
    elif failure == "directory":
        store.path.unlink()
        store.path.mkdir()
    else:
        with closing(sqlite3.connect(store.path)) as connection:
            statement = {
                "unknown_version": "PRAGMA user_version = 999",
                "missing_table": "DROP TABLE attempts",
                "missing_cooldowns": "DROP TABLE cooldowns",
            }[failure]
            connection.execute(statement)
            connection.commit()
    before = store.path.read_bytes() if store.path.is_file() else None
    operations = [reserve(store), store.reserve_scheduled("job", now=NOW), store.status(now=NOW), store.mark_dispatched("interaction", now=NOW), store.complete("interaction", now=NOW), store.refund_undispatched("interaction", now=NOW)]
    for operation in operations:
        result = asyncio.run(operation)
        assert result.code == Code.STORAGE_UNAVAILABLE
        assert "private" not in repr(result)
    if failure == "missing":
        assert not store.path.exists()
    else:
        assert asyncio.run(store.initialize()).code == Code.STORAGE_UNAVAILABLE
    if before is not None:
        assert store.path.read_bytes() == before


def test_inaccessible_storage_and_missing_parent_fail_safely(store, tmp_path):
    path = tmp_path / "nonexistent-parent" / "usage.sqlite3"
    missing = UsageStore(path)
    assert asyncio.run(missing.initialize()).code == Code.STORAGE_UNAVAILABLE
    assert not path.parent.exists()
    if os.geteuid() == 0:
        pytest.skip("Root bypasses file permissions")
    store.path.chmod(0)
    try:
        assert asyncio.run(reserve(store)).code == Code.STORAGE_UNAVAILABLE
        assert asyncio.run(store.status(now=NOW)).code == Code.STORAGE_UNAVAILABLE
        assert asyncio.run(store.initialize()).code == Code.STORAGE_UNAVAILABLE
    finally:
        store.path.chmod(0o600)


def test_initialization_schema_durability_settings_and_read_only_status(store):
    assert store.path.stat().st_mode & 0o777 == 0o600
    with closing(store._connect(write=True)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert connection.execute("PRAGMA synchronous").fetchone() == (3,)
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(attempts)")}
        assert {"global_usage", "user_usage"} <= indexes
    assert asyncio.run(reserve(store)).code == Code.RESERVED
    before = store.path.read_bytes()
    modified = store.path.stat().st_mtime_ns
    assert asyncio.run(store.status(now=NOW)).quota.global_used == 1
    assert asyncio.run(store.initialize()).code == Code.OK
    assert store.path.read_bytes() == before
    assert store.path.stat().st_mtime_ns == modified
    assert b"private prompt" not in before


def test_constructor_requires_explicit_path_and_never_creates_it(tmp_path):
    for value in (None, "relative", ":memory:"):
        with pytest.raises(ConfigurationError):
            UsageStore(value)
    path = tmp_path / "not-opened.sqlite3"
    UsageStore(path)
    assert not path.exists()
