"""Build/verify an allowlisted source artifact from Git; never deploy or start it.

The external manifest must arrive through the trusted administrative SSH channel.
Only regular files are accepted. No tarfile extraction methods are used.
"""

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

MAX_FILE = 1024 * 1024
MAX_TOTAL = 5 * 1024 * 1024
MAX_FILES = 100


def allowed(name):
    path = PurePosixPath(name)
    return (
        str(path) == name and not path.is_absolute() and ".." not in path.parts
        and (name in {"README.md", "pyproject.toml", "requirements-dev.lock"}
             or re.fullmatch(r"src/rolfbot/[a-z_]+\.py", name) is not None)
    )


def digest(data):
    return hashlib.sha256(data).hexdigest()


def build(repo, commit, output):
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full reviewed commit SHA is required.")
    git = lambda *args: subprocess.check_output(["git", "-C", str(repo), *args])
    if git("rev-parse", commit + "^{commit}").decode().strip() != commit:
        raise ValueError("Commit mismatch.")
    files = {}
    for item in git("ls-tree", "-rz", commit).split(b"\0"):
        if not item:
            continue
        meta, raw_name = item.split(b"\t", 1)
        name = raw_name.decode("utf-8")
        if not allowed(name):
            continue
        mode, kind, blob = meta.split()
        if mode != b"100644" or kind != b"blob":
            raise ValueError("Nonregular release source.")
        data = git("cat-file", "blob", blob.decode("ascii"))
        if len(data) > MAX_FILE:
            raise ValueError("Source file too large.")
        files[name] = data
    required = {"README.md", "pyproject.toml", "requirements-dev.lock", "src/rolfbot/__init__.py", "src/rolfbot/__main__.py"}
    if not required <= files.keys() or len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_TOTAL:
        raise ValueError("Unexpected release contents.")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    archive = output / "source.tar"
    with tarfile.open(archive, "w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    manifest = {
        "commit": commit, "archive_sha256": digest(archive.read_bytes()),
        "files": {name: {"sha256": digest(data), "size": len(data)} for name, data in sorted(files.items())},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(archive, manifest_path, commit, destination=None):
    if manifest_path.stat().st_size > 64 * 1024 or archive.stat().st_size > MAX_TOTAL + 256 * 1024:
        raise ValueError("Artifact too large.")
    manifest = json.loads(manifest_path.read_text())
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or manifest["commit"] != commit:
        raise ValueError("Commit mismatch.")
    if digest(archive.read_bytes()) != manifest["archive_sha256"]:
        raise ValueError("Archive checksum mismatch.")
    expected = manifest["files"]
    if not isinstance(expected, dict) or not 1 <= len(expected) <= MAX_FILES or any(not allowed(name) for name in expected):
        raise ValueError("Invalid manifest paths.")
    contents = {}
    total = 0
    with tarfile.open(archive, "r:") as tar:
        for member in tar:
            name = member.name
            if (name not in expected or name in contents or not member.isfile()
                    or member.pax_headers or not allowed(name) or member.size < 0 or member.size > MAX_FILE):
                raise ValueError("Unsafe or unapproved archive member.")
            total += member.size
            if total > MAX_TOTAL:
                raise ValueError("Extracted contents too large.")
            data = tar.extractfile(member).read(MAX_FILE + 1)
            if len(data) != expected[name]["size"] or digest(data) != expected[name]["sha256"]:
                raise ValueError("File checksum mismatch.")
            contents[name] = data
    if contents.keys() != expected.keys():
        raise ValueError("Missing release file.")
    if destination is not None:
        # Parent is a pre-created root-owned release directory. The final
        # destination MUST NOT already exist, including as a dangling symlink.
        destination.mkdir(mode=0o755, exist_ok=False)
        for name, data in contents.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o644)
        for name, data in contents.items():
            if digest((destination / name).read_bytes()) != digest(data):
                raise ValueError("Extracted file mismatch.")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("repo", type=Path)
    make.add_argument("commit")
    make.add_argument("output", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("archive", type=Path)
    check.add_argument("manifest", type=Path)
    check.add_argument("commit")
    check.add_argument("--extract", type=Path)
    args = parser.parse_args()
    try:
        result = (build(args.repo, args.commit, args.output) if args.command == "build"
                  else verify(args.archive, args.manifest, args.commit, args.extract))
    except Exception:
        parser.exit(1, "Release preparation/verification failed; nothing activated.\n")
    print(json.dumps({"commit": result["commit"], "files": len(result["files"]), "archive_sha256": result["archive_sha256"]}))


if __name__ == "__main__":
    main()
