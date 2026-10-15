"""Measure the installed runtime with synthetic SQLite and mocked HTTP only.

No production configuration/storage is read, and an audit hook denies network.
May run as the service user under the proposed systemd sandbox during staging.
"""

import asyncio
from contextlib import closing
from dataclasses import replace
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import sqlite3
import sys
import tempfile


def deny_network(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto"}:
        raise RuntimeError("Network forbidden in deployment probe.")


async def probe(directory):
    import httpx2
    from rolfbot.ai_service import AIService
    from rolfbot.config import guild_channels, load_settings
    from rolfbot.conversation import prepare_turn
    from rolfbot.discord_bot import create_client
    from rolfbot.openai_client import Outcome
    from rolfbot.usage import Code
    from rolfbot.usage_store import UsageStore

    store = UsageStore(directory / "synthetic.sqlite3")
    assert (await store.initialize()).code is Code.OK
    settings = replace(load_settings({"OPENAI_API_KEY": "synthetic-offline-credential"}),
                       discord_bot_token="synthetic-discord-token",
                       guild_channels=guild_channels('{"10":["100"]}'))
    calls = 0
    peak_threads = 0
    peak_fds = 0
    both = asyncio.Event()

    async def http(request):
        nonlocal calls, peak_threads, peak_fds
        calls += 1
        with closing(sqlite3.connect(store.path)) as db:
            assert db.execute("SELECT count(*) FROM attempts WHERE state='dispatched'").fetchone()[0] >= 1
        if calls == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=5)
        peak_threads = max(peak_threads, len(list(Path('/proc/self/task').iterdir())))
        peak_fds = max(peak_fds, len(list(Path('/proc/self/fd').iterdir())))
        return httpx2.Response(200, json={
            "status": "completed", "error": None,
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": "Synthetic answer", "annotations": []}]}],
            "usage": {"input_tokens": 42, "output_tokens": 9},
        })

    service = AIService(settings, store, http_transport=httpx2.MockTransport(http))
    async with create_client(settings, service) as client:
        for index in range(64):
            client.message_handler.memory.remember(
                (10, 100, 1000 + index), prepare_turn(None, str(index + 1)),
                str(index) + "界" * 995, "界" * 950,
            )
        results = await asyncio.gather(
            service.generate_user("1", "probe:1", "Synthetic question"),
            service.generate_user("2", "probe:2", "Synthetic question"),
        )
        assert all(result.outcome is Outcome.SUCCESS for result in results)
        assert len(client.message_handler.memory) == 64 and calls == 2
    assert len(client.message_handler.memory) == 0 and not service._active
    return {"python": platform.python_version(), "mocked_generations": calls,
            "memory_records_exercised": 64, "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "observed_threads": peak_threads, "observed_fds": peak_fds,
            "versions": {name: importlib.metadata.version(name) for name in ("openai", "httpx2", "discord.py", "aiohttp")}}


def main():
    for name in list(os.environ):
        if name.startswith(("OPENAI_", "DISCORD_", "ROLFBOT_")):
            del os.environ[name]
    sys.addaudithook(deny_network)
    with tempfile.TemporaryDirectory(prefix="rolfbot-offline-probe-") as temporary:
        print(json.dumps(asyncio.run(probe(Path(temporary)))))


if __name__ == "__main__":
    main()
