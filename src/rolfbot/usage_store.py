"""File-backed SQLite admission barrier. Every public operation is asynchronous.

Each operation owns a short-lived connection in asyncio's worker pool. No SQL
transaction crosses an await or a future network call. Cancellation can leave a
worker's transaction committed: a lost result is NOT permission to dispatch,
retry, or refund. Unfinished records are never replayed or expired here.
"""

import asyncio
import os
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rolfbot.config import ConfigurationError, database_path
from rolfbot.usage import (
    COOLDOWN, GLOBAL_DAILY_LIMIT, USER_DAILY_LIMIT, Code, Moment, QuotaStatus,
    Result, State, TokenUsage, BlockReason, from_microseconds, moment, valid_attempt_id, valid_discord_id,
    validate_input,
)

SCHEMA_VERSION = 2
LOCK_TIMEOUT_SECONDS = 0.25

_SCHEMA = (
    "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), timezone TEXT NOT NULL)",
    """CREATE TABLE attempts (
        attempt_id TEXT PRIMARY KEY NOT NULL,
        user_id TEXT,
        guild_id TEXT,
        day TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('reserved', 'dispatched', 'completed', 'refunded')),
        reserved_us INTEGER NOT NULL,
        dispatched_us INTEGER,
        finished_us INTEGER,
        CHECK(user_id IS NOT NULL OR guild_id IS NULL),
        CHECK(dispatched_us IS NULL OR dispatched_us >= reserved_us),
        CHECK(finished_us IS NULL OR finished_us >= COALESCE(dispatched_us, reserved_us)),
        CHECK(
            (state = 'reserved' AND dispatched_us IS NULL AND finished_us IS NULL) OR
            (state = 'dispatched' AND dispatched_us IS NOT NULL AND finished_us IS NULL) OR
            (state = 'completed' AND dispatched_us IS NOT NULL AND finished_us IS NOT NULL) OR
            (state = 'refunded' AND dispatched_us IS NULL AND finished_us IS NOT NULL)
        )
    )""",
    "CREATE INDEX global_usage ON attempts(day) WHERE state != 'refunded'",
    "CREATE INDEX user_usage ON attempts(user_id, day) WHERE state != 'refunded'",
    "CREATE TABLE cooldowns (user_id TEXT PRIMARY KEY NOT NULL, until_us INTEGER NOT NULL)",
)

_MIGRATION_2 = (
    "ALTER TABLE attempts ADD COLUMN input_tokens INTEGER CHECK(input_tokens IS NULL OR (typeof(input_tokens) = 'integer' AND input_tokens >= 0))",
    "ALTER TABLE attempts ADD COLUMN output_tokens INTEGER CHECK(output_tokens IS NULL OR (typeof(output_tokens) = 'integer' AND output_tokens >= 0))",
    "ALTER TABLE metadata ADD COLUMN ai_block TEXT CHECK(ai_block IS NULL OR ai_block IN ('project_limit', 'organization_limit', 'credit_exhausted', 'billing_limit', 'authentication', 'permission', 'configuration'))",
)


