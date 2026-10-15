# Rolfbot modernization roadmap

Planning baseline/Milestones 1–2: **14 October 2026**. Milestone 3 offline
validation: **15 October 2026**. Phase 0 and Milestones 1–2 are complete.
**Milestone 3 is implementation complete, pending authorized live smoke testing.**
The package includes a bounded OpenAI service protected by SQLite accounting.
The **minimal Discord MVP (4a) is implemented offline**, with an explicitly opt-in
smoke test and `/rolfbot` command. Live validation remains pending. No real API
call, Discord connection or deployment was performed during implementation.
See the [README](../README.md) for tested setup and commands, and
[YouTube research](YOUTUBE_RESEARCH.md) for the conditional music decision.

The intended scale is one process serving two existing Discord servers, with
roughly 10–12 people in the larger server. Preserve Rolfbot's helpful, casual
friend-group personality without adding unnecessary infrastructure or tools.

## Delivery order

| Milestone | Priority and objective | Dependencies | Completion gate |
| --- | --- | --- | --- |
| 0. Documentation — complete | Establish an accurate baseline | Source inspection and current research | Links, ignore rules and scope verified; legacy source unchanged |
| 1. Runtime foundation — complete | Make future development reproducible and testable | Phase 0 | Validated configuration, parseable modules, offline tests and documented dependencies |
| 2. Usage protection — complete | Enforce persistent quotas before paid access | Milestone 1 | Atomic reservation, lifecycle, restart, calendar and concurrency tests pass on Python 3.12/3.13 |
| 3. OpenAI — implementation complete, pending authorized live smoke testing | Deliver bounded asynchronous AI requests | Milestones 1–2; owner-managed billing configured | 258 offline tests pass on Python 3.12/3.13; live provider access/attribution remains unverified |
| 4a. Minimal Discord MVP — implemented offline | One explicit /rolfbot command, opt-in smoke test and safe startup | Milestones 1–3 | Offline suite passes; owner verifies live OpenAI attribution and one-guild Discord delivery |
| 6. Minimal release readiness — next after local validation | Record the exact reviewed release SHA and local evidence | Milestone 4a; owner authorization to commit | Tests, configuration and operations review complete for the minimal feature set |
| 7. Production deployment — separately authorized | Deploy the minimal MVP independently of the website | Local live acceptance and minimal release readiness | Service, restart, resource limits and rollback verified; website unaffected |
| 4b. Personality and scheduling — after deployment | Friend-group refinement and varied spontaneous messages | Stable deployed MVP | Quiet hours, cadence, novelty, restart and quota tests pass |
| 5. Optional music — later | Decide feasibility, then implement if approved | Stable MVP; research and playback gates | Local and Vultr evidence plus acceptable terms/maintenance decision, or music disabled |

**Delivery order changed by the owner on 15 October 2026:** first validate the
smallest working OpenAI/Discord bot locally, then deploy it to the existing Vultr
website VM with separate authorization, then add features. Scheduling, personalized
inside jokes and music are not prerequisites for the initial deployment. This task
does not deploy. The [local runbook](LOCAL_TEST.md) records tested versions, the
base commit and the pending intended-deployment SHA; no commit/push was authorized.

## 1. Runtime foundation — complete

**Objective achieved:** a small, installable, testable application structure with
all external functionality disabled. The old `rolfbot7.py` was removed; Git
history preserves its implementation and persona. Its syntax error and obsolete
SDK code are no longer part of the active runtime.

Delivered components:

- A conventional `src/rolfbot` package with `python -m rolfbot`, typed settings,
  logging setup and an asyncio CLI lifecycle. There are no empty future modules
  or duplicate executables. Imports are quiet and do not connect to services.
- Linux CPython **3.12 and 3.13** support, tested on **3.12.15 and 3.13.16**.
  Python 3.12 remains the Ubuntu 24.04 production baseline. `pyproject.toml`
  initially declared zero third-party runtime dependencies (Milestone 3 adds the
  OpenAI SDK/HTTPX2), setuptools **84.0.0** for
  builds and pytest **9.1.1** for development; `requirements-dev.lock` pins the
  full build/test dependency set.
- Explicit environment-only configuration. No dotenv dependency or implicit
  `.env` loading. The owner can supply process variables directly now and via
  a protected systemd environment file at the later deployment milestone.
