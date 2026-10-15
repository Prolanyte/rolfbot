"""Deployment preparation stays local/offline and never starts a service."""

import importlib.util
import asyncio
import io
import json
from pathlib import Path
import tarfile
import sqlite3
from contextlib import closing

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


release = module("release")
prepare_env = module("prepare_env")
state = module("state")
COMMIT = "9a4500aa1cc3272471b6492c6c4ec96527ebc7dd"


def artifact(tmp_path, name="src/rolfbot/__init__.py", kind=tarfile.REGTYPE, duplicate=False, pax=False):
    archive = tmp_path / "source.tar"
    data = b"safe application source\n"
    with tarfile.open(archive, "w", format=tarfile.PAX_FORMAT) as tar:
        member = tarfile.TarInfo(name)
        member.type, member.size = kind, len(data) if kind == tarfile.REGTYPE else 0
        if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            member.linkname = "/etc/rolfbot/rolfbot.env"
        if pax:
            member.pax_headers = {"comment": "unexpected"}
        tar.addfile(member, io.BytesIO(data) if member.isfile() else None)
        if duplicate:
            tar.addfile(member, io.BytesIO(data))
    manifest = {
        "commit": COMMIT, "archive_sha256": release.digest(archive.read_bytes()),
        "files": {name: {"sha256": release.digest(data), "size": len(data)}},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return archive, path


def test_build_is_exact_commit_allowlist_and_excludes_worktree_changes(tmp_path):
    result = release.build(ROOT, COMMIT, tmp_path / "artifact")
    assert len(result["files"]) == 16
    assert all(release.allowed(name) for name in result["files"])
    assert "deploy/rolfbot.service" not in result["files"]
    assert not any(".env" in name or ".git" in name or "tests/" in name or "sqlite" in name for name in result["files"])
    extracted = tmp_path / "source"
    release.verify(tmp_path / "artifact/source.tar", tmp_path / "artifact/manifest.json", COMMIT, extracted)
    assert (extracted / "src/rolfbot/__main__.py").is_file()
    with pytest.raises(FileExistsError):
        release.verify(tmp_path / "artifact/source.tar", tmp_path / "artifact/manifest.json", COMMIT, extracted)


@pytest.mark.parametrize("name", ["../escape.py", "/tmp/escape.py", "src/../escape.py", "src/rolfbot/../escape.py", ".env", "usage.sqlite3", "src/rolfbot/secret.env"])
def test_archive_rejects_unapproved_paths_before_extraction(tmp_path, name):
    archive, manifest = artifact(tmp_path, name=name)
    destination = tmp_path / "destination"
    with pytest.raises(ValueError):
        release.verify(archive, manifest, COMMIT, destination)
    assert not destination.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE])
