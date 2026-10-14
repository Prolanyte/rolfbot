import asyncio
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from rolfbot.usage import Code, State, validate_input
from rolfbot.usage_store import UsageStore

NOW = datetime(2026, 10, 14, 12, tzinfo=UTC)


def run(operation):
    return asyncio.run(operation)


@pytest.fixture
def store(tmp_path):
    result = UsageStore(tmp_path / "usage.sqlite3")
    assert run(result.initialize()).code == Code.OK
    return result


def reserve(store, attempt="interaction-1", *, user="1", now=NOW, guild="10", text="Hello"):
    return run(store.reserve_user(user, attempt, text, now=now, guild_id=guild))


def status(store, now=NOW, user="1"):
    result = run(store.status(now=now, user_id=user))
    assert result.code == Code.OK
    return result.quota


@pytest.mark.parametrize("text,expected", [
    ("😀" * 1000, Code.OK), ("😀" * 1001, Code.INPUT_TOO_LONG),
    ("e\u0301" * 500, Code.OK), ("e\u0301" * 500 + "a", Code.INPUT_TOO_LONG),
    ("", Code.INVALID_INPUT), (" \t\n", Code.INVALID_INPUT),
    (None, Code.INVALID_INPUT), (42, Code.INVALID_INPUT),
    ("\ud800", Code.INVALID_INPUT), ("a\x00b", Code.INVALID_INPUT),
])
def test_unicode_input_validation_before_any_storage(tmp_path, text, expected):
    assert validate_input(text).code == expected
    unopened = UsageStore(tmp_path / "does-not-exist.sqlite3")
    result = reserve(unopened, text=text)
    assert result.code == (Code.STORAGE_UNAVAILABLE if expected == Code.OK else expected)
    assert not unopened.path.exists()


def test_input_boundaries_and_rejected_input_do_not_consume_capacity(store):
    assert reserve(store, text="😀" * 1001).code == Code.INPUT_TOO_LONG
    assert status(store).global_used == 0
    assert status(store).cooldown_until is None
    assert reserve(store, text="😀" * 1000).code == Code.RESERVED
    assert status(store).global_used == 1


@pytest.mark.parametrize("value", [None, 1, True, "", "0", "01", "-1", "1.0", "１２", "1; DROP TABLE attempts", "1\n", str(2**64)])
def test_invalid_user_and_guild_ids(store, value):
    assert reserve(store, user=value).code == Code.INVALID_IDENTIFIER
    if value is not None:  # Absent guild context is explicitly supported.
        assert reserve(store, guild=value).code == Code.INVALID_IDENTIFIER
    assert run(store.status(now=NOW, user_id=value)).code == (Code.OK if value is None else Code.INVALID_IDENTIFIER)
    assert status(store).global_used == 0


@pytest.mark.parametrize("value", [None, 1, "", "a" * 129, " job", "job\n", "é", "x' OR 1=1", "-start"])
def test_invalid_attempt_ids_all_entry_points(store, value):
    assert reserve(store, value).code == Code.INVALID_IDENTIFIER
    assert run(store.reserve_scheduled(value, now=NOW)).code == Code.INVALID_IDENTIFIER
    for operation in (store.mark_dispatched, store.complete, store.refund_undispatched):
        assert run(operation(value, now=NOW)).code == Code.INVALID_IDENTIFIER
    assert status(store).global_used == 0


def test_twentieth_admitted_twenty_first_denied_across_guilds(store):
    for index in range(20):
        result = reserve(store, f"request-{index}", now=NOW + timedelta(seconds=30 * index), guild="10" if index % 2 else "20")
        assert result.code == Code.RESERVED
    result = reserve(store, "twenty-first", now=NOW + timedelta(minutes=10))
    assert result.code == Code.USER_LIMIT
    assert result.next_eligible_at == datetime(2026, 10, 14, 22, tzinfo=UTC)
    assert status(store).user_used == status(store).global_used == 20
    assert reserve(store, "another-user", user="2").code == Code.RESERVED


def test_global_boundary_shared_by_users_and_scheduled_generations(store):
    for index in range(1, 100):
        assert reserve(store, f"user-{index}", user=str(index)).code == Code.RESERVED
    assert run(store.reserve_scheduled("job-100", now=NOW)).code == Code.RESERVED
    assert reserve(store, "user-101", user="101").code == Code.GLOBAL_LIMIT
    denial = run(store.reserve_scheduled("job-101", now=NOW))
    assert denial.code == Code.GLOBAL_LIMIT
    assert denial.next_eligible_at == status(store).reset_at
    assert status(store).global_used == 100
    assert status(store, user="101").cooldown_until is None


def test_scheduled_requests_do_not_touch_personal_usage_or_cooldown(store):
    assert reserve(store).code == Code.RESERVED
    before = status(store)
    for attempt in ("job-a", "job-b"):
        assert run(store.reserve_scheduled(attempt, now=NOW)).code == Code.RESERVED
    after = status(store)
    assert after.user_used == before.user_used == 1
    assert after.cooldown_until == before.cooldown_until
    assert after.global_used == 3
    assert run(store.status(now=NOW)).quota.user_used is None