- Safe configuration errors and credential-free offline validation. Credential
  requirements are opt-in parameters to the settings loader for live callers;
  they validate values without enabling integrations. Placeholder/empty or
  whitespace-containing required credentials fail without echoing their values.
- Consistent stderr logs for journald, redaction of configured credential values,
  and omission of exception/stack details even at DEBUG. No prompt content is logged.
- `check-config` validates settings and exits offline. At Milestone 1, `run`
  refused startup; Milestone 4a now starts only the validated minimal bot.
  SIGINT/SIGTERM cancel async work, run cleanup and restore handlers.
- An offline pytest harness that clears service settings, blocks network access
  and guards subprocess imports. `monkeypatch`, ordinary callables and `tmp_path`
  support later fake clients, clocks and temporary databases without implementing
  those components prematurely.

| Setting | Current status / future meaning |
| --- | --- |
| `ROLFBOT_LOG_LEVEL` | Implemented; default INFO, validated standard level names |
| `ROLFBOT_TIMEZONE` | Implemented; default Europe/Oslo, validated against installed IANA data |
| `OPENAI_API_KEY`, `DISCORD_BOT_TOKEN` | Hidden from representations; optional offline; smoke-test requires OpenAI, run requires both |
| `OPENAI_MODEL` | Implemented; default and sole approved model gpt-6-luna; no fallback |
| `OPENAI_REASONING_EFFORT` | Implemented; default and sole approved effort none |
| `ROLFBOT_GUILD_CHANNELS` | Implemented in 4a; private JSON guild-ID strings to nonempty channel-ID lists; required for run |
| `ROLFBOT_DATABASE_PATH` | Implemented in Milestone 2; optional offline, explicit absolute file path required for usage storage; production state outside release directories |

**Acceptance evidence:** 47 offline tests passed on each tested Python version.
All source/tests compile. Editable installation (3.13) and regular wheel
installation (3.12) succeeded in isolated environments; both passed `pip check`.
CLI help/configuration checks and failed startup were exercised from outside the
checkout. Import tripwires observed no network calls; no SDK was a runtime
requirement at Milestone 1. Credential/error redaction and signal cleanup were tested.

**Milestone 1 is ready to close; no acceptance gaps remain.** This does not claim
production VM compatibility, working external integrations or service-level
shutdown guarantees for components that do not exist yet. No production access,
paid API call, Discord connection or billing change was performed.

## 2. Persistent anti-abuse protection — complete

**Objective achieved:** an offline accounting component enforces persistent limits
across independent connections, guild contexts and restarts. Every future AI entry
point must use this component. Milestone 3 integrated it into AIService;
Milestone 4a's CLI smoke test and Discord adapter use that same service.

