"""Frozen school migration, preserving UUIDs and material state.

No BFF imports, network calls, emails, or commits. The operator freezes/drains
both sides and seals the complete source batch before calling import_batch.
Replaying an unchanged batch is idempotent; any existing material drift aborts
the ENTIRE import. Rollback exports the current school state back to the source;
it never deletes shared corpus vacancies or their listings.
"""

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import sqlalchemy as sa
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from jobhunt_core import documents, school_ingest, schools
from jobhunt_core.api.deps import ensure_json_storable
from jobhunt_core.api.school_schemas import MonitorCreate
from jobhunt_core.api.v1_school_applications import ApplicationCreate
from jobhunt_core.api.v1_school_jobs import Observation


class SchoolMigrationError(ValueError):
    """Safe diagnostic, never source content or contact information."""


class ImportedMonitor(MonitorCreate):
    id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ImportedJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: uuid.UUID
    monitor_id: uuid.UUID
    observation: Observation
    source_active: bool = Field(default=True, strict=True)
    notified: bool = Field(strict=True)
    notified_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ImportedApplication(ApplicationCreate):
    id: uuid.UUID
    profile_id: uuid.UUID
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ImportedPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    enabled: bool = Field(strict=True)


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: uuid.UUID
    consumer: str = Field(min_length=1)
    monitors: list[ImportedMonitor]
    jobs: list[ImportedJob]
    applications: list[ImportedApplication]
    preferences: list[ImportedPreference]


def canonical(value):
    def convert(item):
        if isinstance(item, datetime):
            if item.tzinfo is None:
                raise SchoolMigrationError("timezone required")
            return item.astimezone(timezone.utc).isoformat()
        if isinstance(item, (uuid.UUID, date)):
            return str(item)
        if isinstance(item, Decimal):
            return float(item)
        raise TypeError("unsupported school snapshot type")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=convert,
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def prepare_batch(raw):
    try:
        batch = Batch.model_validate(raw)
        ensure_json_storable(batch.model_dump(mode="json"))
        for rows, attribute in (
            (batch.monitors, "id"),
            (batch.jobs, "id"),
            (batch.applications, "id"),
            (batch.preferences, "profile_id"),
        ):
            if len({getattr(row, attribute) for row in rows}) != len(rows):
                raise SchoolMigrationError("repeated source identity")
        monitors = {row.id for row in batch.monitors}
        jobs = {row.id: row for row in batch.jobs}
        if len({row.external_ref for row in batch.monitors}) != len(monitors):
            raise SchoolMigrationError("repeated monitor reference")
        if len(
            {(row.monitor_id, row.observation.dedup_key) for row in batch.jobs}
        ) != len(jobs):
            raise SchoolMigrationError("repeated school job reference")
        if len({(row.profile_id, row.source_ref) for row in batch.applications}) != len(
            batch.applications
        ):
            raise SchoolMigrationError("repeated application reference")
        for row in batch.jobs:
            if row.monitor_id not in monitors:
                raise SchoolMigrationError("job monitor absent from source batch")
            for stamp in (row.observation.date_detected, row.observation.date_posted):
                if stamp is not None and stamp.tzinfo is None:
                    raise SchoolMigrationError("timezone required")
        for row in batch.applications:
            if row.monitor_id not in monitors or (
                row.school_job_id is not None
                and (
                    row.school_job_id not in jobs
                    or jobs[row.school_job_id].monitor_id != row.monitor_id
                )
            ):
                raise SchoolMigrationError("invalid application job or monitor")
        return batch
    except SchoolMigrationError:
        raise
    except Exception:
        raise SchoolMigrationError("invalid school source batch") from None


async def _assert_material(session, table, identity, expected):
    # Table names and columns are internal constants, never source input.
    actual = (
        (
            await session.execute(
                sa.text(f"SELECT * FROM {table} WHERE {identity[0]}=:id"),
                {"id": identity[1]},
            )
        )
        .mappings()
        .one_or_none()
    )
    if actual is None or digest({key: actual[key] for key in expected}) != digest(
        expected
    ):
        raise SchoolMigrationError(
            "target identity collision or material drift: " + table
        )
    return digest(expected)


