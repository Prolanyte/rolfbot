# Rolfbot

Rolfbot is a small Python Discord bot for a friend group, intended to provide
helpful, playful AI replies, occasional spontaneous messages, and music in voice
channels. The owner reports that it is installed in two servers, with roughly
10–12 people in the larger one and low expected usage.

**Status: legacy source, not ready to run or deploy.** Phase 0 documents the
existing code and plans its modernization. No bot functionality has been repaired
or verified against live services.

## Historical and intended capabilities

| Capability | What the legacy source contains | Verification status |
| --- | --- | --- |
| AI replies | A textual `/rolfbot` command using OpenAI and a friendly group persona | Not tested live; uses an obsolete SDK interface |
| Music | A textual `/play` command intended to stream a YouTube URL through FFmpeg into Discord voice | Blocked by a syntax error; extraction and playback unverified |
| Periodic messages | A random wait of approximately 2–7 days followed by an AI-generated weather comment | The startup handler is not registered, so the task is not started |

The `/` command prefix is ordinary message text, **not Discord application/slash
commands**. Weather comments have no live weather source.

## Repository and technology

```text
.
├── README.md
├── .gitignore
├── .env.example
├── rolfbot7.py
└── docs/
    ├── ROADMAP.md
    └── YOUTUBE_RESEARCH.md
```

Everything executable currently lives in [rolfbot7.py](rolfbot7.py): environment
lookups and Discord setup, `YTDLSource` media extraction, `play` and its voice
hook, the intended startup/background task, the `rolfbot` AI command, and the
final `bot.run(...)` call.

The script imports `discord.py`, the `openai` Python SDK and `youtube_dl`, alongside
standard-library modules. Music also needs an FFmpeg executable and a compatible
Discord voice stack. External services are Discord, OpenAI's API and YouTube.
There is no dependency manifest, lockfile, supported Python version declaration
or automated test suite. Compatible dependency versions have not been established.

## Configuration and credentials

The script reads these variables from the **process environment**:

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI API authentication |
| `DISCORD_BOT_TOKEN` | Discord bot authentication |

[.env.example](.env.example) contains placeholders only. The legacy code does
**not** load `.env` files; copying this example does not configure or repair the
bot. Planned model and channel settings are documented in the roadmap and are
not supported by the existing script.

Keep real credentials in a protected local environment/secrets file outside
version control. Never paste tokens, cookie exports or payment details into
source, issue reports or logs. `.gitignore` is a convenience, not secret storage:
review staged changes and rotate any credential that is exposed. Future billing
and payment methods belong in OpenAI organization billing, never Python code.

Use `runtime/` for local databases and transient application data, and `downloads/`
for temporary media. These are ignored conventions for future development, not
directories managed by the current script. Keep intentional assets in `assets/`
and test fixtures in `tests/fixtures/`, which remain trackable.

## Known limitations

- A read-only Python parse fails at line 50: the `try` block in `play` is not
  indented correctly. This prevents the entire script from starting.
- `on_ready` lacks event registration. Simply registering it later would also
  require guarding against duplicate periodic tasks after reconnects.
- Both AI paths use synchronous `openai.ChatCompletion.create` calls and a
  hardcoded legacy model. They would block the Discord event loop.
- `YTDLSource` never defines the `title` attribute used by `play`. Its unused
  download mode downloads again and treats a download return value as a filename.
- Voice setup can run before the music-channel check, and an existing connection
  is not checked against the requester's voice channel. There is no queue, idle
  disconnect policy or complete failure/cleanup handling.
- All gateway intents are enabled, and channel IDs are hardcoded. The background
  task does not validate channel lookup results or recover from API/send errors.
- There are no quotas, cooldowns, persistent usage counters, input-length checks,
  credential validation or comprehensive error handling. Responses are not
  bounded to Discord's message length, and raw music exceptions are sent to users.

These findings come from source inspection; they are not an exhaustive runtime
audit. No tested installation procedure can be offered until the syntax,
dependency and API compatibility problems are addressed.

## Modernization

Start with the [prioritized roadmap](docs/ROADMAP.md): establish a testable runtime
and validated configuration, add atomic SQLite quotas, migrate to asynchronous
OpenAI Responses with `gpt-6-luna`, modernize Discord commands and scheduling,
then pass release checks before a separate Vultr deployment milestone.

Music remains conditional on the [YouTube feasibility research](docs/YOUTUBE_RESEARCH.md).
Upstream support does not prove reliable extraction from the intended host.
The roadmap defines future local and VM checks, billing safeguards and owner
decisions before live operation.
