"""Expire only explicitly registered private project artifacts; dry-run default.

No recursive deletion, discovery-based deletion, symlinks, or wildcard targets.
The caller registers newly verified backups; the newest verified backup of each
database is preserved if a replacement failed. An overdue retained artifact is
an operational failure, not a claim that the retention policy has been met.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import time


def inspect_file(path):
    path = Path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("artifact is not a private regular file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return info, digest
    finally:
        os.close(fd)


def expire(registry, *, now=None, apply=False):
    now = time.time() if now is None else now
    if registry.get("version") != 1 or not isinstance(registry.get("entries"), list):
        raise ValueError("invalid retention registry")
    roots = [Path(r).resolve(strict=True) for r in registry["roots"]]

    def specific_root(root):
        project_temp = root.parent == Path("/tmp") and root.name.startswith(
            ("unification-", "swissjob-")
        )
        return (
            len(root.parts) >= 5 or project_temp
        ) and not root.stat().st_mode & 0o077

    if not roots or not all(specific_root(root) for root in roots):
        raise ValueError("retention roots must be specific private project directories")
    entries = registry["entries"]
    latest = {}
    paths = set()
    for entry in entries:
        path = Path(entry["path"])
        if (
            not path.is_absolute()
            or path.is_symlink()
            or not any(path.resolve().is_relative_to(r) for r in roots)
            or str(path) in paths
        ):
            raise ValueError("invalid or repeated artifact path")
        paths.add(str(path))
        if entry["kind"] not in {"backup", "temporary", "rollback"}:
            raise ValueError("invalid retention class")
        limit = 48 * 3600 if entry["kind"] == "temporary" else 7 * 86400
        if not 0 < entry["expires_at"] - entry["created_at"] <= limit:
            raise ValueError("retention exceeds declared limit")
        if entry["kind"] == "backup" and entry.get("verified") and path.exists():
            db = entry["database"]
            if db not in latest or entry["created_at"] > latest[db]["created_at"]:
                info, digest = inspect_file(path)
                if info.st_size != entry["size"] or digest != entry["sha256"]:
                    raise ValueError("replacement backup is not intact")
                latest[db] = entry
    candidates, overdue = [], 0
    # Validate ALL candidates before unlinking any. A changed file is never
    # interpreted as the old registered backup merely because its name matches.
    for entry in entries:
        if entry["expires_at"] > now or not Path(entry["path"]).exists():
            continue
        if entry["kind"] == "backup" and (
            entry["database"] not in latest
            or latest[entry["database"]]["created_at"] <= entry["created_at"]
        ):
            overdue += 1
            continue
        info, digest = inspect_file(entry["path"])
        if info.st_size != entry["size"] or digest != entry["sha256"]:
            raise ValueError("registered artifact changed; expiry aborted")
        candidates.append((entry, info))
    if apply:
        for entry, original in candidates:
            current = os.stat(entry["path"], follow_symlinks=False)
            if (
                current.st_dev,
                current.st_ino,
                current.st_size,
                current.st_mtime_ns,
            ) != (
                original.st_dev,
                original.st_ino,
                original.st_size,
                original.st_mtime_ns,
            ):
                raise ValueError("artifact replaced during expiry")
            os.unlink(entry["path"])
    return {
        "eligible": len(candidates),
        "deleted": len(candidates) if apply else 0,
        "overdue_blocked": overdue,
        "retention_met": overdue == 0 and (apply or not candidates),
        "scope": "registered_artifacts_only",
    }


def register(registry, path, *, kind, database, created_at, verified=False):
    """Register a completed artifact; re-registering never extends its lifetime."""
    import copy

    value = copy.deepcopy(registry)
    path = str(Path(path).absolute())
    info, digest = inspect_file(path)
    limit = 48 * 3600 if kind == "temporary" else 7 * 86400
    entry = {
        "path": path,
        "kind": kind,
        "database": database,
        "created_at": created_at,
        "expires_at": created_at + limit,
        "size": info.st_size,
        "sha256": digest,
        "verified": verified,
    }
    for previous in value["entries"]:
        if previous["path"] == path:
            if previous != entry:
                raise ValueError("registered artifact metadata cannot change")
            return value
    value["entries"].append(entry)
    expire(value)  # Validate roots, paths and bounds WITHOUT deleting.
    return value


def main():
    import fcntl
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--register", metavar="ARTIFACT")
    parser.add_argument("--kind", choices=["backup", "temporary", "rollback"])
    parser.add_argument("--database")
    parser.add_argument(
        "--created-at", type=float, help="original creation time, Unix seconds"
    )
    parser.add_argument(
        "--verified",
        action="store_true",
        help="operator attests this backup has passed verification",
    )
    args = parser.parse_args()
    if args.register and (
        args.apply or not args.kind or not args.database or args.created_at is None
    ):
        parser.error("registration requires kind/database/created-at, without --apply")
    path = Path(args.registry).absolute()
    if path.parent.stat().st_mode & 0o077:
        raise ValueError("registry directory must be private")
    lock_fd = os.open(
        str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            if os.fstat(stream.fileno()).st_mode & 0o077:
                raise ValueError("registry must be private")
            registry = json.load(stream)
        if args.register:
            value = register(
                registry,
                args.register,
                kind=args.kind,
                database=args.database,
                created_at=args.created_at,
                verified=args.verified,
            )
            fd, temporary = tempfile.mkstemp(prefix=".retention-", dir=path.parent)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(value, stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            result = {"registered": len(value["entries"])}
        else:
            result = expire(registry, apply=args.apply)
    finally:
        os.close(lock_fd)
    print(json.dumps(result))
    return 0 if result.get("retention_met", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
