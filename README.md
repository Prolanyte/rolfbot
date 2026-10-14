# Rolfbot

Rolfbot is a small Python Discord bot project for a friend group across two
servers, with roughly 10–12 people in the larger server. Its intended personality
is friendly, playful and useful.

**Milestones 1 and 2 deliver an offline foundation with persistent usage protection.**
SQLite quotas, cooldowns and reservation recovery are implemented and tested.
The live bot remains disabled: OpenAI, Discord connections, scheduled messages
and music are future work. Supplying credentials does not enable them.

## Architecture

```text
.
├── pyproject.toml          # Package metadata, supported Python and direct dependencies
├── requirements-dev.lock   # Exact build/test dependency versions
├── .env.example            # Safe defaults and credential placeholders
├── src/rolfbot/
│   ├── __init__.py         # Side-effect-free package
│   ├── __main__.py         # python -m rolfbot
│   ├── cli.py              # Offline commands, exit codes and asyncio lifecycle
│   ├── config.py           # Typed settings and conditional credential validation
│   ├── logging_setup.py    # Consistent, redacted stderr logging
│   ├── usage.py            # Limits, input validation and structured results
│   └── usage_store.py      # Async interface to transactional SQLite accounting
├── tests/                  # Offline pytest suite
└── docs/
    ├── ROADMAP.md
    └── YOUTUBE_RESEARCH.md
```

The broken `rolfbot7.py` was removed; its source remains in Git history. No
obsolete OpenAI or YouTube implementation was copied into the new package.
Imports do not start an event loop, configure logging, open databases or connect
to services. No third-party runtime dependency was added for usage protection.

## Python and dependencies