def test_scheduled_dispatch_is_single_use_and_retains_global_capacity(store):
    assert run(store.reserve_scheduled("job-1", now=NOW)).code == Code.RESERVED
    assert run(store.mark_dispatched("job-1", now=NOW)).code == Code.DISPATCH_GRANTED
    assert run(store.mark_dispatched("job-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert run(store.complete("job-1", now=NOW)).code == Code.COMPLETED
    assert status(store).global_used == 1
    assert status(store).user_used == 0
    assert status(store).cooldown_until is None


def test_maximum_discord_id_is_stored_without_integer_overflow(store):
    user = str(2**64 - 1)
    assert reserve(store, user=user, guild=user).code == Code.RESERVED
    assert status(store, user=user).user_used == 1


def test_cooldown_exact_boundary_across_servers_and_backward_clock(store):
    assert reserve(store).code == Code.RESERVED
    for when in (NOW - timedelta(days=1), NOW, NOW + timedelta(seconds=29, microseconds=999999)):
        result = reserve(store, "next", now=when, guild="20")
        assert result.code == Code.COOLDOWN
        assert result.next_eligible_at == NOW + timedelta(seconds=30)
    assert reserve(store, "next", now=NOW + timedelta(seconds=30), guild="20").code == Code.RESERVED


def test_duplicate_ids_share_namespace_across_users_jobs_days_and_restart(store):
    assert reserve(store).code == Code.RESERVED
    reopened = UsageStore(store.path)
    tomorrow = NOW + timedelta(days=1)
    assert reserve(reopened, user="2", now=tomorrow).code == Code.DUPLICATE_ATTEMPT
    assert run(reopened.reserve_scheduled("interaction-1", now=tomorrow)).code == Code.DUPLICATE_ATTEMPT
    assert run(reopened.reserve_scheduled("job", now=NOW)).code == Code.RESERVED
    assert run(reopened.reserve_scheduled("job", now=tomorrow)).code == Code.DUPLICATE_ATTEMPT
    assert reserve(reopened, "job", now=tomorrow).code == Code.DUPLICATE_ATTEMPT
    assert status(reopened).global_used == 2
    assert status(reopened, now=tomorrow).global_used == 0


def test_dispatch_completion_and_errors_always_keep_capacity(store):
    assert reserve(store).code == Code.RESERVED
    assert run(store.complete("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert run(store.mark_dispatched("interaction-1", now=NOW)).code == Code.DISPATCH_GRANTED
    assert run(store.mark_dispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert run(store.refund_undispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    # A timeout/error is completed just like a success; no capacity is released.
    assert run(store.complete("interaction-1", now=NOW)).code == Code.COMPLETED
    assert run(store.complete("interaction-1", now=NOW)).code == Code.ALREADY_COMPLETED
    assert run(store.mark_dispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert run(store.refund_undispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert status(store).global_used == status(store).user_used == 1


def test_refund_is_idempotent_preserves_cooldown_and_prevents_replay(store):
    assert reserve(store).code == Code.RESERVED
    assert run(store.refund_undispatched("interaction-1", now=NOW)).code == Code.REFUNDED
    assert run(store.refund_undispatched("interaction-1", now=NOW)).code == Code.ALREADY_REFUNDED
    assert status(store).global_used == status(store).user_used == 0
    assert reserve(store, "new-id").code == Code.COOLDOWN
    assert reserve(store).code == Code.DUPLICATE_ATTEMPT
    assert run(store.mark_dispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert run(store.complete("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    assert reserve(store, "new-id", now=NOW + timedelta(seconds=30)).code == Code.RESERVED


@pytest.mark.parametrize("operation", ["mark_dispatched", "complete", "refund_undispatched"])
def test_unknown_attempt_cannot_transition(store, operation):
    assert run(getattr(store, operation)("missing", now=NOW)).code == Code.NOT_FOUND
    assert status(store).global_used == 0


@pytest.mark.parametrize("dispatch", [False, True])
def test_crash_before_or_after_dispatch_is_conservative(store, dispatch):
    assert reserve(store).code == Code.RESERVED
    if dispatch:
        # Discard the returned grant, as if the result/API outcome were lost.
        run(store.mark_dispatched("interaction-1", now=NOW))
    reopened = UsageStore(store.path)
    assert run(reopened.initialize()).code == Code.OK
    assert status(reopened).global_used == status(reopened).user_used == 1
    assert reserve(reopened, "after-restart").code == Code.COOLDOWN
    assert reserve(reopened).code == Code.DUPLICATE_ATTEMPT
    if dispatch:
        assert run(reopened.mark_dispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
        assert run(reopened.refund_undispatched("interaction-1", now=NOW)).code == Code.INVALID_TRANSITION
    # No expiry releases capacity, even for the reserved-but-never-sent record.
    assert status(reopened, now=NOW + timedelta(hours=1)).global_used == 1


@pytest.mark.parametrize("local_date", ["2026-10-14", "2026-10-31", "2026-12-31"])
def test_midnight_month_year_and_cooldown_across_restart(store, local_date):
    before = datetime.fromisoformat(local_date + "T23:59:50").replace(tzinfo=store.timezone).astimezone(UTC)
    midnight = before + timedelta(seconds=10)
    assert reserve(store, now=before).code == Code.RESERVED
    reopened = UsageStore(store.path)
    assert status(reopened, now=midnight).global_used == 0
    assert reserve(reopened, "new-day", now=midnight).code == Code.COOLDOWN
    assert reserve(reopened, "new-day", now=before + timedelta(seconds=30)).code == Code.RESERVED
    assert status(reopened, now=before).global_used == 1
    assert status(reopened, now=midnight).global_used == 1


@pytest.mark.parametrize("day,hours", [("2026-03-29", 23), ("2026-10-25", 25)])
def test_both_oslo_dst_days_share_one_bucket(store, day, hours):
    start = datetime.fromisoformat(day).replace(tzinfo=store.timezone).astimezone(UTC)
    end = start + timedelta(hours=hours)
    assert reserve(store, now=start).code == Code.RESERVED
    assert status(store, now=start).reset_at == end
    assert run(store.reserve_scheduled("late-job", now=end - timedelta(microseconds=1))).code == Code.RESERVED
    assert status(store, now=end - timedelta(microseconds=1)).global_used == 2
    assert status(store, now=end).global_used == 0


@pytest.mark.parametrize("start", [datetime(2026, 3, 29, 0, 59, 50, tzinfo=UTC), datetime(2026, 10, 25, 0, 59, 50, tzinfo=UTC)])
def test_cooldown_through_both_dst_transitions(store, start):
    assert reserve(store, now=start.astimezone(store.timezone)).code == Code.RESERVED
    assert reserve(store, "next", now=(start + timedelta(seconds=29)).astimezone(store.timezone)).code == Code.COOLDOWN
    assert reserve(store, "next", now=(start + timedelta(seconds=30)).astimezone(store.timezone)).code == Code.RESERVED


def test_autumn_repeated_hour_is_not_a_backward_utc_clock(store):
    first = datetime(2026, 10, 25, 2, 30, tzinfo=store.timezone, fold=0)
    second = first.replace(fold=1)
    assert reserve(store, now=first).code == Code.RESERVED
    assert reserve(store, "second-fold", now=second).code == Code.RESERVED
    assert status(store, now=second).global_used == 2


@pytest.mark.parametrize("value", [None, "2026-10-14", datetime(2026, 10, 14), datetime(1969, 12, 31, tzinfo=UTC), datetime(9999, 1, 1, tzinfo=UTC), datetime(2026, 3, 29, 2, 30, tzinfo=ZoneInfo("Europe/Oslo"))])
def test_invalid_time_consistently_denied(store, value):
    assert reserve(store, now=value).code == Code.INVALID_TIME
    assert run(store.reserve_scheduled("job", now=value)).code == Code.INVALID_TIME
    for operation in (store.mark_dispatched, store.complete, store.refund_undispatched):
        assert run(operation("job", now=value)).code == Code.INVALID_TIME
    assert run(store.status(now=value)).code == Code.INVALID_TIME
    assert status(store).global_used == 0


def test_completion_and_refund_keep_original_day(store):
    tomorrow = NOW + timedelta(days=1)
    assert reserve(store).code == Code.RESERVED
    assert run(store.reserve_scheduled("refundable", now=NOW)).code == Code.RESERVED
    assert run(store.reserve_scheduled("today", now=tomorrow)).code == Code.RESERVED
    assert run(store.mark_dispatched("interaction-1", now=tomorrow)).code == Code.DISPATCH_GRANTED
    assert run(store.complete("interaction-1", now=tomorrow)).code == Code.COMPLETED
    assert run(store.refund_undispatched("refundable", now=tomorrow)).code == Code.REFUNDED
    assert status(store).global_used == 1
    assert status(store, now=tomorrow).global_used == 1
    with closing(sqlite3.connect(store.path)) as connection:
        assert connection.execute("SELECT day FROM attempts WHERE attempt_id = 'interaction-1'").fetchone() == ("2026-10-14",)


def test_configured_timezone_is_persisted_and_cannot_be_switched(tmp_path):
    path = tmp_path / "usage.sqlite3"
    store = UsageStore(path, ZoneInfo("UTC"))
    assert run(store.initialize()).code == Code.OK
    late = datetime(2026, 10, 14, 23, tzinfo=UTC)
    assert reserve(store, now=late).code == Code.RESERVED
    assert status(store, now=late).day == "2026-10-14"
    assert status(store, now=late).reset_at == datetime(2026, 10, 15, tzinfo=UTC)
    mismatch = UsageStore(path)
    assert run(mismatch.initialize()).code == Code.STORAGE_UNAVAILABLE
    assert reserve(mismatch, user="2", now=late).code == Code.STORAGE_UNAVAILABLE
    assert run(mismatch.status(now=late)).code == Code.STORAGE_UNAVAILABLE
