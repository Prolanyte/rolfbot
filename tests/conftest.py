"""Offline isolation shared by configuration, lifecycle and future unit tests."""

import os
import socket
import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    # Never use a developer's production credentials/configuration in tests.
    for name in os.environ:
        if name.startswith(("OPENAI_", "DISCORD_", "ROLFBOT_")):
            monkeypatch.delenv(name)

    def forbidden(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests")

    # asyncio's local socketpair remains available, but network traffic does not.
    for name in ("connect", "connect_ex", "bind", "sendto"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo"):
        monkeypatch.setattr(socket, name, forbidden)


@pytest.fixture
def child_environment(tmp_path):
    """Install a network tripwire in fresh Python processes, including imports."""
    guard = tmp_path / "guard"
    guard.mkdir()
    attempts = tmp_path / "network-attempts.txt"
    (guard / "sitecustomize.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "def deny_network(event, args):\n"
        "    if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo', 'socket.sendto'}:\n"
        f"        Path({str(attempts)!r}).write_text('attempted')\n"
        "        raise RuntimeError('Network access is forbidden in offline tests')\n"
        "sys.addaudithook(deny_network)\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(guard)
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    yield environment
    assert not attempts.exists(), "A subprocess attempted network access"


@pytest.fixture
def cli_process(child_environment, tmp_path):
    def run(*arguments, extra_env=None):
        return subprocess.run(
            [sys.executable, "-m", "rolfbot", *arguments],
            cwd=tmp_path,
            env=child_environment | (extra_env or {}),
            capture_output=True,
            text=True,
            timeout=10,
        )

    return run