Supported target: **Linux with CPython 3.12 or 3.13**. Python 3.12 is the preferred
future production baseline because [Ubuntu 24.04 provides it](https://ubuntu.com/developers/docs/reference/availability/python/).
The foundation was tested locally on Linux x86_64 with **3.12.15 and 3.13.16**;
this is not a claim of VM/deployment validation. Other Python minor versions are
outside the declared range until tested.

Runtime dependencies are **standard library only**. Timezone validation uses the
operating system's IANA timezone database. Development uses pytest **9.1.1**;
the build backend is setuptools **84.0.0**. All transitive test dependencies are
pinned in [requirements-dev.lock](requirements-dev.lock). The planned Discord
and OpenAI packages currently declare Python minimums compatible with 3.12,
but those SDKs have not been installed or integration-tested in this milestone.
[Discord package metadata](https://pypi.org/project/discord.py/),
[OpenAI package metadata](https://pypi.org/project/openai/).

No dotenv loader, Discord/OpenAI SDK, music dependency or telemetry is included.

## Developer setup

Use an installed Python 3.12 or 3.13 with `venv`, pip and OS timezone data. Run
these commands from the repository root; on Bazzite with Python 3.13, substitute
`python3.13` in the first command. Keep dependencies inside the virtual environment.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-build-isolation --no-deps -e .
python -m pip check
```

The lock installs the exact build backend and test packages before the editable
install, so that step needs no dependency resolution. Initial dependency downloads
require package-index access; tests and diagnostics do not. For a normal wheel
installation, use `.` instead of `-e .` in the second install command. Both
installation forms were verified in isolated environments. No global packages
or host settings are needed by the application.

[pyproject.toml](pyproject.toml) is the source for direct/build requirements;
`dev` contains the direct test dependency. When updating dependencies, regenerate
the lock in a clean environment containing only the declared build/test packages,
review transitive changes and rerun the suite on both supported Python versions.
The lock pins package versions, not an entire OS or interpreter distribution.

## Commands and tests

With the environment activated:

```sh
python -m rolfbot --help
python -m rolfbot check-config
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m compileall -q src tests
```

`check-config` validates local settings and exits. It needs no credentials,
creates no database and performs no network checks. It does not authenticate
credentials or validate future model/channel settings. `python -m rolfbot run`
always refuses startup with an explanation; there is no daemon mode yet.

| Exit code | Meaning |
| --- | --- |
| 0 | Help or successful offline configuration check |
| 1 | Unexpected internal failure; sensitive details omitted |
| 2 | Invalid arguments or configuration |
| 3 | Bot runtime intentionally unavailable |
| 130 | SIGINT or task cancellation |
| 143 | SIGTERM cancellation |

The CLI owns an asyncio loop, handles SIGTERM cancellation, allows SIGINT to
cancel work, restores signal handlers and logs shutdown in a `finally` block.
It is a main-thread Linux entry point and does not rely on terminal interaction.
Later long-running components must put their own resource cleanup inside this
lifecycle; no such components are started now.

The offline suite covers configuration, credential/log redaction, imports, CLI
behavior, refused startup and SIGINT/SIGTERM cleanup, plus real file-backed
SQLite tests for quota boundaries, cooldowns, refunds, restart/crash recovery,
independent-connection races, lock/commit failures, corruption and cancellation.
Injected timestamps cover Oslo midnight, month/year changes, both DST transitions
and backward clock changes. Tests use temporary databases, clear service
configuration, block socket connections/DNS, and guard subprocess network access.
Plugin autoload is disabled by the command above. No paid or live service tests
are included. Run only the usage tests with `python -m pytest -q tests/test_usage*.py`.
Milestone 2 validation passed **142 tests on each of CPython 3.12.15 and 3.13.16**.

## Configuration

Only explicitly supported **process environment variables** are read. There is
no automatic `.env` loading or file discovery. [.env.example](.env.example) is a
reference, not a required setup step. For example:

```sh
ROLFBOT_LOG_LEVEL=DEBUG ROLFBOT_TIMEZONE=UTC python -m rolfbot check-config
```

| Implemented variable | Default and validation |
| --- | --- |
| `ROLFBOT_LOG_LEVEL` | `INFO`; DEBUG, INFO, WARNING, ERROR or CRITICAL, case-insensitive |
| `ROLFBOT_TIMEZONE` | `Europe/Oslo`; must be an installed IANA timezone, such as `UTC` |
| `ROLFBOT_DATABASE_PATH` | Unset offline; explicit absolute file path required to construct usage storage. Syntax validation only; no file access during configuration checks |
| `OPENAI_API_KEY` | Optional, unused; captured for redaction and later conditional validation |
| `DISCORD_BOT_TOKEN` | Optional, unused; captured for redaction and later conditional validation |

Missing credentials and example placeholders are safe for offline use. The
configuration loader has explicit `require_openai`/`require_discord` validation
parameters for future components: required credentials must be nonempty,
contain no whitespace and not be obvious placeholders. These parameters do not
create or enable clients, and no CLI command requests them today. Credential
format checks do not establish remote validity. Errors name the setting without
printing its value; settings representations omit credentials and the database path.

`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT` and
`ROLFBOT_GUILD_CHANNELS` are **future-only and currently ignored**. The approved
future model remains `gpt-6-luna`, initially with reasoning effort `none`; no
model selection, fallback or guild mapping exists in this code.

Application logs use the `rolfbot` logger namespace and one consistently formatted
stderr line per event, suitable for journald. Known configured credential values
are redacted after message formatting. Tracebacks and exception details are
omitted even at DEBUG; usage input is never stored or logged. Redaction is a defense
against accidental exposure, not permission to log arbitrary sensitive content.
The CLI never dumps the environment or a complete configuration object.

Keep credentials in protected environment/secrets storage outside version
control. A future systemd service will supply environment variables directly;
it must not depend on a repository `.env` file. Never store payment details,
cookie exports or real keys in tracked examples. Review staged changes and
rotate exposed credentials; `.gitignore` is not secret storage.

## Persistent usage protection

| Implemented protection | Policy |
| --- | --- |
| Personal allowance | 20 reservations per Discord user ID per local calendar day, across both guilds |
| Global allowance | 100 reservations per day, including scheduled generations |
| Cooldown | 30 seconds per user, retained after refunds, midnight and restart |
| Input | 1–1,000 Unicode code points; whitespace-only, NUL and surrogate-containing strings rejected |
| Dispatch | At most one granted dispatch transition for each unique attempt ID |

**The future OpenAI integration must set `max_output_tokens=512` and disable SDK
retries. SQLite does not enforce output generation parameters or a dollar cap.**
New retries must reserve their own capacity. User-provided time/identity must
never be trusted: future handlers supply trusted server time and Discord IDs,
and check guild/channel access before reservation.

`UsageStore` exposes async methods. Each call opens and closes its own connection
in an `asyncio.to_thread` worker; no storage work runs on the Discord event loop.
Lock waits are limited to **250 ms per SQLite lock wait**, with no automatic retry.
This is not a hard filesystem-operation deadline. Future integration must also
bound concurrent/waiting requests. Cancellation can leave a worker's transaction
committed; a cancelled or lost result gives no permission to dispatch or replay.

| API | Contract |
| --- | --- |
| `validate_input(text)` | Pure validation; runs before any storage operation in user reservation |
| `await store.initialize()` | Explicit provisioning of a new file, or validation of an existing version-1 schema |
| `await store.reserve_user(user_id, attempt_id, text, now=..., guild_id=...)` | Atomically reserve both allowances and establish cooldown; only `Code.RESERVED` can proceed |
| `await store.reserve_scheduled(attempt_id, now=...)` | Reserve global capacity only; caller skips/defers denied jobs |
| `await store.mark_dispatched(attempt_id, now=...)` | Persist dispatch **before** the API call; only a fresh `Code.DISPATCH_GRANTED` authorizes sending |
| `await store.complete(attempt_id, now=...)` | Finalize dispatched success or failure; retains capacity; repeat returns `ALREADY_COMPLETED` |
| `await store.refund_undispatched(attempt_id, now=...)` | Release reserved capacity only; repeat returns `ALREADY_REFUNDED`; cooldown remains |
| `await store.status(now=..., user_id=...)` | Read-only snapshot of counts, reset time and optional user cooldown; never permission to send |

All timestamps are explicit, timezone-aware datetimes, preferably UTC. Naive,
nonexistent local times and UTC/local years outside 1970–9998 return `INVALID_TIME`.
During autumn's repeated hour, local datetimes use `fold` to identify the instant.
Store timestamps as integer UTC microseconds; derive day keys in the configured
timezone (default Europe/Oslo), without a reset job. Existing cooldown expiry is
compared against UTC, so moving the clock backward extends the wait. Lifecycle
audit timestamps never precede earlier transitions. Completed/refunded attempts
always retain their original reservation day. Large forward clock jumps still
require operator attention; the clock is trusted infrastructure.

Discord user/guild IDs must be canonical positive unsigned 64-bit decimal
**strings**. Guild context is optional and never part of the quota key. Attempt
IDs share one namespace across users and jobs: 1–128 ASCII letters, digits,
`_ . : -`, beginning with a letter/digit. Use stable interaction/job generation
IDs, not fresh random IDs on redelivery. No input text or generated output is
stored. Results contain typed codes, optional lifecycle state, quota snapshots
and `next_eligible_at` for cooldown/quota denials, without IDs, paths or raw
exceptions. Next eligibility is advisory because other requests can use capacity.

The version-1 schema uses `attempts` as the source of truth: every state except
`refunded` counts. A primary key prevents ID reuse; indexes support daily global
and personal counts. `cooldowns` persists user expiry independently of refunds;
`metadata` fixes the accounting timezone. `PRAGMA user_version` rejects unknown
versions. Changing the configured timezone of an existing database fails closed
and requires a deliberate future migration, not a new quota bucket.

Every mutation uses a short `BEGIN IMMEDIATE` transaction, with checks, reservation
and cooldown committed together. Dispatch grants are returned only after commit.
Status reads use a consistent read transaction. No transaction remains open for
API/Discord work. The tested configuration uses DELETE rollback journaling and
`synchronous=EXTRA`, including directory synchronization after journal removal.
WAL is unnecessary at this scale and would add backup sidecar handling. See
[SQLite transactions](https://www.sqlite.org/lang_transaction.html) and
[SQLite synchronous settings](https://www.sqlite.org/pragma.html#pragma_synchronous).
Local concurrency tests validate locking behavior, not VM storage or power-loss
guarantees; production needs reliable local storage and filesystem locking.

Reservations survive a crash even before dispatch. Dispatched, completed,
timed-out and ambiguous attempts remain charged; failed Discord delivery also
cannot earn a refund. No record expires or replays automatically. Under the
required persist-before-send protocol, only `reserved` establishes that nothing
was dispatched and can be refunded for local abandonment. Duplicate dispatch is
denied, and dispatch/refund races cannot both succeed. Keep every record,
including refunded tombstones, to prevent replay; no retention cleanup is
implemented. All storage/lock/schema failures return `STORAGE_UNAVAILABLE` and
deny access. A lost acknowledgement may retain capacity conservatively.

## Database location and recovery

Set `ROLFBOT_DATABASE_PATH` to an absolute file path, for example
`/var/lib/rolfbot/usage.sqlite3` in future production. Keep it **outside release,
checkout and virtual-environment directories**, in a protected directory owned by
the service account. Tests use temporary files; optional local development state
belongs in ignored `runtime/`. Configuration checks validate path syntax only,
so they do not prove storage is writable. No command creates a database today.

Explicit one-time provisioning from async application/setup code is:

```python
from rolfbot.config import load_settings
from rolfbot.usage import Code
from rolfbot.usage_store import UsageStore

settings = load_settings()  # ROLFBOT_DATABASE_PATH must be explicitly supplied
store = UsageStore(settings.database_path, timezone=settings.timezone)
result = await store.initialize()  # Deliberate provisioning, not automatic recovery
assert result.code == Code.OK
```

The parent directory must exist; a new database is created with mode `0600`.
Existing empty, corrupt or incompatible files are refused, never reset. Normal
operations require an existing database and cannot recreate a missing one.
**Never automatically provision a missing production database on startup**: first
validate existing state with `status`, and stop AI work if it is unavailable.
Only the operator can decide whether first-time provisioning is appropriate.
Interrupted provisioning may leave an unusable file requiring review.

Back up accounting and cooldowns together using SQLite's backup API, or stop all
writers cleanly before copying the database. Do not copy a live database file
alone or delete a recovery journal. Protect backups because IDs are private.
Test restoration and schema compatibility before deployment. Restoring an older
snapshot can reopen quota capacity and permit duplicate attempts: disable AI
until missing usage is reconciled. Never recover by deleting state or switching
to an empty file. Migration, retention and production recovery tooling remain
future operational work.

## Next milestones

The [roadmap](docs/ROADMAP.md) preserves the intended features:

- Helpful, playful AI replies in both servers, using asynchronous OpenAI Responses.
- Wire the implemented usage barrier into every future AI entry point, with a
  512-token output cap enforced in OpenAI request parameters.
- Proper Discord slash commands and spontaneous messages approximately every
  2–7 days; generated scheduled messages share the global API allowance.
- Optional voice music, subject to the [YouTube research](docs/YOUTUBE_RESEARCH.md)
  and future host-specific tests.
- Tested release/deployment and owner-managed billing safeguards before live use.

**Next: Milestone 3, bounded OpenAI integration with offline mocked tests.**
The owner reports that the Rolfbot project, intended GPT-6 Luna model, restricted
key, $2 enforced monthly limit, $1 alert and $5 prepaid credit with auto-recharge
disabled are configured. These account settings were not inspected or changed.
Live API tests still require separate authorization. Music, Discord, scheduling
and deployment remain disabled. Temporary media will belong under `downloads/`;
intentional assets and fixtures belong in `assets/` and `tests/fixtures/`.