@dataclass(frozen=True, slots=True)
class UsageStore:
    """One persistent file, one fixed timezone; constructing this opens nothing.

    Use trusted server timestamps, canonical string Discord IDs, and stable
    attempt IDs (1–128 ASCII letters/digits/_.:-, beginning with letter/digit).
    Guild context is optional metadata, not an authorization or quota boundary.
    All callers must first validate guild/channel access themselves.

    Call reserve -> prepare locally -> mark_dispatched -> API -> complete.
    Only a newly returned RESERVED may proceed to mark_dispatched, and only
    DISPATCH_GRANTED permits the single API attempt, with SDK retries disabled.
    Never dispatch after cancellation or any other result. Refund is solely for
    local abandonment before dispatch; the transaction checks that proof in the
    stored state. Never delete records to retry. A new retry needs a new reserve.
    """

    path: Path = field(repr=False)
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Oslo"))

    def __post_init__(self) -> None:
        if not isinstance(self.path, (str, Path)):
            raise ConfigurationError("ROLFBOT_DATABASE_PATH is required for usage storage.")
        object.__setattr__(self, "path", database_path(str(self.path)))
        if not isinstance(self.timezone, ZoneInfo):
            raise ConfigurationError("Usage storage requires a configured IANA timezone.")

    async def initialize(self) -> Result:
        """Explicit first provisioning, or validation of an existing schema.

        Parent directory must already exist. New files are owner-only (0600).
        Never initialize an existing empty/unknown/corrupt database: that could
        erase accounting. A failed initial provisioning needs operator review.
        This is NOT an automatic missing-database recovery operation.
        """
        return await asyncio.to_thread(self._initialize)

    async def validate(self) -> Result:
        """Startup check of existing writable storage; never create or migrate.

        Check integrity, schema, timezone and any persistent service block while
        holding a short write lock. A missing file remains missing.
        """
        def validate(connection: sqlite3.Connection) -> Result:
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Integrity failure")
            # BEGIN IMMEDIATE alone can succeed on a read-only file. Force a
            # journaled page write, then roll it back, to check both file and
            # directory writability without changing metadata or accounting.
            connection.execute("SAVEPOINT startup_probe")
            connection.execute("UPDATE metadata SET timezone = timezone WHERE singleton = 1")
            connection.execute("ROLLBACK TO startup_probe")
            connection.execute("RELEASE startup_probe")
            return Result(Code.OK, block_reason=self._block(connection))
        return await asyncio.to_thread(self._transaction, validate)

    async def migrate(self) -> Result:
        """Explicit, transactional v1 -> v2 migration of existing storage only.

        Back up first. Repeat-safe on v2. No state is removed or reset; older
        application versions will reject v2. Ordinary operations never migrate.
        """
        def migrate(connection: sqlite3.Connection) -> Result:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (1, SCHEMA_VERSION):
                raise sqlite3.DatabaseError("Unsupported schema")
            self._verify(connection, version=version)
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise sqlite3.DatabaseError("Integrity failure")
            if version == 1:
                self._migrate_v2(connection)
            return Result(Code.OK)

        return await asyncio.to_thread(self._transaction, migrate, new=True)

    async def clear_ai_block(self) -> Result:
        """Operator-only recovery after fixing billing/configuration; no retries.

        The running AI service must also be closed/recreated. Never call this
        automatically or expose it to ordinary Discord requests.
        """
        def clear(connection: sqlite3.Connection) -> Result:
            connection.execute("UPDATE metadata SET ai_block = NULL WHERE singleton = 1")
            return Result(Code.OK)
        return await asyncio.to_thread(self._transaction, clear)

    @staticmethod
    def _block(connection: sqlite3.Connection) -> BlockReason | None:
        value = connection.execute("SELECT ai_block FROM metadata WHERE singleton = 1").fetchone()[0]
        return BlockReason(value) if value is not None else None

    async def reserve_user(
        self, user_id: str, attempt_id: str, text: str, *,
        now: datetime, guild_id: str | None = None,
    ) -> Result:
        validation = validate_input(text)
        if validation.code != Code.OK:
            return validation
        if not valid_discord_id(user_id) or (guild_id is not None and not valid_discord_id(guild_id)):
            return Result(Code.INVALID_IDENTIFIER)
        return await self._reserve(attempt_id, user_id, guild_id, now)

    async def reserve_scheduled(self, attempt_id: str, *, now: datetime) -> Result:
        """Reserve one future generation from global capacity, without a user."""
        return await self._reserve(attempt_id, None, None, now)

    async def _reserve(self, attempt_id, user_id, guild_id, now) -> Result:
        if not valid_attempt_id(attempt_id):
            return Result(Code.INVALID_IDENTIFIER)
        at = moment(now, self.timezone)
        if at is None:
            return Result(Code.INVALID_TIME)

        def reserve(connection: sqlite3.Connection) -> Result:
            if block := self._block(connection):
                return Result(Code.SERVICE_DISABLED, block_reason=block)
            if connection.execute("SELECT 1 FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone():
                return Result(Code.DUPLICATE_ATTEMPT)
            quota = self._status(connection, user_id, at)
            eligible = at.utc
            reason = None
            if quota.cooldown_until is not None and quota.cooldown_until > at.utc:
                eligible = quota.cooldown_until
                reason = Code.COOLDOWN
            if quota.user_used is not None and quota.user_used >= USER_DAILY_LIMIT:
                eligible = max(eligible, at.reset_at)
                reason = Code.USER_LIMIT
            if quota.global_used >= GLOBAL_DAILY_LIMIT:
                eligible = max(eligible, at.reset_at)
                reason = Code.GLOBAL_LIMIT
            if reason is not None:
                return Result(reason, next_eligible_at=eligible, quota=quota)
            connection.execute(
                "INSERT INTO attempts (attempt_id, user_id, guild_id, day, state, reserved_us) "
                "VALUES (?, ?, ?, ?, 'reserved', ?)",
                (attempt_id, user_id, guild_id, at.day, at.microseconds),
            )
            if user_id is not None:
                until = at.microseconds + int(COOLDOWN.total_seconds() * 1_000_000)
                connection.execute(
                    "INSERT INTO cooldowns VALUES (?, ?) ON CONFLICT(user_id) "
                    "DO UPDATE SET until_us = MAX(until_us, excluded.until_us)",
                    (user_id, until),
                )
            return Result(Code.RESERVED, State.RESERVED)

        return await asyncio.to_thread(self._transaction, reserve)

    async def mark_dispatched(self, attempt_id: str, *, now: datetime) -> Result:
        """Commit before the API call. Repeated dispatch NEVER grants permission."""
        return await self._transition(attempt_id, State.DISPATCHED, now)

    async def complete(
        self, attempt_id: str, *, now: datetime, usage: TokenUsage | None = None,
        block_reason: BlockReason | None = None,
    ) -> Result:
        """Finalize state, numeric usage and any service block in one commit.

        First completion wins; duplicates cannot overwrite usage or lift a block.
        """
        if usage is not None and not isinstance(usage, TokenUsage):
            raise ValueError("Expected validated token usage.")
        if block_reason is not None and not isinstance(block_reason, BlockReason):
            raise ValueError("Expected a safe service block reason.")
        return await self._transition(attempt_id, State.COMPLETED, now, usage, block_reason)

    async def refund_undispatched(self, attempt_id: str, *, now: datetime) -> Result:
        """Refund only reserved state; preserve cooldown and the replay tombstone."""
        return await self._transition(attempt_id, State.REFUNDED, now)

    async def _transition(
        self, attempt_id: str, target: State, now: datetime,
        usage: TokenUsage | None = None, block_reason: BlockReason | None = None,
    ) -> Result:
        if not valid_attempt_id(attempt_id):
            return Result(Code.INVALID_IDENTIFIER)
        at = moment(now, self.timezone)
        if at is None:
            return Result(Code.INVALID_TIME)

        def transition(connection: sqlite3.Connection) -> Result:
            row = connection.execute(
                "SELECT state, reserved_us, dispatched_us FROM attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return Result(Code.NOT_FOUND)
            state = State(row[0])
            if state == target == State.COMPLETED:
                return Result(Code.ALREADY_COMPLETED, state)
            if state == target == State.REFUNDED:
                return Result(Code.ALREADY_REFUNDED, state)
            source = State.DISPATCHED if target == State.COMPLETED else State.RESERVED
            if state != source:
                return Result(Code.INVALID_TRANSITION, state)
            timestamp = max(at.microseconds, row[1], row[2] or row[1])
            if target == State.DISPATCHED:
                if block := self._block(connection):
                    return Result(Code.SERVICE_DISABLED, state, block_reason=block)
                connection.execute(
                    "UPDATE attempts SET state = 'dispatched', dispatched_us = ? WHERE attempt_id = ?",
                    (timestamp, attempt_id),
                )
                return Result(Code.DISPATCH_GRANTED, target)
            connection.execute(
                "UPDATE attempts SET state = ?, finished_us = ? WHERE attempt_id = ?",
                (target.value, timestamp, attempt_id),
            )
            if target == State.COMPLETED:
                counts = usage or TokenUsage()
                connection.execute(
                    "UPDATE attempts SET input_tokens = ?, output_tokens = ? WHERE attempt_id = ?",
                    (counts.input_tokens, counts.output_tokens, attempt_id),
                )
                if block_reason is not None:
                    connection.execute(
                        "UPDATE metadata SET ai_block = COALESCE(ai_block, ?) WHERE singleton = 1",
                        (block_reason.value,),
                    )
            return Result(Code.COMPLETED if target == State.COMPLETED else Code.REFUNDED, target)

        return await asyncio.to_thread(self._transaction, transition)

    async def status(self, *, now: datetime, user_id: str | None = None) -> Result:
        """Read a consistent, sanitized snapshot; never an admission decision."""
        if user_id is not None and not valid_discord_id(user_id):
            return Result(Code.INVALID_IDENTIFIER)
        at = moment(now, self.timezone)
        if at is None:
            return Result(Code.INVALID_TIME)
        return await asyncio.to_thread(
            self._transaction,
            lambda connection: Result(
                Code.OK, quota=self._status(connection, user_id, at),
                block_reason=self._block(connection),
            ),
            write=False,
        )

    @staticmethod
    def _status(connection: sqlite3.Connection, user_id: str | None, at: Moment) -> QuotaStatus:
        global_used = connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE day = ? AND state != 'refunded'", (at.day,)
        ).fetchone()[0]
        user_used = None
        cooldown = None
        if user_id is not None:
            user_used = connection.execute(
                "SELECT COUNT(*) FROM attempts WHERE user_id = ? AND day = ? AND state != 'refunded'",
                (user_id, at.day),
            ).fetchone()[0]
            row = connection.execute("SELECT until_us FROM cooldowns WHERE user_id = ?", (user_id,)).fetchone()
            cooldown = from_microseconds(row[0]) if row is not None else None
        return QuotaStatus(at.day, global_used, user_used, at.reset_at, cooldown)

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        # mode=rw is intentional: a lost database must not be silently recreated.
        connection = sqlite3.connect(
            self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
            uri=True, timeout=LOCK_TIMEOUT_SECONDS, isolation_level=None,
        )
        try:
            # DELETE + EXTRA syncs the journal's directory after unlink. WAL is
            # unnecessary for this small workload and introduces backup sidecars.
            connection.execute("PRAGMA synchronous = EXTRA")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise sqlite3.DatabaseError("Unsupported journal mode")
            return connection
        except BaseException:
            connection.close()
            raise

    def _verify(self, connection: sqlite3.Connection, *, version: int = SCHEMA_VERSION) -> None:
        if connection.execute("PRAGMA user_version").fetchone()[0] != version:
            raise sqlite3.DatabaseError("Unsupported schema")
        if connection.execute("SELECT timezone FROM metadata WHERE singleton = 1").fetchone() != (self.timezone.key,):
            raise sqlite3.DatabaseError("Usage timezone mismatch")
        # Partial/mismatched schemas must not admit even scheduled requests.
        connection.execute(
            "SELECT attempt_id, user_id, guild_id, day, state, reserved_us, "
            "dispatched_us, finished_us FROM attempts LIMIT 0"
        )
        connection.execute("SELECT user_id, until_us FROM cooldowns LIMIT 0")
        if version == 2:
            connection.execute("SELECT input_tokens, output_tokens FROM attempts LIMIT 0")
            self._block(connection)

    @staticmethod
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        for statement in _MIGRATION_2:
            connection.execute(statement)
        connection.execute("PRAGMA user_version = 2")

    def _transaction(
        self, operation: Callable[[sqlite3.Connection], Result], *,
        write: bool = True, new: bool = False,
    ) -> Result:
        try:
            with closing(self._connect(write=write)) as connection:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                # Close rolls back on EVERY failure, including commit failure.
                if not new:
                    self._verify(connection)
                result = operation(connection)
                connection.execute("COMMIT")
                # Never return a grant until COMMIT has succeeded.
                return result
        except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError):
            # Includes invalid persisted states/timestamps; never expose details.
            return Result(Code.STORAGE_UNAVAILABLE)

    def _initialize(self) -> Result:
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            new = False
        except OSError:
            return Result(Code.STORAGE_UNAVAILABLE)
        else:
            os.close(descriptor)
            new = True

        def initialize(connection: sqlite3.Connection) -> Result:
            if new:
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute("INSERT INTO metadata VALUES (1, ?)", (self.timezone.key,))
                self._migrate_v2(connection)
            else:
                if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise sqlite3.DatabaseError("Integrity failure")
            return Result(Code.OK)

        return self._transaction(initialize, new=new)