`usage.py` defines limits, validation and typed results. `usage_store.py` provides
async reservation, dispatch, completion, refund and read-only status operations.
See the [README contracts and recovery guidance](../README.md#persistent-usage-protection).

| Protection | Required limit |
| --- | --- |
| AI requests per Discord user ID | 20 per Oslo calendar day across both servers |
| AI requests globally | 100 per Oslo calendar day, including scheduled AI messages |
| Per-user cooldown | 30 seconds across both servers, surviving restarts |
| User input | At most 1,000 characters, checked before reservation/API dispatch |
| AI output — implemented by Milestone 3 | At most 512 output tokens per API attempt, enforced in request parameters |

### Reservation and failure policy

1. Future handlers validate guild/channel access first. The implemented component
   validates identifiers and input before storage, counts Unicode code points and
   rejects blank, malformed or oversized input without reserving capacity.
2. SQLite `attempts` records are the source of truth for daily counts,
   with indexed day/user queries and unique interaction/job attempt IDs. Separate
   `cooldowns` records preserve user expiry; `metadata` fixes the timezone.
   User identity is Discord user ID alone, not `(guild_id, user_id)`.
3. In one short write transaction (for example `BEGIN IMMEDIATE`), check the
   cooldown and both quotas, reject duplicate attempt IDs, reserve capacity,
   and persist cooldown expiry. Commit **before** any billable request. Never
   hold the transaction open during network I/O. Handle lock contention with
   bounded waits outside Discord's event loop; database failure denies AI access.
4. Persist dispatch state before sending. Milestone 3 records completion and
   available token counts afterward. Automatic SDK retries are disabled: one reservation
   permits one API attempt. Any future retry must obtain its own allowance and
   fit a bounded retry policy; no invisible extra paid attempts.
5. Refund capacity only when it is provable that no request was dispatched, such
   as cancellation during local preparation. Make refunds transactional and
   idempotent, against the original reservation's date. Keep the cooldown after
   a refund so repeated failures cannot create a tight request loop.
6. Retain reservations for dispatched failures, ambiguous timeouts, process
   crashes after dispatch, and failed Discord delivery after generation. They
   might have incurred cost; do not promise a refund. On restart, reconcile
   unfinished records conservatively and never replay them automatically.

Derive day keys from timezone-aware `Europe/Oslo` time, with UTC timestamps for
cooldowns and audit records. A new day uses new buckets; do not reset counters
with a fragile midnight task. Test the 23-hour and 25-hour DST days. A request
keeps the date on which it was reserved even if completion crosses midnight.
Cooldowns continue across midnight, and backward clock changes must not shorten
them. Retain reservation records long enough to prevent replay during recovery.

Scheduled jobs reserve from the **same global bucket** before generation. They
do not impersonate a Discord user. Each separate API generation counts once;
generating separate messages for two channels consumes two global requests.
At exhaustion, skip or defer with a bounded policy, never bypass the limit.

**Implementation details:** every mutation uses `BEGIN IMMEDIATE` and commits
before returning a grant. Connections run in worker threads with a 250 ms SQLite
lock timeout, DELETE rollback journaling and `synchronous=EXTRA`; there is no WAL
dependency or transaction held during network work. Cancellation/lost commit
acknowledgement may retain capacity conservatively. Structured denials expose
safe reason codes and next eligibility where calculable, never SQL errors or
database paths. Future Discord code supplies friendly wording.

Initialization is explicit and creates owner-only files in an existing directory.
Normal operations cannot recreate missing storage. Existing empty/corrupt files,
unknown schemas and timezone mismatches fail closed. Imports and `check-config`
perform no database setup. Retain all attempts, including refunded tombstones;
no expiry, replay, implicit migration or cleanup can reopen capacity. Milestone 3
adds an explicit transactional version-1 to version-2 migration. Production
backup/recovery must preserve usage and cooldowns together and reconcile any
usage missing from an older snapshot before enabling AI.

**Acceptance evidence:** **142 offline tests passed on CPython 3.12.15 and
3.13.16**, including all Milestone 1 regressions. Real file-backed tests cover
20th/21st and 100th/101st boundaries, shared guild/user/global allowances, scheduled
jobs, Unicode validation, exact cooldown expiry, month/year/midnight boundaries,
both Oslo DST transitions, backward clocks and original reservation dates.
Separate overlapping SQLite connections race for the final personal/global slot
and admit exactly one winner; duplicate dispatch and dispatch/refund races are
also tested. A fresh child process exits without cleanup after committed reserve/
dispatch operations, and the parent verifies retained usage and cooldowns.
Tests cover lock and commit failures, transactional rollback, idempotent lifecycle
operations, lost acknowledgements, cancelled workers, corrupt/inaccessible storage
and schema failures. Imports open no database or network connection, diagnostics
remain sanitized, `run` was still disabled at Milestone 2, both source/test compilations pass and
`git diff --check` is clean.

**Milestone 2 provided the accounting foundation for Milestone 3.** Its storage
implementation remains standard-library only. No live OpenAI/Discord call,
scheduler, music functionality, VM access or billing verification/change was added.
Production storage durability,
backup restoration and release migration still require operational validation.

## 3. OpenAI — implementation complete, pending authorized live smoke testing

### Asynchronous AI requests

Implemented components:

- `AIService` is the only public generation interface, shared by user and future
  scheduled requests. It always reserves SQLite capacity, prepares the bounded
  request, persists dispatch and requires exactly `DISPATCH_GRANTED` before one
  send. There is no recovery replay or alternate public unaccounted send method.
- The private `openai_client.py` layer uses official **openai 3.31.0** with
  **httpx2 2.13.1**, pinned alongside the full dependency closure. It constructs
  `AsyncOpenAI` lazily, with `max_retries=0`, an explicit key and official endpoint,
  no redirects/proxy-environment configuration, and no ambient billing/key override.
  [Official release](https://github.com/openai/openai-python/releases/tag/v3.31.0),
  [SDK reference](https://developers.openai.com/api/reference/python).
- Model and effort settings are validated allowlists: **gpt-6-luna** and **none**
  only. Every Responses request sets **512 output tokens**, `store=False`, no
  streaming/background work, no tools and no continuation/history. User input is
  one validated message; scheduled input and the friendly, playful persona are
  centralized bounded constants. No live-weather or tool capability is claimed.
  [Model](https://developers.openai.com/api/docs/models/gpt-6-luna),
  [Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create).
- Two immediate generation slots cover admission through finalization. There is
  no in-memory waiting queue; `BUSY` consumes no quota. Network timeouts are
  connect 5 s, read 25 s, write 5 s and pool 1 s, inside a 30 s overall network
  deadline. SQLite remains on worker threads with bounded lock waits. Shutdown
  cancels active requests and owns async client cleanup even if its caller cancels.
- Only confirmed local abandonment before dispatch can refund; lost reservation
  results, attempted dispatch transitions, API timeouts/cancellation and failed
  completion writes retain capacity. Completion failure cannot trigger a repeat
  send. Available counts can remain unknown if persistence fails.
- Completed text is limited to 1,800 UTF-16 units and checked for empty/malformed,
  incomplete, refused, oversized or unsafe-control output. Failure produces safe
  categories without automatic continuation. Milestone 4a now supplies the
  Discord sending/mention controls around this unchanged AI contract.
- Transient failures apply monotonic 30/60/120/240/300-second cooldowns, capped at
  300 s, with no automatic retry. Billing limits, exhausted credit, credentials,
  permissions and request-configuration failures latch the service disabled.
  The block is persisted with completion and checked at reserve/dispatch. Recovery
  requires owner remediation, a closed service, explicit `clear_ai_block()` and a
  new service instance. A failed block write still stops the running process;
  reconcile storage before restart. Already-started requests may still finish.
- Logs contain only derived correlation IDs, outcome, model, duration, safe HTTP
  status, token counts and accounting status. Raw SDK/transport logs are suppressed;
  prompts, answers, headers, provider error bodies and user IDs are never logged.
  `store=False` is not a guarantee against abuse-monitoring retention.
  [Data controls](https://developers.openai.com/api/docs/guides/your-data).

### Schema and usage tracking

Version 2 adds nullable integer input/output token counts to `attempts` and a
nullable provider block to `metadata`. `UsageStore.migrate()` upgrades an existing
version-1 file in one `BEGIN IMMEDIATE` transaction, preserving all records,
cooldowns, indexes and timezone. It is explicit, repeat-safe and rejects unknown
schemas; normal operations never migrate or recreate state. Completion persists
numeric metadata and any block atomically; duplicate completion cannot overwrite
counts. Missing/invalid counts are unknown (`NULL`), not zero. Prompt/answer
contents are never persisted. Raw JSON types are checked before counting usage.

Stop writers and back up before migration. Old Milestone 2 binaries reject schema
2, so rollback requires a compatible release, not restoring stale accounting.
See [database recovery](../README.md#database-location-and-recovery).

### Offline acceptance evidence

**258 tests passed on each of CPython 3.12.15 and 3.13.16.** The suite retains
all earlier quota, calendar, independent-connection and process-restart coverage.
New tests run the real SDK over mocked HTTP and assert a committed, unused
dispatch for every simulated send. They cover exact request settings, no hidden
retries even with retry headers, quota/duplicate/storage denials, concurrency
slots, cancellations at each lifecycle boundary, timeouts, provider errors,
persistent blocks, backoff, output/Unicode limits, usage metadata and privacy.
Actual file-backed v1 fixtures verify preservation, transactional rollback and
repeat migration. Imports remain database/network/client-free, `check-config`
works without credentials, and `run` refused startup at this milestone. Both compilations, locked
dependency installations, `pip check` and `git diff --check` pass.

**Offline Milestone 3 acceptance is satisfied.** Live API/project attribution,
model availability and response quality are unverified. No real credentials,
provider call, Discord connection, scheduler, music, VM access or billing change
was used. Milestone 4a now supplies the integration and smoke-test command; the
owner must perform the opt-in live checks before end-to-end acceptance.

### Owner-managed billing prerequisite

On 14 October 2026, the owner reported manually configuring a dedicated
**Rolfbot** project, GPT-6 Luna as the intended model, a restricted key, a **$2 USD
enforced monthly limit**, a **$1 USD alert**, and **$5 USD prepaid credit** with
**automatic recharging disabled**. This is owner-reported status: the agent did
not inspect, verify or change these settings during Milestones 2–3. No credentials
or payment details were read. Live API tests still need separate authorization;
Milestone 3 used offline mocks only.

Current OpenAI documentation distinguishes alerts, which allow traffic to
continue, from enforced project/organization spend caps. Hard-limit errors use
HTTP 429 with `project_spend_limit_exceeded` or
`organization_spend_limit_exceeded`. Enforcement may lag slightly, so **$2 is
not an absolute guarantee against any overrun**. Account settings have not been
inspected or changed by the agent in Phase 0 or Milestones 1–3. The owner remains
responsible for maintaining the controls before live use.
[OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits).

The daily quotas complement the billing controls; request counts alone are not
a dollar cap. For scale, 100 daily attempts × 31 days × 512 output tokens at the
planning rate ($0.50 per million output tokens) is about **$0.79 in output alone**.
Recheck current pricing before live use. Input, cache-write
charges where applicable, other pricing tiers and future price changes must also
be budgeted. Characters are not tokens. Record actual usage without storing
message content, and review spending at the $1 warning.

**Remaining live acceptance:** after separate authorization, a small smoke test verifies
project attribution, model access, useful replies and usage reporting. Do not
spend through the cap to test enforcement; simulate those errors locally.

## 4a. Minimal Discord MVP — implemented, pending local live validation

- Pinned **discord.py 2.7.1**, with aiohttp **3.14.5**, on Python 3.12/3.13.
- One guild-scoped `/rolfbot message:<text>` command. A private
  `ROLFBOT_GUILD_CHANNELS` JSON mapping enables one test guild or both guilds
  without source edits. DMs, bots, unlisted/mismatched guilds/channels and
  non-text channels are denied before admission.
- Authenticated interaction user/guild IDs and interaction ID feed the unchanged
  `AIService`/SQLite barrier. No alternate transport or quota implementation.
- Only the guilds intent; no ordinary-message subscriptions, message cache,
  member/presence/content intents or Administrator requirement.
- Defer before storage/API work, with a 2-second deadline. Failed/ambiguous defer
  cannot generate. Edit the deferred response once within 10 seconds, suppressing
  all mentions. AI output remains bounded to 1,800 UTF-16 units. Failed delivery
  never refunds or regenerates; friendly errors contain no provider details.
- Synchronize once per configured guild in `setup_hook` (30-second deadline per
  guild); never in `on_ready` or reconnect callbacks. Own and cancel active
  interaction tasks, then close OpenAI and Discord resources on shutdown.
- `init-db` explicitly provisions schema 2 offline. `run` and `smoke-test` require
  existing writable, intact, compatible external storage and refuse service
  blocks; no auto-create, migration, quota reset or block clearing.
- `smoke-test --confirm-billable` uses a fixed prompt, synthetic user ID `1`,
  unique attempt ID and normal quotas. It prints safe outcome/text/token counts
  and closes the client. The agent must not run it live; the owner opts in.

**Offline acceptance: 352 tests pass on each of Python 3.12.15 and 3.13.16.**
Real command registration/dispatch runs over mocked Discord
interactions and OpenAI HTTP, with committed SQLite authorization for every
simulated API send. Tests cover both guilds, duplicates, quotas, storage and
ambiguous dispatch, defer/delivery errors, Unicode/mentions, reconnection,
shutdown/cancellation and credential-free imports. Full results and the pending
release SHA are recorded in [LOCAL_TEST.md](LOCAL_TEST.md).

**Live acceptance pending:** owner executes the runbook, verifies OpenAI project
usage and a few slash commands in one test guild, then validates the second
guild. No live result or deployment is claimed. No periodic messages, extra
commands, music, history, tools, dashboard or HTTP server are implemented.

## 4b. Personalized personality and varied spontaneous messages — deferred

Develop this only after the minimal MVP is locally validated and deployed.

- Refine the friendly Rolfbot persona with owner-provided friend-group details,
  humor and inside jokes. Keep private details out of public examples; no
  accumulated conversation history or automatic channel ingestion is implied.
- Vary spontaneous content among **Million Dollars, But...**, **Would You
  Rather...**, strange/interesting facts, food-related remarks, jokes, absurd
  observations, conversation starters and occasional other suitable subjects.
  Avoid repetitive content and previously generated questions where practical;
  design a bounded novelty record rather than unbounded message history.
- Keep approximately **2–7 days** between spontaneous messages per configured
  channel, and **no catch-up bursts after downtime**. Persist and atomically
  claim next-due UTC timestamps; all generated messages use global API quota.
- Post only **08:00 inclusive to 00:00 exclusive in Europe/Oslo**. If a due time
  is outside that window, reschedule to a **randomized permitted time**, not
  exactly 08:00. Persist the adjusted time and test Oslo DST transitions.
- Own one scheduler through a controlled lifecycle; reconnects must not create
  duplicate tasks. Handle missing channels, quotas and temporary outages with
  bounded deferral. Ambiguous delivery must not regenerate or duplicate posts.

**Future acceptance:** deterministic clock/randomness tests for permitted hours,
2–7-day cadence, novelty, restart/downtime and global allowance, plus controlled
live delivery checks. These requirements are documentation only in the MVP.

## 5. Conditional music restoration

**Objective:** restore voice playback only if the
[research and future testing plan](YOUTUBE_RESEARCH.md) justify it.

- First decide whether YouTube's terms, reliability and maintenance costs are
  acceptable. Run the authorized future local/VM investigation before promising
  the feature; keep music disabled if the gate fails.
- If viable, replace `youtube_dl` with a tested `yt-dlp` setup, compatible EJS
  scripts and a supported JavaScript runtime. Include FFmpeg/ffprobe and the
  supported Discord voice dependencies. Current `discord.py` documents DAVE
  support and the `davey` dependency; legacy PyNaCl-only assumptions are inadequate.
  [Discord changelog](https://discordpy.readthedocs.io/en/stable/whats_new.html).
- Keep extraction asynchronous from Discord's perspective, with cancellable
  workers and deadlines. Resolve short-lived media URLs near playback time.
- Maintain separate bounded queues and voice state per guild, while enforcing
  process-wide resource limits. Add stop/skip, idle disconnect, failed-track
  handling, requester voice checks and FFmpeg/temp-file cleanup.
- Restrict URL sources, schemes, redirects and resolved destinations; prevent
  access to local files, private networks and VM metadata. Users cannot provide
  extractor flags, shell fragments, cookies or FFmpeg options.
- Start with public, authorized, finite tracks; no playlists, livestreams,
  authenticated accounts or PO Token services by default. Any provider requires
  a separate dependency/security/terms decision; it is not a guaranteed fix.

**Acceptance:** mocked queue, cancellation, security and cleanup tests pass;
the future local and Vultr playback gate passes with rights-cleared samples.
Alternatively, explicitly release with music disabled and an agreed fallback.

## 6. Tests and release readiness

Develop tests alongside each milestone, then run this release gate before any
production deployment, limited to features actually enabled in that release.
Scheduling and voice rows are later feature gates, not blockers for the minimal
MVP deployment. Mock paid/network services and inject time rather than
waiting days or hitting real rate limits.

| Area | Required evidence |
| --- | --- |
| User/global quotas | 20th/21st and 100th/101st boundaries; same user across two guilds; scheduled calls included |
| Cooldowns | 30-second boundary, restart and midnight crossing; failed/refunded attempts cannot loop |
| Calendar | Oslo midnight, DST transitions, month/year boundaries and backward clock changes |
| Atomicity | Concurrent writers, last-slot races, lock timeout and duplicate interaction/job IDs |
| Persistence | Restart before dispatch, after dispatch and before finalization; idempotent refunds; DB errors deny access |
| Input/output | Empty and 1,000/1,001-character input; Unicode; 512-token request cap; incomplete/empty output; Discord length and mentions |
| OpenAI failures | Timeout, connection error, 401/403, invalid model, transient 429, spend-limit 429 and 5xx; no hidden retry calls |
| Commands | Slash registration, permission failures, two guilds, unsupported DM/guild and friendly feedback |
| Scheduling | One task after repeated reconnects, deterministic 2–7-day range, restart, missing channel, downtime and quota exhaustion |
| Voice, if enabled | Queue isolation/limits, disconnects, extraction failure, expired URL, cancellation, worker/process cleanup; mocked network |
| Security/configuration | Missing/placeholder credentials, malformed mappings, secret redaction, hostile URLs/redirects/DNS and subprocess arguments |
| Operations | Dependency reproducibility, clean shutdown, state backup/recovery and compatible rollback |

**Acceptance:** automated checks pass from a clean environment; known defects are
resolved or disabled explicitly; manual tests record dates, versions and outcomes.
Record owner live results in the local runbook and the exact reviewed clean
commit SHA before deployment; never label an uncommitted workspace as a release.

## 7. Separate production deployment milestone

**Target:** the existing Ubuntu 24.04 Vultr VM serving `nilsherman.no` through
nginx as a static site. This follows minimal local OpenAI/Discord acceptance and
precedes additional feature development. No production access or changes were
made in Phase 0, Milestones 1–3, or the minimal MVP implementation.

- Run as a dedicated, unprivileged Linux service user with an isolated virtual
  environment and versioned release directories. Keep persistent SQLite state
  outside code/venv paths, accessible only to the service and administrators.
- Provide a protected environment/secrets file outside the checkout, owned by
  root or the service account with restrictive permissions. Record how to rotate
  keys without exposing values in command lines, logs or backups.
- Create a `systemd` service with bounded automatic restarts, startup rate
  limiting, graceful stop timeout and child-process cleanup. Use journald with
  retention limits and redaction. No separate public log/health endpoint needed.
- Apply least privilege and filesystem protections, plus measured `MemoryMax`,
  `CPUQuota`, `TasksMax` and file-descriptor limits. Profile the optional FFmpeg,
  JS and provider workloads before choosing values; leave headroom for nginx.
- Open no new listening ports. Permit needed outbound HTTPS/WebSocket traffic
  and, if voice is enabled, outbound Discord voice UDP with stateful replies.
  Confirm actual voice networking in testing; do not broadly open inbound ports.
- Write a runbook covering dependency installation, config validation, database
  migration/backup, release activation, service start/status/log checks and
  rollback to the previous tested release/venv.
- Rehearse rollback with compatible state schemas. Preserve usage/cooldowns;
  blindly restoring an old database can reopen quota capacity. If accounting
  cannot be reconciled, disable AI pending recovery instead of resetting it.
- Verify restart/reboot persistence, memory/CPU use and the existing website's
  health. Make no nginx/site changes unless separately necessary and approved.

**Acceptance:** tests pass before activation; service ownership, secrets, bounded
restarts, resource controls and rollback are verified; the website continues to
work without nginx configuration changes. Music remains disabled unless its
separate feasibility gate passed.

## Owner decisions before the relevant milestone

| Decision | When needed | Proposed starting point |
| --- | --- | --- |
| Which channels receive AI, music and scheduled messages? | Configuration/Discord work | Private per-guild mapping; no public numeric IDs |
| Periodic cadence and downtime policy | Later scheduling work | Approximately 2–7 days; 08:00–00:00 Europe/Oslo only, randomized allowed-time deferral; no catch-up burst |
| Maintain billing safeguards and authorize any live test | Before paid calls | Owner reports Rolfbot project, $2 enforced limit, $1 alert and $5 prepaid without auto-recharge; not agent-verified |
| YouTube rights, maintenance burden and optional provider/account use | Before music investigation beyond public samples | No authenticated cookies; keep music optional |
| Fallback if YouTube fails on Vultr | Before enabling music | Rights-cleared local/approved audio or link sharing |
| VM resource ceilings and maintenance window | Deployment planning | Measure available headroom; protect the existing website |

No project, key, card, provider service, Discord setting or deployment resource
has been created or configured by the agent during Phase 0, Milestones 1–3, or
the minimal MVP implementation.
