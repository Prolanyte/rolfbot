# Rolfbot

Rolfbot is a small Python Discord bot project for a friend group across two
servers, with roughly 10–12 people in the larger server. Its intended personality
is friendly, playful and useful.

**The owner reports successful local live smoke, slash-command, mention and
reply-chain tests**, including conversation isolation and cooldown enforcement.
Natural mentions and bounded conversation memory are implemented and verified
offline. **Gate B was approved and Rolfbot is running on Vultr**, activated on
**16 October 2026 at 00:09 Europe/Oslo**. Discord connection and command sync
succeeded. Historical SQLite accounting was preserved; production SQLite is now
authoritative and the local instance must remain stopped. Owner live acceptance,
boot enablement and one controlled restart passed. See
[DEPLOYMENT.md](docs/DEPLOYMENT.md) for release and operational evidence.
Both interaction paths use the same OpenAI service and persistent SQLite admission.
The owner confirmed slash, mention/reply, fresh-context and channel-isolation
checks; four successful requests are recorded in production SQLite. Project
attribution and billing controls remain owner-managed.
Scheduling and music remain inactive; only the approved guild/channel is enabled.
The [local test runbook](docs/LOCAL_TEST.md) remains the reference for local checks.
Do not start the local bot against its older database while production is active.

## Architecture

```text
.
├── pyproject.toml          # Package metadata, supported Python and direct dependencies
├── requirements-dev.lock   # Exact build/test dependency versions
├── .env.example            # Safe defaults and credential placeholders
├── src/rolfbot/
│   ├── __init__.py         # Side-effect-free package
│   ├── __main__.py         # python -m rolfbot
│   ├── cli.py              # Explicit commands, exit codes and asyncio lifecycle
│   ├── runtime.py          # Storage checks, opt-in smoke test and client ownership
│   ├── discord_bot.py      # Allowlisted /rolfbot command and Discord lifecycle
│   ├── discord_messages.py # Explicit mentions/replies through the shared AI service
│   ├── conversation.py     # Bounded ephemeral reply snapshots and context validation
│   ├── config.py           # Typed settings and conditional credential validation
│   ├── logging_setup.py    # Consistent, redacted stderr logging
│   ├── usage.py            # Limits, input validation and structured results
│   ├── usage_store.py      # SQLite accounting, usage metadata and explicit migration
│   ├── openai_client.py    # Private SDK transport, bounded prompts and safe results
│   └── ai_service.py       # Public generation interface with mandatory admission
├── tests/                  # Offline pytest suite
└── docs/
    ├── ROADMAP.md
    ├── LOCAL_TEST.md
    └── YOUTUBE_RESEARCH.md
```

The broken `rolfbot7.py` was removed; its source remains in Git history. No
obsolete OpenAI or YouTube implementation was copied into the new package.
Imports do not start an event loop, configure logging, open databases or connect
to services. No third-party runtime dependency was added for usage protection.

## Python and dependencies

