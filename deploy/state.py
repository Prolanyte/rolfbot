"""Inspect accounting or back up an owner-confirmed stopped instance.

No provisioning, migration, recovery, quota resets or provider-block clearing.
The confirmation flag is an operator attestation, not proof about other hosts.
"""

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def summary(db):
    if db.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise ValueError("Incompatible schema.")
    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise ValueError("Database integrity failure.")
    if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        raise ValueError("Unexpected journal mode.")
    metadata = db.execute("SELECT timezone,ai_block FROM metadata WHERE singleton=1").fetchone()
    if metadata is None or metadata[0] != "Europe/Oslo":
        raise ValueError("Unexpected accounting timezone.")
    fingerprint = hashlib.sha256()
    for table, key in (("metadata", "singleton"), ("attempts", "attempt_id"), ("cooldowns", "user_id")):
        fingerprint.update(table.encode())
        for row in db.execute(f"SELECT * FROM {table} ORDER BY {key}"):
            fingerprint.update(json.dumps(row, separators=(",", ":"), ensure_ascii=True).encode() + b"\n")
    return {
        "schema": 2, "integrity": "ok", "journal_mode": "delete", "timezone": metadata[0],
        "service_block_present": metadata[1] is not None,
        "attempts": db.execute("SELECT count(*) FROM attempts").fetchone()[0],
        "cooldowns": db.execute("SELECT count(*) FROM cooldowns").fetchone()[0],
        "states": dict(db.execute("SELECT state,count(*) FROM attempts GROUP BY state")),
        "token_totals": db.execute("SELECT sum(input_tokens),sum(output_tokens) FROM attempts").fetchone(),
        "rows_sha256": fingerprint.hexdigest(),
    }


def inspect(path):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return summary(db)


def backup(source, destination, *, confirmed_stopped=False):
    if not confirmed_stopped:
        raise ValueError("Stop all instances and confirm quiescence first.")
    if source.is_symlink() or any(Path(str(source) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
        raise ValueError("Ambiguous storage; do not remove sidecars or force recovery.")
    if destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
        raise ValueError("Use a new backup in an existing private directory.")
    # Detect competing SQLite readers/writers before copying. An operator must
    # still prevent process restart throughout cutover; no local lock can prove
    # that the same token is inactive on another host.
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=rw", uri=True, timeout=0.25)) as db:
        db.execute("BEGIN EXCLUSIVE")
        db.rollback()
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as src:
        src.execute("PRAGMA query_only=ON")
        src.execute("BEGIN")
        before = summary(src)
        if before["attempts"] == 0:
            raise ValueError("Expected existing historical accounting.")
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
        if inspect(destination) != before:
            raise ValueError("Backup accounting mismatch; preserve both files for review.")
    if inspect(source) != before:
        raise ValueError("Source changed during backup; cutover must stop.")
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return before | {"backup_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("inspect")
    check.add_argument("source", type=Path)
    copy = commands.add_parser("backup")
    copy.add_argument("source", type=Path)
    copy.add_argument("destination", type=Path)
    copy.add_argument("--confirmed-stopped", action="store_true")
    args = parser.parse_args()
    try:
        result = (inspect(args.source) if args.command == "inspect"
                  else backup(args.source, args.destination, confirmed_stopped=args.confirmed_stopped))
    except Exception:
        parser.exit(1, "Accounting inspection/backup refused; preserve state and investigate.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
