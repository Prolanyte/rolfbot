-- Frozen Milestone 2 schema, captured before the version-2 migration.
CREATE TABLE metadata (singleton INTEGER PRIMARY KEY CHECK(singleton = 1), timezone TEXT NOT NULL);
CREATE TABLE attempts (
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
    );
CREATE INDEX global_usage ON attempts(day) WHERE state != 'refunded';
CREATE INDEX user_usage ON attempts(user_id, day) WHERE state != 'refunded';
CREATE TABLE cooldowns (user_id TEXT PRIMARY KEY NOT NULL, until_us INTEGER NOT NULL);
INSERT INTO metadata VALUES (1, 'Europe/Oslo');
PRAGMA user_version = 1;
