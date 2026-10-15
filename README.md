# Rolfbot

Rolfbot is a small Python Discord bot project for a friend group across two
servers, with roughly 10–12 people in the larger server. Its intended personality
is friendly, playful and useful.

**Milestone 3 is implementation complete, pending authorized live smoke testing.**
The OpenAI integration is tested offline through the real SDK and mocked HTTP,
with persistent SQLite admission for every request. The live bot remains disabled:
Discord, the periodic scheduler and music are future work. No real API request
has been made, and supplying credentials does not start the bot.

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
│   ├── usage_store.py      # SQLite accounting, usage metadata and explicit migration
│   ├── openai_client.py    # Private SDK transport, bounded prompts and safe results
│   └── ai_service.py       # Public generation interface with mandatory admission
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

Direct runtime dependencies are **openai 3.31.0** and **httpx2 2.13.1**. The SDK
uses HTTPX2; it is also declared directly because the application configures its
timeouts, transport and connection limits. SQLite remains standard-library only.
Timezone validation uses OS IANA data. Development uses pytest **9.1.1** and the
build backend is setuptools **84.0.0**. The full runtime/build/test dependency
closure is pinned in [requirements-dev.lock](requirements-dev.lock), including
pydantic 2.14.1 and anyio 4.15.1.
[Official SDK release](https://github.com/openai/openai-python/releases/tag/v3.31.0),
[Python SDK reference](https://developers.openai.com/api/reference/python).

No dotenv loader, Discord SDK, music dependency or application telemetry is included.

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
credentials or validate future channel settings. Model/effort allowlists are
validated locally. `python -m rolfbot run`
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
Milestone 2's 142-test baseline is retained, with assertions updated for the new
dependency/configuration contract. Milestone 3 adds real-SDK mocked-HTTP tests,
usage migration, provider errors, backoff, cancellation and output/privacy checks.
All simulated sends assert a fresh committed SQLite dispatch, and test network
tripwires remain active. No SDK test has a separate unaccounted generation path.
**258 tests pass on each of CPython 3.12.15 and 3.13.16**; compilation, dependency
installation/`pip check` and `git diff --check` also pass. This validates the
offline implementation, not provider access, billing attribution or response quality.

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
| `OPENAI_MODEL` | `gpt-6-luna`; the only approved model |
| `OPENAI_REASONING_EFFORT` | `none`; the only approved effort |
| `OPENAI_API_KEY` | Optional offline; required and revalidated when explicitly constructing `AIService` |
| `DISCORD_BOT_TOKEN` | Optional, unused; captured for redaction and later conditional validation |

Missing credentials and example placeholders are safe for offline use. The
configuration loader has explicit `require_openai`/`require_discord` validation
parameters for integration components: required credentials must be nonempty,
contain no whitespace and not be obvious placeholders. These parameters do not
create or enable clients, and no CLI command requests them today. Credential
format checks do not establish remote validity. Errors name the setting without
printing its value; settings representations omit credentials and the database path.

`ROLFBOT_GUILD_CHANNELS` remains future-only and ignored. Unapproved model/effort
values fail safely, including during `check-config`. No configuration can increase
the output cap, enable retries or bypass quotas. Client construction rejects
`OPENAI_CUSTOM_HEADERS` and `OPENAI_LOG`; SDK/transport raw logging is suppressed.
Explicit client settings fix the official API endpoint and key, omit ambient
organization/project/admin credentials, and disable proxy-environment lookup and
redirect following. There is no provider, model, key or reasoning fallback.

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

**The OpenAI integration sets `max_output_tokens=512` and disables SDK retries.**
SQLite enforces request allowances; it does not enforce a dollar cap.
New retries must reserve their own capacity. User-provided time/identity must
never be trusted: future handlers supply trusted server time and Discord IDs,
and check guild/channel access before reservation.

`UsageStore` exposes async methods. Each call opens and closes its own connection
in an `asyncio.to_thread` worker; no storage work runs on the Discord event loop.
Lock waits are limited to **250 ms per SQLite lock wait**, with no automatic retry.
This is not a hard filesystem-operation deadline. `AIService` limits concurrent
work to two requests with no waiting queue. Cancellation can leave a worker's transaction
committed; a cancelled or lost result gives no permission to dispatch or replay.

| API | Contract |
| --- | --- |
| `validate_input(text)` | Pure validation; runs before any storage operation in user reservation |
| `await store.initialize()` | Explicit provisioning of a new version-2 file, or validation of an existing version-2 schema |
| `await store.migrate()` | Explicit, repeat-safe version-1 to version-2 migration of existing storage |
| `await store.reserve_user(user_id, attempt_id, text, now=..., guild_id=...)` | Atomically reserve both allowances and establish cooldown; only `Code.RESERVED` can proceed |
| `await store.reserve_scheduled(attempt_id, now=...)` | Reserve global capacity only; caller skips/defers denied jobs |
| `await store.mark_dispatched(attempt_id, now=...)` | Persist dispatch **before** the API call; only a fresh `Code.DISPATCH_GRANTED` authorizes sending |
| `await store.complete(attempt_id, now=..., usage=..., block_reason=...)` | Atomically finalize state, reported token counts and any provider block; repeat returns `ALREADY_COMPLETED` without overwriting usage |
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

The version-2 schema uses `attempts` as the source of truth: every state except
`refunded` counts. A primary key prevents ID reuse; indexes support daily global
and personal counts. `cooldowns` persists user expiry independently of refunds;
`metadata` fixes the accounting timezone and stores any provider block.
`PRAGMA user_version` rejects unknown
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

## OpenAI service contract

Create **one `AIService(settings, store)` per process/event loop**, shared by all
future user handlers and scheduled jobs. Creation validates the explicit key and
constructs an `AsyncOpenAI` client, but sends nothing. Imports, settings loading
and every CLI command leave the SDK uninitialized. Use the service as an async
context manager, or await `aclose()` during shutdown; it stops admission, cancels
active work and closes HTTP resources. A cancelled shutdown caller still waits
for owned cleanup. The future Discord lifecycle must own this service.

The only public generation methods are:

- `await service.generate_user(user_id, attempt_id, text, guild_id=..., now=...)`
- `await service.generate_scheduled(attempt_id, now=...)`

Omit `now` to use the injected/default UTC clock, or supply trusted server time.
Scheduled input is a fixed bounded prompt, not an arbitrary user impersonation.
Transport injection is for offline tests, below the same admission and request
construction code. Do not use private transport methods in application handlers.

The sequence is validate → claim an immediate service slot → reserve SQLite
capacity → prepare the request → commit dispatch → send once → persist completion
and token counts. Only the exact `Code.DISPATCH_GRANTED` enum permits a send.
Two slots cover the whole generation lifecycle, including SQLite work; excess
requests return `BUSY` before consuming quota. There is no internal waiting queue.
Local preparation failure can refund a confirmed reservation while retaining its
cooldown. Once dispatch has started, cancellation, denial or an ambiguous result
never refunds. A lost reservation result is also retained conservatively.

Each request uses `gpt-6-luna`, `reasoning={"effort": "none"}`, a fixed 512-output-
token cap, `store=False`, `stream=False`, `background=False`, an empty tool list,
`tool_choice="none"`, plain-text format and disabled input truncation. It carries
one validated user message plus a concise centralized personality instruction.
There are no history, conversation IDs, tools, searches or continuation calls.
The persona is friendly and playful, keeps answers short and admits its lack of
live information. See the [model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna)
and [Responses reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

The SDK has `max_retries=0`. Network timeouts are 5 seconds connect, 25 seconds
read, 5 seconds write and 1 second pool acquisition, with a **30-second overall
network-attempt deadline**. SQLite admission/finalization adds its short bounded
lock waits outside that deadline. No timeout, error or incomplete response causes
another API call. A future retry must be a new explicitly admitted attempt.

Only completed assistant text is accepted. Output is bounded to **1,800 UTF-16
code units**, including astral characters, with CRLF normalized to LF. Invalid
Unicode, unsafe controls, directional overrides, unexpected output types,
refusals, empty output and incomplete/oversized responses produce safe categories
without continuation. Discord mention suppression and message sending remain
Milestone 4 work; no Discord API is imported here.

Results contain an `Outcome`, optional validated text, safe admission denial,
numeric usage and an `accounting` result. Response text is excluded from result
representations. Failed completion persistence never repeats generation: a
successful text may still be returned with `accounting=STORAGE_UNAVAILABLE`, while
the dispatched reservation continues counting. Handlers must never turn this
accounting warning into an automatic generation retry.

Transient failures use a monotonic service cooldown of **30, 60, 120, 240, then
300 seconds**, capped there, without automatically sending anything. Success
resets the failure streak but cannot lift an existing cooldown or permanent
block. Authentication, permission, invalid request/model configuration, project/
organization spend limits, exhausted credits and generic billing quota failures
disable the service. The block is committed with completion in `metadata` and
checked by both reservation and dispatch; it survives restart. At most two
already-started attempts can be in flight when a failure is observed.

Recovery is deliberate: close the service, resolve the owner-managed cause, then
explicitly call `await store.clear_ai_block()` and recreate the service. No CLI
or ordinary request clears a block. If the completion write fails, the process
still blocks locally, but cannot promise disk persistence of that block or usage;
inspect/reconcile storage before restarting. Transient cooldown state alone is
in memory and resets on restart. Persistent request quotas never reset this way.

Valid reported input/output token counts are stored as nullable integers on the
attempt in the completion transaction. Missing/invalid counts remain `NULL`,
including ambiguous timeouts; a reported zero stays zero. Parsing preserves raw
JSON numeric types so booleans/strings cannot become fabricated counts. Duplicate
completion cannot overwrite the first result. Counts are estimates for review,
not a currency ledger or replacement for the owner's spending controls.

Logs contain only a derived attempt correlation, outcome, fixed model, duration,
token counts, safe HTTP status and accounting result. Raw prompts, response bodies,
provider errors, headers and user IDs are not logged. `store=False` disables
response storage for later retrieval, but **does not guarantee no provider-side
abuse-monitoring retention**. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data).

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
to an empty file. Retention and production recovery tooling remain future
operational work.

For an existing Milestone 2 database, stop all writers, make a consistent backup,
then explicitly run `await store.migrate()` from offline maintenance code. The
single `BEGIN IMMEDIATE` migration adds nullable `input_tokens`/`output_tokens`
and a nullable `metadata.ai_block`, then advances `user_version` from 1 to 2.
It preserves every attempt, timestamp, cooldown, index and timezone, rolls back
all changes on failure, and is repeat-safe on version 2. `initialize()` does not
silently migrate an old database; regular admission refuses it until migration.
The tests construct actual v1 SQLite files from the frozen Milestone 2 SQL fixture.

Milestone 2 binaries reject schema 2. Rollback therefore requires a compatible
application release; there is no automatic downgrade. Do not restore a stale v1
backup over new usage to make old code start. Deployment must rehearse migration
and recovery with the actual storage before enabling live requests.

## Next milestones

The [roadmap](docs/ROADMAP.md) preserves the intended features:

- An explicitly authorized live smoke test of model access, project attribution
  and response quality; offline validation cannot establish those facts.
- Wire the implemented AI service into both servers without bypassing admission.
- Proper Discord slash commands and spontaneous messages approximately every
  2–7 days; generated scheduled messages share the global API allowance.
- Optional voice music, subject to the [YouTube research](docs/YOUTUBE_RESEARCH.md)
  and future host-specific tests.
- Tested release/deployment and owner-managed billing safeguards before live use.

**Next: Milestone 4, Discord integration, alongside the separately authorized
live smoke-test gate for Milestone 3.**
The owner reports that the Rolfbot project, intended GPT-6 Luna model, restricted
key, $2 enforced monthly limit, $1 alert and $5 prepaid credit with auto-recharge
disabled are configured. These account settings were not inspected or changed.
Live API tests still require separate authorization. Music, Discord, scheduling
and deployment remain disabled. Temporary media will belong under `downloads/`;
intentional assets and fixtures belong in `assets/` and `tests/fixtures/`.