def test_archive_rejects_all_nonregular_entries(tmp_path, kind):
    archive, manifest = artifact(tmp_path, kind=kind)
    with pytest.raises(ValueError):
        release.verify(archive, manifest, COMMIT, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize("failure", ["duplicate", "pax", "checksum", "file_checksum", "commit", "missing"])
def test_archive_checks_complete_manifest_and_content_before_writing(tmp_path, failure):
    archive, manifest = artifact(tmp_path, duplicate=failure == "duplicate", pax=failure == "pax")
    data = json.loads(manifest.read_text())
    if failure == "checksum":
        data["archive_sha256"] = "0" * 64
    elif failure == "file_checksum":
        data["files"]["src/rolfbot/__init__.py"]["sha256"] = "0" * 64
    elif failure == "commit":
        data["commit"] = "0" * 40
    elif failure == "missing":
        data["files"]["src/rolfbot/__main__.py"] = data["files"]["src/rolfbot/__init__.py"]
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        release.verify(archive, manifest, COMMIT, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()


def env_file(tmp_path, extra=""):
    path = tmp_path / "private.env"
    path.write_text(
        "OPENAI_API_KEY=synthetic-offline-credential\n"
        "DISCORD_BOT_TOKEN=synthetic-discord-token\n"
        "ROLFBOT_GUILD_CHANNELS='{\"10\":[\"100\"]}'\n"
        'ROLFBOT_DATABASE_PATH="$HOME/.local/state/rolfbot/usage.sqlite3"\n' + extra
    )
    return path


def test_env_preserves_single_guild_and_replaces_shell_database_path(tmp_path):
    settings, rendered = prepare_env.prepare(env_file(tmp_path))
    assert str(settings.database_path) == "/var/lib/rolfbot/usage.sqlite3"
    assert settings.guild_channels[0].guild_id == "10"
    assert "$HOME" not in rendered
    assert 'ROLFBOT_GUILD_CHANNELS="{\\"10\\":[\\"100\\"]}"\n' in rendered
    assert settings.openai_api_key == "synthetic-offline-credential"


@pytest.mark.parametrize("extra", [
    "OPENAI_LOG=debug\n", "OPENAI_MODEL=expensive\n", "OPENAI_API_KEY=duplicate\n",
    "touch /tmp/never-run-this\n", 'ROLFBOT_LOG_LEVEL="unterminated\n',
])
def test_env_rejects_unsupported_or_ambiguous_assignments(tmp_path, extra):
    with pytest.raises(ValueError):
        prepare_env.prepare(env_file(tmp_path, extra))


def test_env_requires_one_approved_guild(tmp_path):
    path = env_file(tmp_path)
    path.write_text(path.read_text().replace('{"10":["100"]}', '{"10":["100"],"20":["200"]}'))
    with pytest.raises(ValueError):
        prepare_env.prepare(path)


def used_database(tmp_path):
    from test_ai_service import Harness
    from rolfbot.usage import BlockReason, Code, TokenUsage
    harness = Harness(tmp_path / "existing.sqlite3")

    async def populate():
        assert (await harness.store.reserve_user("123", "existing", "not persisted", now=harness.clock.now)).code is Code.RESERVED
        assert (await harness.store.mark_dispatched("existing", now=harness.clock.now)).code is Code.DISPATCH_GRANTED
        await harness.store.complete("existing", now=harness.clock.now, usage=TokenUsage(42, 9), block_reason=BlockReason.BILLING_LIMIT)

    asyncio.run(populate())
    return harness.store.path


def test_backup_preserves_all_accounting_and_block_without_revealing_ids(tmp_path):
    source = used_database(tmp_path)
    before = source.read_bytes()
    destination = tmp_path / "private-backup.sqlite3"
    result = state.backup(source, destination, confirmed_stopped=True)
    assert state.inspect(source) == state.inspect(destination)
    assert source.read_bytes() == before
    assert result["attempts"] == 1 and result["cooldowns"] == 1
    assert result["token_totals"] == (42, 9) and result["service_block_present"]
    assert destination.stat().st_mode & 0o777 == 0o600
    assert "existing" not in json.dumps(result) and "not persisted" not in json.dumps(result)
    with pytest.raises(ValueError):
        state.backup(source, destination, confirmed_stopped=True)


@pytest.mark.parametrize("reason", ["unconfirmed", "sidecar", "locked", "missing", "schema"])
def test_backup_refuses_ambiguous_state_without_creating_destination(tmp_path, reason):
    source = used_database(tmp_path)
    destination = tmp_path / "backup.sqlite3"
    with closing(sqlite3.connect(source)) as db:
        if reason == "sidecar":
            Path(str(source) + "-journal").touch()
        elif reason == "locked":
            db.execute("BEGIN IMMEDIATE")
        elif reason == "missing":
            source = tmp_path / "missing.sqlite3"
        elif reason == "schema":
            db.execute("PRAGMA user_version=1")
        with pytest.raises((ValueError, sqlite3.Error)):
            state.backup(source, destination, confirmed_stopped=reason != "unconfirmed")
    assert not destination.exists()
    if reason == "sidecar":
        assert Path(str(source) + "-journal").exists()
