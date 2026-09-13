"""Source transformation boundaries independent of a NAS or production data."""

import uuid
from datetime import date, datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core.import_schools import SchoolMigrationError, canonical, prepare_batch
from jobhunt_core.school_source import _typed, to_batch


def test_portfolio_mapping_preserves_history_and_never_publishes_missing():
    mid, jid, aid, pid = (uuid.uuid4() for _ in range(4))
    now = datetime.now(timezone.utc)
    source = {
        "schools": [
            {
                "id": mid,
                "school_id": "test-school",
                "name": "School",
                "group_tier": "A",
                "monitoring_mode": "scrape",
                "policy": "portal_only",
                "created_at": now,
                "updated_at": now,
            }
        ],
        "school_jobs": [
            {
                "id": jid,
                "school_id": mid,
                "title": "IT Technician",
                "content_hash": "a" * 64,
                "dedup_key": "historic",
                "url": None,
                "notified": True,
                "notified_at": now,
                "created_at": now,
                "updated_at": now,
                "date_detected": now,
            }
        ],
        "school_applications": [
            {
                "id": aid,
                "user_id": 7,
                "school_id": mid,
                "school_job_id": jid,
                "status": "sent",
                "draft_content": "Keep private",
                "sent_at": now.isoformat(),
                "notes": "Original note",
                "created_at": now,
                "updated_at": now,
            }
        ],
    }
    result = to_batch(
        batch_id=uuid.uuid4(),
        origin="portfolio",
        consumer="portfolio",
        bindings={"7": str(pid)},
        source=source,
    )
    assert result["jobs"][0]["id"] == jid
    assert result["jobs"][0]["notified"] is True
    assert result["jobs"][0]["observation"]["publish_missing"] is False
    assert result["applications"][0]["profile_id"] == pid
    assert result["applications"][0]["context"]["notes"] == "Original note"
    assert result["applications"][0]["draft_content"] == "Keep private"
    # Serialized seals must regenerate the same typed material.
    import json

    assert canonical(
        prepare_batch(json.loads(canonical(result))).model_dump()
    ) == canonical(result)


def test_reverse_types_restore_datetime_date_and_uuid_not_iso_strings():
    meta = sa.MetaData()
    table = sa.Table(
        "history",
        meta,
        sa.Column("id", sa.Uuid),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("deadline", sa.Date),
    )
    uid = uuid.uuid4()
    result = _typed(
        table,
        {
            "id": str(uid),
            "sent_at": "2026-09-13T21:00:00+00:00",
            "deadline": "2026-09-30",
        },
    )
    assert result == {
        "id": uid,
        "sent_at": datetime(2026, 9, 13, 21, tzinfo=timezone.utc),
        "deadline": date(2026, 9, 30),
    }
    with pytest.raises(SchoolMigrationError, match="unknown"):
        _typed(table, {"new_unmapped_private_field": "must not disappear"})


def test_swiss_source_without_explicit_catalog_fails_closed():
    with pytest.raises(SchoolMigrationError, match="configuration snapshot"):
        to_batch(
            batch_id=uuid.uuid4(),
            origin="swissjob",
            consumer="swissjob-shadow",
            bindings={str(uuid.uuid4()): str(uuid.uuid4())},
            source={},
        )
