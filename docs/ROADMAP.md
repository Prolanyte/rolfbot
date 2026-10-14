# Rolfbot modernization roadmap

Planning baseline and Milestone 1 validation: **14 October 2026**. Phase 0 and
**Milestone 1 are complete**. The delivered package is an offline foundation;
Milestones 2–7, all external integrations and deployment remain future work.
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
| 2. Usage protection | Enforce persistent quotas before paid access | Milestone 1 | Atomic reservation and restart/concurrency tests pass |
| 3. OpenAI and billing | Deliver bounded asynchronous AI requests | Milestones 1–2; owner's billing setup before live calls | Mocked failures pass; later authorized smoke test confirms model and accounting |
| 4. Discord and scheduling | Restore commands and periodic messages safely | Milestones 1–3 | Slash commands, two-server isolation and reconnect/scheduling tests pass |
| 5. Optional music | Decide feasibility, then implement if approved | Milestone 4; research and playback gates | Local and Vultr evidence plus acceptable terms/maintenance decision, or music disabled |
| 6. Release readiness | Establish a releasable, tested build | Milestones 1–4; 5 if music enabled | Test matrix, configuration and operational review complete |
| 7. Production deployment | Deploy independently of the existing website | Milestone 6 and separate deployment authorization | Service, restart, resource limits and rollback verified |

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
  declares zero third-party runtime dependencies, setuptools **84.0.0** for
  builds and pytest **9.1.1** for development; `requirements-dev.lock` pins the
  full build/test dependency set.
- Explicit environment-only configuration. No dotenv dependency or implicit
  `.env` loading. The owner can supply process variables directly now and via
  a protected systemd environment file at the later deployment milestone.
- Safe configuration errors and credential-free offline validation. Credential
  requirements are opt-in parameters to the settings loader for future callers;
  they validate values without enabling integrations. Placeholder/empty or
  whitespace-containing required credentials fail without echoing their values.
- Consistent stderr logs for journald, redaction of configured credential values,
  and omission of exception/stack details even at DEBUG. No prompt input exists.
- `check-config` validates settings and exits offline. With valid configuration,
  `run` refuses with exit code 3 until Milestones 2–4 are implemented. SIGINT/SIGTERM cancel async
  work, run cleanup and restore handlers; no continuous service is started.
- An offline pytest harness that clears service settings, blocks network access
  and guards subprocess imports. `monkeypatch`, ordinary callables and `tmp_path`
  support later fake clients, clocks and temporary databases without implementing
  those components prematurely.

| Setting | Milestone 1 status / future meaning |
| --- | --- |
| `ROLFBOT_LOG_LEVEL` | Implemented; default INFO, validated standard level names |
| `ROLFBOT_TIMEZONE` | Implemented; default Europe/Oslo, validated against installed IANA data |
| `OPENAI_API_KEY`, `DISCORD_BOT_TOKEN` | Recognized and hidden from settings representations; optional/unused offline |
| `OPENAI_MODEL` | Future-only, ignored now; planned default gpt-6-luna with no unapproved fallback |
| `OPENAI_REASONING_EFFORT` | Future-only, ignored now; planned default none, evaluate low only if justified |
| `ROLFBOT_GUILD_CHANNELS` | Future-only, ignored now; private two-guild channel mapping |
| `ROLFBOT_DATABASE_PATH` | Future-only, ignored now; persistent SQLite state outside release directories |

**Acceptance evidence:** 47 offline tests passed on each tested Python version.
All source/tests compile. Editable installation (3.13) and regular wheel
installation (3.12) succeeded in isolated environments; both passed `pip check`.
CLI help/configuration checks and failed startup were exercised from outside the
checkout. Import tripwires observed no network calls, no SDKs are runtime
requirements, and credential/error redaction and signal cleanup were tested.

**Milestone 1 is ready to close; no acceptance gaps remain.** This does not claim
production VM compatibility, working external integrations or service-level
shutdown guarantees for components that do not exist yet. No production access,
paid API call, Discord connection or billing change was performed.

## 2. Persistent anti-abuse protection — next milestone

**Objective:** all AI entry points share one accounting service and cannot bypass
limits through parallel requests, server switching or restarts.

