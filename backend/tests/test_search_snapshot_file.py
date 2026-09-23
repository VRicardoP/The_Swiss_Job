"""Private, no-overwrite and reproducibly sealed handover artifacts."""

from datetime import datetime, timezone
import hashlib
import json
import stat
import uuid

import pytest

from scripts.capture_search_handover import private_write, seal_snapshot


def test_snapshot_seal_round_trips_types_and_survives_key_order():
    row = {"id": uuid.uuid4(), "at": datetime.now(timezone.utc), "name": "Prüfung"}
    sealed = seal_snapshot(row)
    assert seal_snapshot(dict(reversed(list(row.items())))) == sealed
    body = {key: value for key, value in sealed.items() if key != "seal"}
    assert (
        hashlib.sha256(
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        == sealed["seal"]
    )


def test_private_snapshot_cannot_overwrite_or_follow_a_symlink(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "snapshot.json"
    private_write(path, {"seal": "test"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        private_write(path, {"other": "value"})
    link = tmp_path / "alias.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        private_write(link, {"other": "value"})
    assert json.loads(path.read_text()) == {"seal": "test"}


def test_public_directory_and_naive_timestamp_are_rejected(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        private_write(tmp_path / "private.json", {})
    with pytest.raises(TypeError):
        seal_snapshot({"at": datetime.now()})
