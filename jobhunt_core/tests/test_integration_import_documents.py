"""Historical migration, exact material round-trip, atomic failures and post-flip data."""

import asyncio
import copy
import json
from datetime import datetime, timezone
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core import documents
from jobhunt_core.import_documents import (
    DocumentMigrationError,
    _canonical,
    digest,
    import_batch,
    prepare_batch,
    restore_rows,
)
from jobhunt_core.tests.test_integration_api_saved_searches import db, _seed_profile


def source(origin, owner):
    row = {
        "id": uuid.uuid4(),
        "user_id": owner,
        "doc_type": "cv",
        "content": "Exact résumé\n\n  text  ",
        "language": "fr",
        "created_at": datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc),
    }
    if origin == "swissjob":
        row.update(job_hash="a" * 32, job_title="Historical vacancy", job_company=None)
    else:
        row.update(
            application_id=uuid.uuid4(),
            application_snapshot={"title": "Historical vacancy", "tags": ["A", "B"]},
            model_used="historical-model",
            generation_time_ms=123,
        )
    return row


@pytest.mark.parametrize("origin,core_reference", [("swissjob", False), ("swissjob", True), ("portfolio", False)])
def test_import_preserves_history_and_replays_after_lost_ack(db, origin, core_reference):
    factory, created = db
    _, consumer, pid = _seed_profile(factory, created)
    owner = uuid.uuid4() if origin == "swissjob" else 11
    row = source(origin, owner)
    if core_reference:
        row["job_hash"] = str(uuid.uuid4())
    batch = prepare_batch(
        batch_id=uuid.uuid4(),
        origin=origin,
        consumer=consumer,
        bindings={str(owner): pid},
        rows=[row],
    )

    async def run():
        async with factory() as session:
            first = await import_batch(session, batch)
            await session.commit()
        async with factory() as session:
            replay = await import_batch(session, json.loads(_canonical(batch)))
            await session.commit()
            stored = (
                (
                    await session.execute(
                        sa.text(
                            "SELECT * FROM generated_documents WHERE profile_id=:pid"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .all()
            )
            count = await session.scalar(
                sa.text(
                    "SELECT count(*) FROM integration_outbox WHERE subject_profile_id=:pid AND type='document.changed'"
                ),
                {"pid": pid},
            )
        assert first["inserted"] == [str(row["id"])]
        assert replay["inserted"] == [] and replay["already_imported"] == [
            str(row["id"])
        ]
        assert first["documents"] == replay["documents"] and count == 1
        assert stored[0]["created_at"] == row["created_at"]
        restored = restore_rows(stored, origin=origin, bindings={str(owner): pid})
        assert digest(restored) == digest([row])

    asyncio.run(run())


def test_collision_rolls_back_earlier_rows_and_events(db):
    factory, created = db
    _, consumer, pid = _seed_profile(factory, created)
    owner = uuid.uuid4()
    one, two = source("swissjob", owner), source("swissjob", owner)
    one["id"], two["id"] = uuid.UUID(int=1), uuid.UUID(int=2)
    batch = prepare_batch(
        batch_id=uuid.uuid4(),
        origin="swissjob",
        consumer=consumer,
        bindings={str(owner): pid},
        rows=[one, two],
    )

    async def run():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO generated_documents(id,profile_id,doc_type,content,output_hash) VALUES (:id,:pid,'cv','preexisting',:h)"
                ),
                {"id": two["id"], "pid": pid, "h": "a" * 64},
            )
            await s.commit()
            with pytest.raises(DocumentMigrationError, match="collision"):
                await import_batch(s, batch)
            # Even if a caller catches the error and commits, no partial import survives.
            await s.commit()
            assert (
                await s.scalar(
                    sa.text(
                        "SELECT count(*) FROM generated_documents WHERE profile_id=:pid"
                    ),
                    {"pid": pid},
                )
                == 1
            )
            assert (
                await s.scalar(
                    sa.text(
                        "SELECT count(*) FROM integration_outbox WHERE subject_profile_id=:pid"
                    ),
                    {"pid": pid},
                )
                == 0
            )

    asyncio.run(run())


def test_seal_and_cross_consumer_fences(db):
    factory, created = db
    _, consumer, pid = _seed_profile(factory, created)
    owner = uuid.uuid4()
    batch = prepare_batch(
        batch_id=uuid.uuid4(),
        origin="swissjob",
        consumer=consumer,
        bindings={str(owner): pid},
        rows=[source("swissjob", owner)],
    )

    async def run():
        async with factory() as s:
            changed = copy.deepcopy(batch)
            changed["documents"][0]["target"]["content"] = "changed"
            with pytest.raises(DocumentMigrationError, match="seal"):
                await import_batch(s, changed)
            changed["seal"] = digest({k: v for k, v in changed.items() if k != "seal"})
            with pytest.raises(DocumentMigrationError, match="transformation"):
                await import_batch(s, changed)
            foreign = prepare_batch(
                batch_id=uuid.uuid4(),
                origin="swissjob",
                consumer="wrong-consumer",
                bindings={str(owner): pid},
                rows=[source("swissjob", owner)],
            )
            with pytest.raises(DocumentMigrationError, match="ownership"):
                await import_batch(s, foreign)
            await s.commit()
            assert (
                await s.scalar(
                    sa.text(
                        "SELECT count(*) FROM generated_documents WHERE profile_id=:pid"
                    ),
                    {"pid": pid},
                )
                == 0
            )

    asyncio.run(run())


def test_reverse_snapshot_includes_new_writes_and_excludes_deleted_history(db):
    factory, created = db
    _, consumer, pid = _seed_profile(factory, created)
    owner = 11
    old = source("portfolio", owner)
    batch = prepare_batch(
        batch_id=uuid.uuid4(),
        origin="portfolio",
        consumer=consumer,
        bindings={str(owner): pid},
        rows=[old],
    )

    async def run():
        async with factory() as s:
            await import_batch(s, batch)
            await documents.delete(s, pid, old["id"], consumer)
            values = dict(batch["documents"][0]["target"])
            for key in ("id", "profile_id", "created_at", "output_hash"):
                values.pop(key)
            values["context"] = {
                "portfolio_user_id": owner,
                "application_snapshot": {"title": "New after flip"},
            }
            values["content"] = "New after cutover"
            new_id = await documents.create(s, pid, values, consumer)
            await s.commit()
            rows = (
                (
                    await s.execute(
                        sa.text(
                            "SELECT * FROM generated_documents WHERE profile_id=:pid"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .all()
            )
            restored = restore_rows(
                rows, origin="portfolio", bindings={str(owner): pid}
            )
            assert len(restored) == 1 and restored[0]["id"] == new_id
            assert restored[0]["content"] == "New after cutover"
            assert restored[0]["application_snapshot"] == {"title": "New after flip"}

    asyncio.run(run())