| Protection | Required limit |
| --- | --- |
| AI requests per Discord user ID | 20 per Oslo calendar day across both servers |
| AI requests globally | 100 per Oslo calendar day, including scheduled AI messages |
| Per-user cooldown | 30 seconds across both servers, surviving restarts |
| User input | At most 1,000 characters, checked before reservation/API dispatch |
| AI output | At most 512 output tokens per API attempt |

### Reservation and failure policy

1. Validate guild/channel access and input first. Count characters explicitly as
   Unicode code points; reject empty or oversized input without calling OpenAI.
2. Use SQLite tables for daily user/global usage, per-user cooldown expiry and
   reservations keyed by a unique interaction/job attempt ID. The user key must
   be Discord user ID alone, not `(guild_id, user_id)`.
3. In one short write transaction (for example `BEGIN IMMEDIATE`), check the
   cooldown and both quotas, reject duplicate attempt IDs, reserve capacity,
   and persist cooldown expiry. Commit **before** any billable request. Never
   hold the transaction open during network I/O. Handle lock contention with
   bounded waits outside Discord's event loop; database failure denies AI access.
4. Persist dispatch state before sending. Record completion and available token
   usage afterward. Disable automatic SDK retries initially: one reservation
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

**Acceptance:** independent SQLite connections racing for the last slot admit
exactly one request; quotas and cooldowns survive restart; replay and duplicate
refunds do not change accounting twice. Denials give friendly messages with the
next eligible time. Unit tests prove all entry points share this service.

## 3. OpenAI integration and cost protection

### Asynchronous AI requests

