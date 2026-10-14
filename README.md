# Rolfbot

Rolfbot is a small Python Discord bot project for a friend group across two
servers, with roughly 10–12 people in the larger server. Its intended personality
is friendly, playful and useful.

**Milestone 1 delivers a tested offline runtime foundation, not a working bot.**
The package validates configuration and provides safe logging and application
lifecycle handling. OpenAI, Discord connections, scheduled messages, quotas and
music are not implemented or enabled. Supplying credentials does not enable them.

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
│   └── logging_setup.py    # Consistent, redacted stderr logging
├── tests/                  # Offline pytest suite
└── docs/
    ├── ROADMAP.md
    └── YOUTUBE_RESEARCH.md
```

The broken `rolfbot7.py` was removed; its source remains in Git history. No
obsolete OpenAI or YouTube implementation was copied into the new package.
Imports do not start an event loop, configure logging or connect to services.

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

The **47 offline tests** cover settings, missing/placeholder credentials when
required, timezone failures, log/error redaction, module imports, CLI behavior,
refused startup and real SIGINT/SIGTERM delivery to short-lived test processes.
Tests clear service configuration, block socket connections/DNS, and install a
network tripwire in subprocesses. Plugin autoload is disabled by the command
above. pytest's `monkeypatch` and `tmp_path` provide the basis for future fake
clients, injected clocks and temporary SQLite databases; no quota or client
stubs have been implemented prematurely.

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
| `OPENAI_API_KEY` | Optional, unused; captured for redaction and later conditional validation |
| `DISCORD_BOT_TOKEN` | Optional, unused; captured for redaction and later conditional validation |

Missing credentials and example placeholders are safe for offline use. The
configuration loader has explicit `require_openai`/`require_discord` validation
parameters for future components: required credentials must be nonempty,
contain no whitespace and not be obvious placeholders. These parameters do not
create or enable clients, and no CLI command requests them today. Credential
format checks do not establish remote validity. Errors name the setting without
printing its value; settings representations omit credentials.

`OPENAI_MODEL`, `OPENAI_REASONING_EFFORT`, `ROLFBOT_DATABASE_PATH` and
`ROLFBOT_GUILD_CHANNELS` are **future-only and currently ignored**. The approved
future model remains `gpt-6-luna`, initially with reasoning effort `none`; no
model selection, fallback, database access or guild mapping exists in this code.

Application logs use the `rolfbot` logger namespace and one consistently formatted
stderr line per event, suitable for journald. Known configured credential values
are redacted after message formatting. Tracebacks and exception details are
omitted even at DEBUG; prompts are not accepted or logged. Redaction is a defense
against accidental exposure, not permission to log arbitrary sensitive content.
The CLI never dumps the environment or a complete configuration object.

Keep credentials in protected environment/secrets storage outside version
control. A future systemd service will supply environment variables directly;
it must not depend on a repository `.env` file. Never store payment details,
cookie exports or real keys in tracked examples. Review staged changes and
rotate exposed credentials; `.gitignore` is not secret storage.

## Next milestones

The [roadmap](docs/ROADMAP.md) preserves the intended features:

- Helpful, playful AI replies in both servers, using asynchronous OpenAI Responses.
- Persistent, atomic SQLite accounting: 20 AI requests per user/day, 100 globally/day
  across both servers, a 30-second cooldown, 1,000-character input and 512-token
  output limits. Day boundaries use Europe/Oslo.
- Proper Discord slash commands and spontaneous messages approximately every
  2–7 days; generated scheduled messages share the global API allowance.
- Optional voice music, subject to the [YouTube research](docs/YOUTUBE_RESEARCH.md)
  and future host-specific tests.
- Tested release/deployment and owner-managed billing safeguards before live use.

**Next: Milestone 2, persistent usage protection.** Use the existing settings
boundary and offline test infrastructure to develop reservation/recovery rules
before enabling any paid API calls. Keep SQLite state under ignored `runtime/`
and temporary media under `downloads/`; neither directory is created now.
Intentional assets and database fixtures belong in `assets/` and `tests/fixtures/`.
