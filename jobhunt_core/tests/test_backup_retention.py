"""The expiry tool never deletes an unregistered or changed artifact."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "backup_retention",
    Path(__file__).resolve().parents[2] / "scripts/backup_retention.py",
)
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


def registered(tmp_path, name, *, created=0, verified=True):
    tmp_path.chmod(0o700)
    path = tmp_path / name
    path.write_bytes(b"synthetic archive")
    path.chmod(0o600)
    return {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
        "created_at": created,
        "expires_at": created + 86400,
        "kind": "backup",
        "database": "synthetic",
        "verified": verified,
    }


def test_only_registered_expired_backup_with_intact_replacement_is_removed(tmp_path):
    old = registered(tmp_path, "old.dump")
    new = registered(tmp_path, "new.dump", created=90000)
    untouched = tmp_path / "unregistered.dump"
    untouched.write_bytes(b"not authorized")
    registry = {"version": 1, "roots": [str(tmp_path)], "entries": [old, new]}
    result = retention.expire(registry, now=100000)
    assert result["eligible"] == 1 and not result["retention_met"]
    assert Path(old["path"]).exists()
    result = retention.expire(registry, now=100000, apply=True)
    assert result["deleted"] == 1 and result["retention_met"]
    assert Path(new["path"]).exists() and untouched.exists()
    assert retention.expire(registry, now=100000, apply=True)["deleted"] == 0


def test_private_top_level_project_temp_root_is_supported():
    import tempfile

    with tempfile.TemporaryDirectory(
        prefix="unification-retention-test-", dir="/tmp"
    ) as folder:
        root = Path(folder)
        entry = registered(root, "synthetic.dump")
        entry["kind"] = "temporary"
        result = retention.expire(
            {"version": 1, "roots": [folder], "entries": [entry]},
            now=100000,
            apply=True,
        )
        assert result["deleted"] == 1
    with pytest.raises(ValueError, match="roots"):
        retention.expire({"version": 1, "roots": ["/tmp"], "entries": []})


def test_registration_cannot_extend_retention_or_replace_an_artifact(tmp_path):
    old = registered(tmp_path, "old.dump")
    registry = {"version": 1, "roots": [str(tmp_path)], "entries": []}
    value = retention.register(
        registry, old["path"], kind="temporary", database="synthetic", created_at=0
    )
    assert registry["entries"] == []
    assert value["entries"][0]["expires_at"] == 48 * 3600
    assert (
        retention.register(
            value, old["path"], kind="temporary", database="synthetic", created_at=0
        )
        == value
    )
    with pytest.raises(ValueError, match="cannot change"):
        retention.register(
            value, old["path"], kind="temporary", database="synthetic", created_at=86400
        )


def test_expired_last_backup_is_kept_and_reported_overdue(tmp_path):
    old = registered(tmp_path, "only.dump")
    result = retention.expire(
        {"version": 1, "roots": [str(tmp_path)], "entries": [old]},
        now=100000,
        apply=True,
    )
    assert result["overdue_blocked"] == 1 and not result["retention_met"]
    assert Path(old["path"]).exists()


def test_corrupt_replacement_cannot_justify_deletion(tmp_path):
    old = registered(tmp_path, "old.dump")
    new = registered(tmp_path, "new.dump", created=90000)
    Path(new["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="replacement"):
        retention.expire(
            {"version": 1, "roots": [str(tmp_path)], "entries": [old, new]},
            now=100000,
            apply=True,
        )
    assert Path(old["path"]).exists()


def test_expiry_rejects_symlinks_and_changed_artifacts(tmp_path):
    old = registered(tmp_path, "old.dump")
    old["kind"] = "temporary"
    Path(old["path"]).write_bytes(b"different content")
    registry = {"version": 1, "roots": [str(tmp_path)], "entries": [old]}
    with pytest.raises(ValueError, match="changed"):
        retention.expire(registry, now=100000, apply=True)
    target = tmp_path / "target"
    target.write_bytes(b"preserve")
    Path(old["path"]).unlink()
    Path(old["path"]).symlink_to(target)
    with pytest.raises(ValueError, match="path"):
        retention.expire(registry, now=100000, apply=True)
    assert target.read_bytes() == b"preserve"