Use the supported `openai` Python SDK's `AsyncOpenAI` client and await the
Responses API. Set explicit connect/read/overall deadlines and close the client
on shutdown. The SDK has automatic retries by default; configure `max_retries=0`
for the first implementation so accounting controls every attempt.
[Official Python SDK reference](https://developers.openai.com/api/reference/python).

Keep the requested model **`gpt-6-luna`**, selectable through `OPENAI_MODEL`.
Start with `reasoning.effort="none"` for short social replies: this is a supported
setting and preserves the legacy non-reasoning workload. Compare `low` only if
representative examples justify the extra cost/latency. Standard short-context
rates currently list $0.10 per million input tokens and $0.50 per million output
tokens; verify rates again before enabling paid access.
[GPT-6 Luna model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna).

Set `max_output_tokens=512`, covering **both visible output and reasoning tokens**.
Handle incomplete responses and empty text without increasing the cap or making
automatic continuation calls. A token cap does not guarantee Discord's character
limit; also bound/split the message safely at delivery.
[Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create).

- Preserve the existing persona in concise instructions, with one bounded user
  message and no accumulated conversation history initially. Send no tools and
  perform no weather lookup or other auxiliary calls. Do not imply live weather
  knowledge in periodic messages.
- Review request fields during migration; do not mechanically copy `n`, `stop`
  or legacy sampling settings. If reasoning is enabled, remove unsupported
  sampling parameters such as `temperature`.
  [GPT-6 migration guidance](https://developers.openai.com/api/docs/guides/latest-model/gpt-6-astra#migration-quickstart).
- Limit concurrent requests and waiting work. Keep all network and potentially
  blocking storage work off the Discord event loop. Defer Discord interactions
  promptly while waiting for a response.
- Distinguish invalid credentials, unavailable models, malformed requests,
  timeouts, transient rate limiting, upstream outages and exhausted billing.
  Return friendly errors; log sanitized error categories/request IDs.
- Back off after repeated failures with a bounded service cooldown. Billing
  limit errors disable generation until the owner resolves them or the limit
  resets; never retry them in a loop or switch to a more expensive model.

### Owner-managed billing prerequisite

Before any live API test or production use, the owner will manually:

- Create a dedicated API project named **Rolfbot** and a suitably restricted key.
- Set the project's monthly spending limit to **$2 USD**, explicitly enable
  **hard-limit enforcement**, and set a spending warning at **$1 USD**.
- Create/configure the planned Revolut virtual card in **OpenAI organization
  billing**. Confirm payment-method acceptance there; no card details in the bot,
  environment example, repository or deployment scripts.
- Prefer prepaid credit with **automatic recharging disabled**, where available.
  Check minimum purchase, expiry, taxes and account eligibility before funding;
  a prepaid balance is not the same as the project's monthly spending limit.

Current OpenAI documentation distinguishes alerts, which allow traffic to
continue, from enforced project/organization spend caps. Hard-limit errors use
HTTP 429 with `project_spend_limit_exceeded` or
`organization_spend_limit_exceeded`. Enforcement may lag slightly, so **$2 is
not an absolute guarantee against any overrun**. Account settings have not been
inspected or changed in Phase 0 or Milestone 1; verify the actual controls before
live use.
[OpenAI spend limits](https://developers.openai.com/api/docs/guides/spend-limits).

The daily quotas complement the billing controls; request counts alone are not
a dollar cap. For scale, 100 daily attempts × 31 days × 512 output tokens at the
listed Standard output rate is about **$0.79 in output alone**. Input, cache-write
charges where applicable, other pricing tiers and future price changes must also
be budgeted. Characters are not tokens. Record actual usage without storing
message content, and review spending at the $1 warning.

**Acceptance:** mocked tests verify reservation-before-dispatch, model/effort and
512-token settings, disabled retries, error handling and clean shutdown. After
owner billing setup and live-test authorization, a small smoke test verifies
project attribution, model access, useful replies and usage reporting. Do not
spend through the cap to test enforcement; simulate those errors locally.

## 4. Modern Discord commands and scheduling

**Objective:** reliable interaction in both servers from one process.

- Select the current supported `discord.py` release at implementation time;
  record the tested version. Use application/slash commands for AI and, if
  enabled later, music. Define synchronization during startup without repeating
  registration on every reconnect.
- Use minimal gateway intents: start with guild information, adding voice-state
  events only for music. Avoid member, presence and message-content intents
  unless a concrete feature requires them. Document channel permissions and
  command scopes; do not request Administrator permission.
- Configure and validate guild/channel mappings privately. Apply access checks
  before voice connection or API reservation. Initially reject DMs and unknown
  guilds; allow the owner to change that policy deliberately later.
- Give helpful responses for invalid input, missing permissions, quota denial
  and outages. Bound Discord message length and suppress unintended mentions
  in generated text. Do not expose internal exception strings.
- Support reconnection, cancellation and graceful shutdown. Retain references
  to tasks; start the scheduler once through a controlled lifecycle hook, wait
  for readiness, and prevent duplicate tasks after reconnects.
- Preserve the random 2–7-day interval. Proposed policy: persist a next-due UTC
  timestamp per configured channel, claim jobs atomically, and do not send a
  burst of missed messages after downtime. Record a new due time after an attempt;
  use bounded deferral for temporary outages and quota exhaustion.
- Handle missing/deleted channels and send failures without killing the entire
  scheduler. Resolve ambiguous sends conservatively: avoid automatic duplicate
  generations/posts. Catch per-job errors while preserving task cancellation.

**Acceptance:** mocked commands exercise both guilds, permissions and denials;
repeated ready/reconnect events leave exactly one scheduler. Simulated time,
restart and failure tests preserve due times and global quota accounting.
Later controlled Discord testing verifies real registration and delivery.

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
production deployment. Mock paid/network services and inject time rather than
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
The README is then extended with an actually tested live-bot installation
procedure; Milestone 1 only verifies the offline foundation's installation.

## 7. Separate production deployment milestone

**Target:** the existing Ubuntu 24.04 Vultr VM serving `nilsherman.no` through
nginx as a static site. No production access or changes were made in Phase 0 or
Milestone 1.

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
| Periodic cadence and downtime policy | Scheduling work | Independent 2–7-day schedule per configured channel; no catch-up burst |
| Billing/card availability and acceptable small enforcement overrun | Before paid calls | Rolfbot project, $2 enforced limit, $1 alert, prepaid without auto-recharge |
| YouTube rights, maintenance burden and optional provider/account use | Before music investigation beyond public samples | No authenticated cookies; keep music optional |
| Fallback if YouTube fails on Vultr | Before enabling music | Rights-cleared local/approved audio or link sharing |
| VM resource ceilings and maintenance window | Deployment planning | Measure available headroom; protect the existing website |

No project, key, card, provider service, Discord setting or deployment resource
has been created or configured during Phase 0 or Milestone 1.
