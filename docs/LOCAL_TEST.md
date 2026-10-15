# Local OpenAI and Discord validation

The owner reports **successful local live smoke, slash, mention and reply-chain
tests**, including follow-ups without another mention, conversation isolation and
the 30-second cooldown. Project attribution remains owner-managed and a second
guild remains disabled. Approved Gate B cutover activated
Rolfbot on Vultr on 16 October 2026 at 00:09 Europe/Oslo. Historical accounting was
preserved and production SQLite is now authoritative. Keep the local bot stopped
and retain its protected cutover snapshot for recovery; do not resume using stale
accounting. Owner production checks, boot enablement and a controlled restart
passed. See [DEPLOYMENT.md](DEPLOYMENT.md) for the evidence and operating commands.

For the existing working installation, keep its credentials and usage database;
do not repeat provisioning or the paid smoke test just for this enhancement.
Review the updated permissions in step 6 and conversation checks in step 8a.
Steps 2–5 remain a reference for a new installation or deliberate owner diagnosis.

## 1. Install and verify locally

From the checkout, use Linux Python 3.12 (preferred) or 3.13:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-build-isolation --no-deps -e .
python -m pip check
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q
python -m compileall -q src tests
```

Dependency installation uses the package index. Pytest blocks network access,
removes service configuration and uses synthetic keys/IDs and temporary storage.
The smoke test is a separate command; pytest never runs it live.

## 2. Configure a private environment

Create protected state/config directories outside all checkouts and releases:

```sh
umask 077
install -d -m 700 "$HOME/.config/rolfbot" "$HOME/.local/state/rolfbot"
${EDITOR:-vi} "$HOME/.config/rolfbot/local.env"
chmod 600 "$HOME/.config/rolfbot/local.env"
```

Put the following in that **private** file, replacing the key placeholder there.
Do not put the real key in a chat, tracked file, command argument, or shell history.
Use the existing restricted key belonging to the Rolfbot OpenAI project:

```sh
OPENAI_API_KEY=replace_with_your_openai_api_key
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=none
ROLFBOT_TIMEZONE=Europe/Oslo
ROLFBOT_DATABASE_PATH="$HOME/.local/state/rolfbot/usage.sqlite3"
ROLFBOT_LOG_LEVEL=INFO
```

Load your own trusted file explicitly, with shell tracing disabled:

```sh
set +x
set -a
. "$HOME/.config/rolfbot/local.env"
set +a
python -m rolfbot check-config
```

Rolfbot does not load `.env` automatically. `check-config` validates local syntax,
not remote credentials, billing or storage availability. Do not set
`OPENAI_CUSTOM_HEADERS` or `OPENAI_LOG`; the client rejects these settings.

## 3. Provision storage once

For a **new installation only**, after checking the path:

```sh
python -m rolfbot init-db
```

This creates a mode-0600 schema-2 file. The parent must already exist. Repeating
the command validates existing schema 2 and preserves quotas, cooldowns and
service blocks. It will not reset empty, corrupt, old or unknown schemas.

Do not add this command to normal startup or run it to recover a lost database.
Stop and investigate missing historical storage; restoring stale state can
reopen quotas and allow replay. Existing schema-1 files need a backup and explicit
migration as described in [database recovery](../README.md#database-location-and-recovery).

## 4. Run the single billable smoke test

After reviewing the implementation and owner-managed spending controls, the
owner may explicitly run:

```sh
python -m rolfbot smoke-test --confirm-billable
```

This sends one fixed short greeting request through `AIService` and SQLite. It
uses synthetic user ID `1` and a unique `smoke-<UUID>` attempt. Normal 20/day
personal and 100/day global allowances, 30-second cooldown, 512 output tokens,
timeouts and zero SDK retries apply. A denial makes no API call. The flag is
required in full; there is no prompt override or automatic continuation.

Expect one JSON result with `outcome`, `denial`, `accounting`, `input_tokens`,
`output_tokens` and `text`. Success with confirmed accounting exits 0; an AI
failure/denial or unconfirmed completion exits 4. Missing configuration exits 2;
unusable storage or a persistent block exits 3. `null` token counts mean unknown.

Do not repeatedly rerun on errors. Authentication/model-access/billing failures
persistently block the service; fix the underlying cause and use the deliberate
operator recovery described in the README. No command clears that block.
Timeouts or failed completion writes remain potentially billable and counted.
Do not delete the database to retry. The baseline smoke test has already succeeded
according to the owner; report safe outcome/counts if a later mismatch needs investigation.

## 5. Verify the OpenAI project

In the OpenAI dashboard, select **Rolfbot** and verify that the test's time/model
and usage appear under that project. Allow for reporting delay; do not generate
extra requests just to force the dashboard to update. The restricted key decides
project attribution; the application sends no organization/project override.

Confirm the owner-managed $2 enforced monthly project limit, $1 alert, prepaid
balance and disabled auto-recharge are still as intended. This task did not
inspect or alter them. Spending enforcement/reporting may lag and is not an
instantaneous cost guarantee. Record the outcome privately without copying keys
or raw provider errors into the repository.

## 6. Configure one test guild and AI channel

Use the **existing Discord bot account**. In private configuration, add its token
and one guild/channel mapping. The following IDs are synthetic; replace them only
in the private file:

```sh
DISCORD_BOT_TOKEN=replace_with_your_discord_bot_token
ROLFBOT_GUILD_CHANNELS='{"111111111111111111":["222222222222222222"]}'
```

Use an ordinary guild text channel. Threads, DMs and unlisted channels are
rejected. Confirm the bot is installed in that guild with `bot` and
`applications.commands` scopes. Allow **View Channel, Send Messages and Read
Message History** for the bot, and Use Application Commands for the intended
members. Read Message History is required by Discord to send linked replies;
Rolfbot does not fetch history. Keep Message Content, Server Members and Presence
intents disabled; the code requests only nonprivileged `guilds` and `guild_messages`.
There is no new environment variable or privileged-intent toggle to enable.
Do not grant Administrator. No Developer Portal changes were made by this task.
[Discord reply permission](https://docs.discord.com/developers/resources/message#create-message),
[content exception for mentions](https://docs.discord.com/developers/events/gateway#message-content-intent).

Review the application's existing guild commands: startup synchronization will
replace its configured guild command set with `/rolfbot`. Other applications'
commands and global commands are untouched. Reload your private environment as
in step 2, then run `check-config` again.

## 7. Start locally

Run only one bot process, using the same persistent database:

```sh
python -m rolfbot run
```

Startup verifies existing writable storage, schema, integrity, timezone and block
state before opening network clients. It never provisions or migrates storage.
Look for safe `discord_commands_synced` and `discord_ready` events. Registration
runs once per startup, not on reconnect. Startup itself makes no OpenAI request.

## 8. Exercise the slash command

In the allowed test channel, invoke `/rolfbot message:Say hello in one sentence`.
Expect a deferred interaction followed by a short answer in the channel. Wait at
least 30 seconds between accepted requests; an immediate second invocation
should show a cooldown response without another API request. Keep live testing
to a few intentional calls and check project usage again.

Check an unconfigured channel: it should deny access without calling OpenAI.
Mention-like generated text must not notify everyone, users or roles. Ordinary
channel messages should receive no response. Do not deliberately exhaust live
quotas or induce billing errors; those paths have offline coverage.

After successful one-guild validation, enable the second guild by extending only
the private mapping, then restarting:

```sh
ROLFBOT_GUILD_CHANNELS='{"111111111111111111":["222222222222222222"],"333333333333333333":["444444444444444444"]}'
```

The same user's quota/cooldown spans both guilds. A removed guild may retain a
stale command in its UI, but the runtime denies it; any remote cleanup is an owner
action. No scheduler or music is active in either guild.

## 8a. Exercise natural conversations

Use a few deliberate messages in the private test channel, budgeting for history's
additional input tokens. Every accepted turn shares the same 20/user/day,
100 total/day, 30-second cooldown and 512-output-token cap with `/rolfbot`.
Do not exhaust limits or manufacture API failures live; offline tests cover them.

1. Send `@Rolfbot would you rather fly or be invisible?` using Discord's real bot
   mention. Expect typing and a linked reply, with your original message still
   visible and no reply-author ping. Check that ordinary nearby messages receive
   no answer. The bot rejects empty text after mention removal and more than
   1,000 submitted code points before quota use.
2. Have another friend **reply to that Rolfbot answer** with a short follow-up.
   When Discord supplies its content, the answer should use the earlier exchange
   and distinguish the participants. If there is no response, add an explicit
   `@Rolfbot` mention in the reply: content availability without a fresh mention
   is not guaranteed. Do not enable Message Content intent to compensate.
3. After the cooldown, reply to the earlier answer again. This branches from that
   answer; it should not include later sibling turns. A standalone mention, or
   a mention replying to an unrelated message, begins with no history. Another
   allowed channel/guild must not inherit the original context.
4. Keep any generated `@everyone`, user or role mention inert: all replies use
   `AllowedMentions.none()` and `mention_author=False`. The bot never edits/deletes
   the triggering user's message. Bots/webhooks, DMs and threads are ignored.
5. Stop and restart once using the same SQLite file. An explicit mention while
   replying to an old answer should start fresh. A bare reply to a forgotten
   answer is ignored because the bot cannot identify it without fetching history.
   Usage and cooldowns must survive. Expiry after 60 idle minutes and eviction
   from the 64-record cache have the same context behavior; no need to spend
   live calls filling the cache or wait an hour, as fake-clock tests cover both.

Each confirmed bot answer holds an immutable snapshot of up to four completed
user/assistant pairs, capped at 6,000 UTF-8 bytes including participant labels.
Full request text, including personality and current input, is at most 12,288
bytes. Oldest complete pairs are removed first. A pair too large for historical
storage is omitted as a whole; the current prompt is still sent intact. Replying
to an older point cannot import its later branches. At most two message handlers
run at once, with no waiting queue; excess messages may be ignored. A referenced
reply already being processed gets brief busy feedback when capacity allows.

Only successful, confirmed deliveries enter memory. Ambiguous delivery does not
trigger another generation or refund. The SDK's bounded Discord HTTP retries can
still cause an ambiguous visible post; retrying generation is never the recovery.
No transcript is written to disk/logs/SQLite. Explicit text and bounded context
are sent to OpenAI, with session-local participant numbers instead of automatically
added Discord IDs/usernames. Discord retains visible messages independently.
`/rolfbot` remains context-free. No attachments, embeds or channel history are read.

Record safe observations and project usage privately, including whether a reply
without an extra mention worked in this guild. Do not paste private conversation
text, credentials or raw exceptions into the repository. Initial implementation
verification was offline; subsequent owner-driven production checks are recorded
in [DEPLOYMENT.md](DEPLOYMENT.md).

## 9. Stop cleanly

Press Ctrl-C, or send SIGTERM to the bot process. Expect shutdown logging and
closed Discord/OpenAI connections. SIGINT exits 130; SIGTERM exits 143. An
in-flight or ambiguous API request remains counted even if its answer was not
delivered. Typing tasks are joined and conversation snapshots/expiry timers are
cleared. Restart using `run`, **not** `init-db`; quotas and blocks persist.

## Verification and release record

- Implementation/test date: **15 October 2026**.
- Tested Python: **3.12.15 and 3.13.16**, Linux x86_64.
- Direct dependencies: **openai 3.31.0**, **httpx2 2.13.1**, **discord.py 2.7.1**.
- Discord HTTP dependency: **aiohttp 3.14.5**; Python 3.13-only **audioop-lts 0.2.2**.
- Test/build tools: **pytest 9.1.1**, **setuptools 84.0.0**. Full closure is in
  [requirements-dev.lock](../requirements-dev.lock).
- Offline verification: **432 tests passed on each Python version**; source/test
  compilation, dependency installation and `pip check` passed on both. Python
  3.13 used an editable install and Python 3.12 a regular wheel. Import/CLI network
  tripwires, mocked real-SDK requests, SQLite admission checks and
  `git diff --check` passed. No real credentials or live services were used.
- Reviewed application release candidate:
  **`9a4500aa1cc3272471b6492c6c4ec96527ebc7dd`**. It contains the conversation
  enhancement and matched clean `HEAD`, `main` and the local `origin/main` at
  deployment preflight. Deployment-only helpers/documentation are separately
  prepared operational assets, not changes to that application release.
- Baseline live smoke test and private-guild `/rolfbot`: **owner-reported success**.
  The agent has not independently verified those results. Exact test timestamps,
  project attribution, second-guild rollout and live shutdown evidence were not supplied.
- Mention/reply enhancement: **owner-reported local success**, including bare
  reply continuation, isolation and cooldowns. Exact test timestamps were not
  supplied. Later production checks succeeded on 16 October; see the deployment
  evidence record for the owner reports and corresponding accounting/log checks.

Deployment uses the exact reviewed application commit and checksummed artifacts,
never an arbitrary dirty working tree. The existing Vultr website VM now has the
staged release, with 432 application tests passing on Ubuntu Python 3.12.3.
Gate B cutover is complete and production is active/enabled; owner-driven live
acceptance and boot/restart verification passed. Personality refinements,
spontaneous messages and optional music follow deployment; see the [roadmap](ROADMAP.md).