async def import_batch(session, raw):
    batch = prepare_batch(raw)
    inserted = {
        table: []
        for table in (
            "school_monitors",
            "school_job_details",
            "school_applications",
            "school_profile_preferences",
        )
    }
    material = {}
    async with session.begin_nested():
        cid = (
            await session.execute(
                sa.text("SELECT id FROM consumers WHERE name=:name"),
                {"name": batch.consumer},
            )
        ).scalar_one_or_none()
        if cid is None:
            raise SchoolMigrationError("consumer not provisioned")
        pids = {row.profile_id for row in [*batch.applications, *batch.preferences]}
        for pid in sorted(pids):
            if await documents.owner(session, pid, cid, write=True) is None:
                raise SchoolMigrationError("profile binding ownership mismatch")
        # Administrative, short single-writer window; lock profile before monitor
        # in the same order as the API. Never run while producers are active.
        await session.execute(
            sa.text(
                "LOCK TABLE school_monitors,school_job_details,"
                "school_applications,school_profile_preferences IN SHARE ROW EXCLUSIVE MODE"
            )
        )
        for row in batch.monitors:
            values = row.settings.model_dump(mode="json")
            mid = await schools.create_monitor(
                session, cid, row.external_ref, values, monitor_id=row.id
            )
            if mid is not None:
                await session.execute(
                    sa.text(
                        "UPDATE school_monitors SET created_at=:a,updated_at=:b WHERE id=:id"
                    ),
                    {"id": mid, "a": row.created_at, "b": row.updated_at},
                )
                inserted["school_monitors"].append(str(mid))
            shared_id = (
                await session.execute(
                    sa.text("SELECT id FROM schools WHERE school_key=:key"),
                    {"key": schools.school_key(values["country"], row.external_ref)},
                )
            ).scalar_one()
            expected = dict(
                id=row.id,
                consumer_id=cid,
                school_id=shared_id,
                external_ref=row.external_ref,
                settings=values,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            material["monitor:" + str(row.id)] = await _assert_material(
                session, "school_monitors", ("id", row.id), expected
            )
        for row in batch.jobs:
            monitor = await schools.monitor(session, cid, row.monitor_id, write=True)
            values = row.observation.model_dump(exclude={"publish_missing"})
            jid = await session.scalar(
                sa.text(
                    "SELECT id FROM school_job_details WHERE monitor_id=:m AND source_ref=:ref"
                ),
                {"m": row.monitor_id, "ref": row.observation.dedup_key},
            )
            created = False
            if jid is None:
                jid, created = await school_ingest.record_observation(
                    session,
                    monitor,
                    values,
                    observation_id=row.id,
                    source_active=row.source_active,
                    publish_missing=row.observation.publish_missing,
                )
            if jid != row.id:
                raise SchoolMigrationError("target school job reference collision")
            # Match the API's stored wire form, adding the historical delivery flag.
            metadata = json.loads(json.dumps(values, default=str))
            metadata["notified"] = row.notified
            metadata["source_active"] = row.source_active
            if created:
                await session.execute(
                    sa.text(
                        "UPDATE school_job_details SET metadata=CAST(:m AS jsonb),"
                        "notified_at=:n,created_at=:a,updated_at=:b WHERE id=:id"
                    ),
                    {
                        "id": jid,
                        "m": json.dumps(metadata),
                        "n": row.notified_at,
                        "a": row.created_at,
                        "b": row.updated_at,
                    },
                )
                inserted["school_job_details"].append(str(jid))
            expected = dict(
                id=row.id,
                consumer_id=cid,
                monitor_id=row.monitor_id,
                source_ref=row.observation.dedup_key,
                metadata=metadata,
                notified_at=row.notified_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            material["job:" + str(row.id)] = await _assert_material(
                session, "school_job_details", ("id", row.id), expected
            )
        for row in batch.applications:
            values = row.model_dump()
            values["consumer_id"] = cid
            params = {**values, "context": json.dumps(values["context"])}
            aid = (
                await session.execute(
                    sa.text(
                        "INSERT INTO school_applications(id,profile_id,consumer_id,"
                        "monitor_id,school_job_id,source_ref,status,draft_content,context,created_at,updated_at) "
                        "VALUES (:id,:profile_id,:consumer_id,:monitor_id,:school_job_id,:source_ref,:status,"
                        ":draft_content,CAST(:context AS jsonb),:created_at,:updated_at) ON CONFLICT(id) DO NOTHING RETURNING id"
                    ),
                    params,
                )
            ).scalar_one_or_none()
            if aid is not None:
                inserted["school_applications"].append(str(aid))
            material["application:" + str(row.id)] = await _assert_material(
                session, "school_applications", ("id", row.id), values
            )
        for row in batch.preferences:
            pid = (
                await session.execute(
                    sa.text(
                        "INSERT INTO school_profile_preferences(profile_id,enabled) "
                        "VALUES (:profile_id,:enabled) ON CONFLICT(profile_id) DO NOTHING RETURNING profile_id"
                    ),
                    row.model_dump(),
                )
            ).scalar_one_or_none()
            if pid is not None:
                inserted["school_profile_preferences"].append(str(pid))
            material["preference:" + str(row.profile_id)] = await _assert_material(
                session,
                "school_profile_preferences",
                ("profile_id", row.profile_id),
                row.model_dump(),
            )
    return {
        "batch_id": str(batch.batch_id),
        "consumer": batch.consumer,
        "verdict": "verified",
        "source_sha256": digest(batch.model_dump()),
        "inserted": inserted,
        "material_sha256": material,
        "corpus_rollback": "retained; never delete shared vacancies or listings",
    }


async def snapshot(session, consumer):
    """Complete current school state for reverse migration, not the old import receipt."""
    cid = (
        await session.execute(
            sa.text("SELECT id FROM consumers WHERE name=:name"), {"name": consumer}
        )
    ).scalar_one_or_none()
    if cid is None:
        raise SchoolMigrationError("consumer not provisioned")
    result = {}
    for table in ("school_monitors", "school_job_details", "school_applications"):
        rows = (
            (
                await session.execute(
                    sa.text(
                        f"SELECT * FROM {table} WHERE consumer_id=:cid ORDER BY id"
                    ),
                    {"cid": cid},
                )
            )
            .mappings()
            .all()
        )
        result[table] = [dict(row) for row in rows]
    rows = (
        (
            await session.execute(
                sa.text(
                    "SELECT sp.* FROM school_profile_preferences sp "
                    "JOIN profiles p ON p.id=sp.profile_id WHERE p.consumer_id=:cid ORDER BY sp.profile_id"
                ),
                {"cid": cid},
            )
        )
        .mappings()
        .all()
    )
    result["school_profile_preferences"] = [dict(row) for row in rows]
    return result