Supported target: **Linux with CPython 3.12 or 3.13**. Python 3.12 is the preferred
production baseline because [Ubuntu 24.04 provides it](https://ubuntu.com/developers/docs/reference/availability/python/).
The foundation was tested locally on Linux x86_64 with **3.12.15 and 3.13.16**;
the 432 application tests also pass on the staged Ubuntu VM with **3.12.3**.
Production live acceptance and restart validation also passed. Other Python minor versions are
outside the declared range until tested.

Direct runtime dependencies are **openai 3.31.0**, **httpx2 2.13.1** and
**discord.py 2.7.1**. The OpenAI SDK
uses HTTPX2; it is also declared directly because the application configures its
timeouts, transport and connection limits. SQLite remains standard-library only.
Timezone validation uses OS IANA data. Development uses pytest **9.1.1** and the
build backend is setuptools **84.0.0**. The full runtime/build/test dependency
closure is pinned in [requirements-dev.lock](requirements-dev.lock), including
pydantic 2.14.1, anyio 4.15.1 and aiohttp 3.14.5. Discord's dependency
`audioop-lts 0.2.2` is installed only on Python 3.13; Python 3.12 supplies audioop.
Tests narrowly ignore the SDK's Python 3.12 audioop deprecation warning; all
other warnings remain errors. No voice feature is enabled.
[Official SDK release](https://github.com/openai/openai-python/releases/tag/v3.31.0),
[Python SDK reference](https://developers.openai.com/api/reference/python),
[discord.py release](https://github.com/Rapptz/discord.py/releases/tag/v2.7.1).

No dotenv loader, music playback dependencies or application telemetry is included.

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
credentials or open storage. Model/effort allowlists and any supplied channel
mapping are validated locally. The explicit additional commands are:

| Command | Behavior |
| --- | --- |
| `python -m rolfbot init-db` | Offline first-time provisioning, or validation of existing schema 2; requires an external persistent path and existing parent directory |
| `python -m rolfbot smoke-test --confirm-billable` | One fixed-prompt, quota-protected OpenAI attempt; requires a key and existing usable database; never runs automatically |
| `python -m rolfbot run` | Start the minimal Discord bot after validating credentials, mappings and existing storage |

The smoke flag must be spelled in full. The smoke test uses synthetic user ID
`1`, a fresh `smoke-<UUID>` attempt ID, and normal personal/global quotas and
cooldown. It prints JSON containing a safe outcome, denial/accounting codes,
validated response text and reported token counts (`null` means unknown).
There are no model retries, fallbacks or continuation calls. The command can
make zero API calls if admission denies it. Only the owner should run the live
steps in the [runbook](docs/LOCAL_TEST.md).

| Exit code | Meaning |
| --- | --- |
| 0 | Successful command or help |
| 1 | Unexpected internal failure; sensitive details omitted |
| 2 | Invalid arguments or configuration |
| 3 | Storage/service-block startup refusal, or Discord connection/setup failure |
| 4 | Smoke test denied/failed, or completion accounting not confirmed |
| 130 | SIGINT or task cancellation |
| 143 | SIGTERM cancellation |

The CLI owns an asyncio loop, handles SIGTERM cancellation, allows SIGINT to
cancel work, restores signal handlers and logs shutdown in a `finally` block.
It is a main-thread Linux entry point and does not rely on terminal interaction.
The Discord lifecycle owns one AI service. Shutdown stops admission, cancels
interaction/message work, joins typing tasks, clears conversation memory, and
closes OpenAI and Discord resources.

The offline suite covers configuration, credential/log redaction, imports, CLI
behavior, invalid-startup refusal and SIGINT/SIGTERM cleanup, plus real file-backed
SQLite tests for quota boundaries, cooldowns, refunds, restart/crash recovery,
independent-connection races, lock/commit failures, corruption and cancellation.
Injected timestamps cover Oslo midnight, month/year changes, both DST transitions
and backward clock changes. Tests use temporary databases, clear service
configuration, block socket connections/DNS, and guard subprocess network access.
Plugin autoload is disabled by the command above. No paid or live service tests
are run by pytest. Run only the usage tests with `python -m pytest -q tests/test_usage*.py`.
Milestone 2's 142-test baseline is retained, with assertions updated for the new
dependency/configuration contract. Milestone 3 adds real-SDK mocked-HTTP tests,
usage migration, provider errors, backoff, cancellation and output/privacy checks.
All simulated sends assert a fresh committed SQLite dispatch, and test network
tripwires remain active. No SDK test has a separate unaccounted generation path.
**432 tests pass on each of Python 3.12.15 and 3.13.16**, retaining the original
352-test MVP baseline. The MVP adds real
Discord command-tree invocation with mocked interactions,
opt-in smoke tests through mocked HTTP, startup/storage failure tests, mention
suppression, defer/delivery deadlines and owned shutdown. The conversation tests
add authenticated mentions, multi-user reply branches, scope isolation, expiry,
LRU eviction, UTF-8 budgets, shared quotas, duplicate events, typing cleanup and
unconfirmed delivery. See the
[verification record](docs/LOCAL_TEST.md#verification-and-release-record) for
the final test counts, dependency versions and release SHA status. Offline
evidence does not establish provider access, billing attribution or Discord delivery.

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
| `OPENAI_API_KEY` | Optional offline; required for `smoke-test` and `run`, revalidated when constructing `AIService` |
| `DISCORD_BOT_TOKEN` | Optional offline and for smoke testing; required for `run` |
| `ROLFBOT_GUILD_CHANNELS` | Optional offline and for smoke testing; required for `run`; JSON object mapping guild ID strings to nonempty lists of channel ID strings |

Missing credentials and example placeholders are safe for offline use. The
configuration loader has explicit `require_openai`/`require_discord` validation
parameters for integration components: required credentials must be nonempty,
contain no whitespace and not be obvious placeholders. These parameters do not
create or enable clients. `run` requires both credentials; `smoke-test` only the
OpenAI key. Credential
format checks do not establish remote validity. Errors name the setting without
printing its value; settings representations omit credentials and the database path.

For example, a synthetic one-guild mapping is `{"111111111111111111":["222222222222222222"]}`.
Add a second entry to enable the second guild; no source changes are required.
IDs must be canonical positive 64-bit decimal strings. Duplicate guild/channel
entries, empty mappings and non-string IDs fail safely. Real IDs belong only in
private configuration and are omitted from settings representations.

Unapproved model/effort values fail safely, including during `check-config`. No configuration can increase
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
never be trusted: the Discord handler supplies trusted server time and Discord IDs,
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
| `await store.validate()` | Check existing writable storage, integrity, schema, timezone and block state; never create or migrate |
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

## Minimal Discord behavior

Only `/rolfbot message:<text>` is registered, as a guild command in each
configured guild. Runtime checks also reject DMs, bots, unconfigured guilds or
channels, and mismatched guild/channel pairs before usage admission. This MVP
supports ordinary guild text channels; threads and other channel types are
rejected. It uses the authenticated interaction user/guild IDs and the interaction
ID as the unique usage attempt ID. User allowances are shared across guilds.

The bot requests the nonprivileged `guilds` and `guild_messages` intents.
Message Content, members, presences, DM messages and voice-state intents remain
disabled, as does the SDK message cache. Use the existing bot account with `bot`
and `applications.commands` scopes, View Channel, Send Messages and **Read Message
History** in AI channels, and permission for users to invoke application commands.
Discord requires Read Message History to **send a reply**; Rolfbot never fetches
history. Administrator and privileged intents are unnecessary.
[Discord reply permissions](https://docs.discord.com/developers/resources/message#create-message).

Allowed interactions are deferred immediately with a two-second local timeout,
before database or OpenAI work. Failed, timed-out, already-answered or expired
interactions cannot start generation. Discord requires initial acknowledgement
within three seconds; the 30-second AI deadline fits inside its later response
window. [Discord interaction deadlines](https://docs.discord.com/developers/interactions/receiving-and-responding).

The handler edits the deferred response once, with a ten-second delivery
deadline. Answers and post-defer errors are visible in the configured channel;
access denials before deferral are ephemeral. All sends/edits use
`AllowedMentions.none()` for everyone, roles, users and reply mentions. Generated
text stays within 1,800 UTF-16 units, below Discord's 2,000-character limit, and
is never split into repeated requests. Delivery failure/cancellation does not
refund, replay or regenerate. Discord's own bounded HTTP retries can repeat an
edit of the same response; they never cause another OpenAI generation.

`setup_hook` synchronizes the command tree once per configured guild per client
startup, with a 30-second deadline per guild. Ready/reconnect events only log
readiness. Guild synchronization replaces this application's guild command set
with `/rolfbot`; review existing application commands first. No global command
sync is performed. Removing a guild from configuration immediately denies its
use after restart; an old command may remain visible there until the owner removes
it from Discord. [discord.py lifecycle](https://discordpy.readthedocs.io/en/stable/api.html#discord.Client.setup_hook),
[command synchronization](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.app_commands.CommandTree.sync).

Run one bot process sharing one persistent database. Construction/startup,
reconnect and shutdown generate no AI messages. Third-party Discord/aiohttp
payload logs are suppressed, including at DEBUG; application errors use fixed
safe categories. No scheduler, music or extra slash commands run.

## Natural conversations and memory

Mention the actual bot in a configured text channel: `@Rolfbot what's the most
useless superpower?` The mention starts a fresh conversation unless it is also a
reply to a remembered Rolfbot answer. Both `<@ID>` and `<@!ID>` are recognized
against the logged-in bot's ID and Discord mention metadata. Its mention syntax
is removed before the existing 1,000-code-point validator runs. Empty/oversized
prompts are rejected before quota admission.

Use Discord's **Reply** action on a recent Rolfbot answer to continue. Other
friends can join that same chain by replying. A snapshot is tied to each confirmed
bot answer and scoped to its guild and channel: replying to an older answer
branches from that point without importing later turns. Nearby messages, replies
to unrelated users/bots, and separate mentions do not join the conversation.
`/rolfbot` remains a standalone, context-free alternative.

Discord exposes messages mentioning the application without Message Content
intent. Replies without a fresh textual mention work only when Discord supplies
their text and the referenced bot answer is still remembered. If nothing happens,
**include `@Rolfbot` in your reply**. After expiry, eviction or restart, an explicit
mention safely starts fresh; an unrecognized reply without a mention is ignored.
The bot never fetches missing content or reconstructs old context.
[Discord content exceptions](https://docs.discord.com/developers/events/gateway#message-content-intent),
[discord.py intents](https://discordpy.readthedocs.io/en/stable/intents.html).

| Bound | Policy |
| --- | --- |
| History per reply snapshot | At most 4 completed user/assistant pairs and 6,000 UTF-8 text bytes, including participant prefixes |
| Full OpenAI request text | At most 12 KiB (12,288 bytes), including personality, history, prefixes and current input |
| Idle lifetime | 60 minutes since creation or a valid addressed use of that reply accepted for handling; an event-loop timer removes idle records even without traffic |
| Process capacity | 64 reply snapshots; expired records removed first, then least recently used |
| Trimming | Oldest complete pairs first; never split Unicode, retain orphan answers, or discard current input to fit history |

These constants live in `conversation.py`; no new environment settings are needed.
A single completed pair larger than 6,000 bytes is omitted from future history;
its reply ID remains recognized with empty context. Each snapshot counts toward
the 64-record cap, including older branching points. Participant mappings are
also bounded by retained exchanges. Labels such as `Participant 1` distinguish
contributors without automatically sending usernames or raw Discord IDs to OpenAI.
An author whose older turns have been trimmed may receive a new label.

Only a successful AI answer **and confirmed Discord delivery** create a completed
history pair. Denials, failures, partial output, cancellation and ambiguous sends
never do. A reply already being processed gets brief busy feedback when capacity
permits. At most two message handlers run through generation/delivery; excess
events and active duplicates are ignored without a queue or quota use. Unrelated
conversations can use both shared AI slots. Typing indicators are owned, bounded
and cancelled/joined after completion or shutdown. Replies use `mention_author=False`,
`AllowedMentions.none()` and suppressed embeds, leaving the user's original message
visible. Delivery has a ten-second deadline; there is no application retry. Discord's
SDK may retry its HTTP send within that deadline, so ambiguous delivery cannot
guarantee exactly one visible post; it never causes a second model generation.

Both paths retain 20/user/day, 100 total/day, the 30-second shared user cooldown,
512 output tokens and zero OpenAI retries. Message attempts use `message:<message-ID>`;
slash interactions retain their existing numeric attempt IDs. SQLite's single-use
dispatch prevents regeneration after redelivery, memory eviction or process restart.
Changing channel or starting another conversation never resets quota.

Explicit user text and this bounded history are sent to OpenAI with `store=False`.
Previous contributions have only user/assistant roles; none become privileged
instructions. The AI layer independently validates context before reservation and
enforces the full text budget during request construction. History increases input
token costs despite unchanged request quotas; monitor the owner-managed $2/month
limit and $1 alert. Character/byte bounds are not a dollar guarantee.

Rolfbot writes no conversation transcript to SQLite, logs or files. Memory is
cleared at expiry and graceful shutdown and is absent after restart. Usage and
cooldowns persist independently. Discord retains the visible messages according
to its own policies; `store=False` is not a promise about all provider retention.

## OpenAI service contract

Create **one `AIService(settings, store)` per process/event loop**, shared by all
Discord user handlers and future scheduled jobs. Creation validates the explicit key and
constructs an `AsyncOpenAI` client, but sends nothing. Imports, settings loading
and offline commands leave the SDK uninitialized. Use the service as an async
context manager, or await `aclose()` during shutdown; it stops admission, cancels
active work and closes HTTP resources. A cancelled shutdown caller still waits
for owned cleanup. The Discord lifecycle owns this service.

The only public generation methods are:

- `await service.generate_user(user_id, attempt_id, text, guild_id=..., now=..., context=None)`
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
one validated current user message plus a concise centralized personality instruction,
optionally preceded by validated bounded user/assistant history. Slash commands
and smoke tests omit context. There are no provider-side persistent conversation
IDs, tools, searches or continuation calls.
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
without continuation. The Discord adapter suppresses mentions and enforces the
delivery boundary; the AI service itself stays independent of Discord.

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
the service account. Tests use temporary files; local live-test state should use
`$HOME/.local/state/rolfbot/usage.sqlite3`. CLI live/provisioning commands reject
paths resolving inside the application package, known checkout/release trees or
Python environment, including symlink aliases. The operator must keep any other
release directories separate as well. `check-config` validates syntax only.

Use `python -m rolfbot init-db` for deliberate first-time provisioning. It needs
no credentials and sends nothing. The underlying async maintenance API remains:

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
validate existing state with `validate`, and stop AI work if it is unavailable.
Only the operator can decide whether first-time provisioning is appropriate.
Interrupted provisioning may leave an unusable file requiring review.
`run` and `smoke-test` require a successful existing-file integrity/schema/write
check, then refuse persistent OpenAI blocks. Neither command creates, migrates,
resets or clears state. Every later admission/dispatch still checks storage.
Startup tests file and journal-directory writability with a metadata write inside
a savepoint, then rolls it back; accounting and block metadata remain unchanged.

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

- Monitor the validated production mention/reply behavior and privately review
  project attribution and billing usage as needed.
- Keep the approved single guild/channel; a second guild requires a later rollout.
- Maintain the validated production service and establish ongoing private
  off-host backup retention before expanding the deployment.
- Later refine the friend-group personality and add varied spontaneous messages
  approximately every 2–7 days, only 08:00–00:00 Europe/Oslo, sharing global quota.
- Optional voice music, subject to the [YouTube research](docs/YOUTUBE_RESEARCH.md)
  and future host-specific tests.
- Tested release/deployment and owner-managed billing safeguards before live use.

**Production acceptance is complete; Rolfbot is active and enabled at boot.**
See the [deployment runbook](docs/DEPLOYMENT.md) for status, restart, emergency-stop
commands and accounting recovery rules.
The owner reports that the Rolfbot project, intended GPT-6 Luna model, restricted
key, $2 enforced monthly limit, $1 alert and $5 prepaid credit with auto-recharge
disabled are configured. These account settings were not inspected or changed.
Owner Discord checks were reconciled with production logs and accounting.
Music, scheduling and additional guilds remain deferred.
Temporary media will belong under `downloads/`;
intentional assets and fixtures belong in `assets/` and `tests/fixtures/`.
