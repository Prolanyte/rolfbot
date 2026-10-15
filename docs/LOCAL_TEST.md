# Local OpenAI and Discord validation

The minimal MVP is implemented and tested offline. **Live OpenAI access, project
attribution and Discord delivery are not yet verified.** These steps are for the
owner after reviewing the code. The agent has not executed live commands, read
real credentials, changed Discord settings, or accessed Vultr. Do not deploy yet.

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
Do not delete the database to retry. No compatibility claim depends on a live
result yet; report safe outcome/counts if a provider mismatch needs investigation.

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
`applications.commands` scopes. Allow View Channel and Send Messages for the bot,
and Use Application Commands for the intended members. Do not grant
Administrator or enable privileged gateway intents; only the nonprivileged
guilds intent is requested. No Developer Portal changes were made by this task.

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

## 9. Stop cleanly

Press Ctrl-C, or send SIGTERM to the bot process. Expect shutdown logging and
closed Discord/OpenAI connections. SIGINT exits 130; SIGTERM exits 143. An
in-flight or ambiguous API request remains counted even if its answer was not
delivered. Restart using `run`, **not** `init-db`; quotas and blocks persist.

## Verification and release record

- Implementation/test date: **15 October 2026**.
- Tested Python: **3.12.15 and 3.13.16**, Linux x86_64.
- Direct dependencies: **openai 3.31.0**, **httpx2 2.13.1**, **discord.py 2.7.1**.
- Discord HTTP dependency: **aiohttp 3.14.5**; Python 3.13-only **audioop-lts 0.2.2**.
- Test/build tools: **pytest 9.1.1**, **setuptools 84.0.0**. Full closure is in
  [requirements-dev.lock](../requirements-dev.lock).
- Offline verification: **352 tests passed on each Python version**; source/test
  compilation, dependency installation and `pip check` passed on both. Python
  3.13 used an editable install and Python 3.12 a regular wheel. Import/CLI network
  tripwires, mocked real-SDK requests, SQLite admission checks and
  `git diff --check` passed. No real credentials or live services were used.
- Base commit: `d06fde8be6304bd97393dc5a226d028e9e540f01` (Milestone 3).
- **Intended deployment commit SHA: pending an owner-authorized commit of this
  MVP.** The base SHA does not contain these uncommitted changes and must not be
  presented as the MVP release. No commit or push was authorized in this task.
- Live smoke test, project attribution, Discord delivery and shutdown: **pending
  owner verification**. Record date, safe outcome/counts and guild rollout status.

Before later deployment, commit the reviewed implementation with separate
authorization, record the resulting `git rev-parse HEAD` as the intended release
SHA, and verify that exact clean revision. Deployment to the existing Vultr
website VM is a separate task after local acceptance. Personality refinements,
spontaneous messages and optional music follow deployment; see the [roadmap](ROADMAP.md).
