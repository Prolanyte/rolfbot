"""Validate private local settings; optionally emit systemd syntax to an SSH pipe.

Never run --emit with stdout attached to a terminal or captured by a tool/chat.
No shell execution, expansion, dotenv discovery, client creation, or file writes.
"""

import argparse
import json
from pathlib import Path
import shlex
import sys

from rolfbot.config import load_settings

NAMES = {
    "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_REASONING_EFFORT", "DISCORD_BOT_TOKEN",
    "ROLFBOT_DATABASE_PATH", "ROLFBOT_GUILD_CHANNELS", "ROLFBOT_LOG_LEVEL", "ROLFBOT_TIMEZONE",
}


def prepare(path):
    values = {}
    for line in path.read_text().splitlines():
        words = shlex.split(line, comments=True, posix=True)
        if not words:
            continue
        if words[0] == "export":
            words.pop(0)
        if len(words) != 1 or "=" not in words[0]:
            raise ValueError("Unsupported environment syntax.")
        name, value = words[0].split("=", 1)
        if name not in NAMES or name in values or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Invalid environment assignment.")
        values[name] = value
    values["ROLFBOT_DATABASE_PATH"] = "/var/lib/rolfbot/usage.sqlite3"
    settings = load_settings(values, require_openai=True, require_discord=True)
    if len(settings.guild_channels) != 1:
        raise ValueError("Select the one owner-approved test guild before transfer.")
    # systemd EnvironmentFile understands double quotes/backslash escaping; it
    # does not expand $HOME, evaluate shell syntax or perform substitutions.
    rendered = "".join(name + '="' + value.replace("\\", "\\\\").replace('"', '\\"') + '"\n'
                       for name, value in sorted(values.items()))
    return settings, rendered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--emit", action="store_true")
    args = parser.parse_args()
    try:
        settings, rendered = prepare(args.source)
        if args.emit:
            if sys.stdout.isatty():
                raise ValueError("Secret output must go directly to a protected SSH pipe.")
            sys.stdout.write(rendered)
        else:
            print(json.dumps({"valid": True, "guilds": len(settings.guild_channels),
                              "channels": sum(len(entry.channel_ids) for entry in settings.guild_channels),
                              "production_database": "/var/lib/rolfbot/usage.sqlite3"}))
    except Exception:
        parser.exit(1, "Environment preparation failed; no private values displayed.\n")


if __name__ == "__main__":
    main()
