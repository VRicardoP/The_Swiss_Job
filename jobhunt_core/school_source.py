"""Frozen source adapters for school migration; no imports across the BFF boundary."""

import hashlib
import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa

from jobhunt_core.import_schools import SchoolMigrationError, digest, prepare_batch

_TABLES = {
    "portfolio": ("schools", "school_jobs", "school_applications"),
    "swissjob": ("jobs", "match_results", "user_profiles"),
}
_JOB_FIELDS = (
    "hash",
    "source",
    "title",
    "company",
    "description",
    "url",
    "tags",
    "first_seen_at",
    "last_seen_at",
    "published_at",
    "is_active",
)
_MATCH_FIELDS = (
    "id",
    "user_id",
    "job_hash",
    "application_status",
    "application_status_at",
    "draft_letter",
    "created_at",
)


def _uid(origin, value):
    return uuid.UUID(str(value)) if origin == "swissjob" else int(value)


async def lock_source(session, origin, bindings, *, authority, schema="public"):
    if (
        origin not in _TABLES
        or not bindings
        or len(set(bindings.values())) != len(bindings)
    ):
        raise SchoolMigrationError("invalid source origin or bindings")
    connection = await session.connection()
    meta = sa.MetaData(schema=schema)
    names = ("users", "jobhunt_routing", *_TABLES[origin])
    if origin == "swissjob":
        names += ("jobhunt_profile_map",)
    tables = await connection.run_sync(
        lambda c: {name: sa.Table(name, meta, autoload_with=c) for name in names}
    )
    if any(not tuple(t.primary_key) for t in tables.values()):
        raise SchoolMigrationError("source copy is missing primary keys")
    await session.execute(sa.text("SET LOCAL lock_timeout='5s'"))
    fmt = connection.dialect.identifier_preparer.format_table
    await session.execute(
        sa.text(f"LOCK TABLE {fmt(tables['jobhunt_routing'])} IN SHARE MODE")
    )
    routing = tables["jobhunt_routing"]
    modes = dict(
        (
            await session.execute(
                sa.select(routing.c.profile_id, routing.c.mode).where(
                    routing.c.consumer_id == origin, routing.c.capability == "schools"
                )
            )
        ).all()
    )
    allowed = (
        {"local", "shadow", "core_read"}
        if authority == "local"
        else {"core_primary", "rollback_pending"}
    )
    if any(
        mode not in allowed
        for mode in [modes.get(uuid.UUID(int=0), "local"), *modes.values()]
    ):
        raise SchoolMigrationError("school authority differs from migration direction")
    owners = sorted((_uid(origin, uid) for uid in bindings), key=str)
    users = tables["users"]
    found = (
        (
            await session.execute(
                sa.select(users.c.id)
                .where(users.c.id.in_(owners))
                .order_by(users.c.id)
                # Protect identity against edits/deletion without excluding
                # authenticated BFF readers, which also acquire FOR SHARE.
                # Source durable writers are frozen and table-locked below.
                .with_for_update(read=True)
            )
        )
        .scalars()
        .all()
    )
    if set(found) != set(owners):
        raise SchoolMigrationError("source owner missing")
    if origin == "swissjob":
        mapping = tables["jobhunt_profile_map"]
        actual = dict(
            (
                await session.execute(
                    sa.select(mapping.c.user_id, mapping.c.core_profile_id)
                    .where(mapping.c.user_id.in_(owners))
                    .with_for_update(read=True)
                )
            ).all()
        )
        if actual != {
            _uid(origin, uid): uuid.UUID(str(pid)) for uid, pid in bindings.items()
        }:
            raise SchoolMigrationError("source profile binding changed")
    required_fks = (
        (
            ("school_jobs", "school_id", "schools"),
            ("school_applications", "school_id", "schools"),
            ("school_applications", "school_job_id", "school_jobs"),
            ("school_applications", "user_id", "users"),
        )
        if origin == "portfolio"
        else (
            ("match_results", "job_hash", "jobs"),
            ("match_results", "user_id", "users"),
            ("user_profiles", "user_id", "users"),
        )
    )
    for table, column, parent in required_fks:
        if not any(
            fk.parent.name == column and fk.column.table.name == parent
            for fk in tables[table].foreign_keys
        ):
            raise SchoolMigrationError("source copy is missing a required foreign key")
    for name in _TABLES[origin]:
        await session.execute(
            sa.text(f"LOCK TABLE {fmt(tables[name])} IN SHARE ROW EXCLUSIVE MODE")
        )
    return tables, owners


async def read_source(session, tables, origin, owners, catalog):
    if origin == "portfolio":
        result = {}
        for name in _TABLES[origin]:
            table = tables[name]
            result[name] = [
                dict(row)
                for row in (
                    await session.execute(sa.select(table).order_by(table.c.id))
                ).mappings()
            ]
        if any(row["user_id"] not in owners for row in result["school_applications"]):
            raise SchoolMigrationError(
                "school application owner has no explicit binding"
            )
        return result
    refs = [row["id"] for row in catalog]
    jobs, matches, profiles = (tables[name] for name in _TABLES[origin])
    rows = (
        (
            await session.execute(
                sa.select(*(jobs.c[name] for name in _JOB_FIELDS))
                .where(
                    sa.or_(
                        jobs.c.tags.op("?|")(sa.cast(refs, sa.ARRAY(sa.Text))),
                        sa.func.starts_with(jobs.c.source, "swiss_schools_"),
                    )
                )
                .order_by(jobs.c.hash)
            )
        )
        .mappings()
        .all()
    )
    hashes = [row["hash"] for row in rows]
    states = (
        (
            await session.execute(
                sa.select(*(matches.c[name] for name in _MATCH_FIELDS))
                .where(matches.c.job_hash.in_(hashes))
                .order_by(matches.c.id)
            )
        )
        .mappings()
        .all()
    )
    if any(row["user_id"] not in owners for row in states):
        raise SchoolMigrationError("watchlist owner has no explicit binding")
    prefs = (
        (
            await session.execute(
                sa.select(profiles.c.user_id, profiles.c.watchlist_schools_enabled)
                .where(profiles.c.user_id.in_(owners))
                .order_by(profiles.c.user_id)
            )
        )
        .mappings()
        .all()
    )
    return {
        "jobs": [dict(row) for row in rows],
        "match_results": [dict(row) for row in states],
        "user_profiles": [dict(row) for row in prefs],
    }


def to_batch(
    *, batch_id, origin, consumer, bindings, source, catalog=None, catalog_stamp=None
):
    mapping = {str(uid): str(pid) for uid, pid in bindings.items()}
    output = {
        "batch_id": batch_id,
        "consumer": consumer,
        "monitors": [],
        "jobs": [],
        "applications": [],
        "preferences": [],
    }
    if origin == "portfolio":
        for row in source["schools"]:
            values = dict(row)
            output["monitors"].append(
                {
                    "id": values.pop("id"),
                    "external_ref": values.pop("school_id"),
                    "created_at": values.pop("created_at"),
                    "updated_at": values.pop("updated_at"),
                    "settings": values,
                }
            )
        for row in source["school_jobs"]:
            values = dict(row)
            item = {
                key: values.pop(key)
                for key in ("id", "created_at", "updated_at", "notified", "notified_at")
            }
            item["monitor_id"] = values.pop("school_id")
            item["observation"] = values
            output["jobs"].append(item)
        for row in source["school_applications"]:
            values = dict(row)
            item = {
                key: values.pop(key)
                for key in (
                    "id",
                    "school_job_id",
                    "status",
                    "draft_content",
                    "created_at",
                    "updated_at",
                )
            }
            item["monitor_id"] = values.pop("school_id")
            item["profile_id"] = mapping[str(values.pop("user_id"))]
            item["source_ref"] = str(item["id"])
            item["context"] = values
            output["applications"].append(item)
    elif origin == "swissjob":
        if not catalog or catalog_stamp is None:
            raise SchoolMigrationError("Swiss school configuration snapshot required")
        mids = {}
        for school in catalog:
            row = dict(school)
            ref = row.pop("id")
            mid = uuid.uuid5(uuid.NAMESPACE_URL, "jobhunt:swissjob:school:" + ref)
            mids[ref] = mid
            row["scraping_method"] = row.pop("strategy")
            row["scraping_params"] = row.pop("params")
            row["jobs_page_url"] = row.pop("careers_url")
            row["template_letter"] = row.pop("template_id")
            row["portal_url"] = row.pop("application_url")
            row.update(
                country="CH",
                is_active=True,
                monitoring_mode=(
                    "manual_only" if row["scraping_method"] == "manual" else "scrape"
                ),
            )
            output["monitors"].append(
                {
                    "id": mid,
                    "external_ref": ref,
                    "settings": row,
                    "created_at": catalog_stamp,
                    "updated_at": catalog_stamp,
                }
            )
        jobs = {}
        for row in source["jobs"]:
            ref = next((tag for tag in row["tags"] or [] if tag in mids), None)
            if ref is None:
                raise SchoolMigrationError("watchlist job has no monitor")
            jid = uuid.uuid5(
                uuid.NAMESPACE_URL, "jobhunt:swissjob:school-job:" + row["hash"]
            )
            jobs[row["hash"]] = (jid, mids[ref], row)
            output["jobs"].append(
                {
                    "id": jid,
                    "monitor_id": mids[ref],
                    "source_active": row["is_active"],
                    "created_at": row["first_seen_at"],
                    "updated_at": row["last_seen_at"],
                    "notified": True,
                    "notified_at": None,  # Swiss alerts remain in its existing notification system.
                    "observation": {
                        "title": row["title"],
                        "url": row["url"],
                        "source": row["source"],
                        "description_snippet": row["description"],
                        "dedup_key": row["hash"],
                        "content_hash": hashlib.sha256(
                            (row["title"] + (row["description"] or "")).encode()
                        ).hexdigest(),
                        "date_detected": row["first_seen_at"],
                        "date_posted": row["published_at"],
                    },
                }
            )
        for row in source["match_results"]:
            jid, mid, job = jobs[row["job_hash"]]
            output["applications"].append(
                {
                    "id": row["id"],
                    "profile_id": mapping[str(row["user_id"])],
                    "monitor_id": mid,
                    "school_job_id": jid,
                    "source_ref": row["job_hash"],
                    "status": row["application_status"],
                    "draft_content": row["draft_letter"],
                    "created_at": row["created_at"],
                    "updated_at": row["application_status_at"],
                    "context": {
                        "detected_at": datetime.fromisoformat(str(row["created_at"]))
                        .astimezone(timezone.utc)
                        .isoformat(),
                        "job_title": job["title"],
                        "job_company": job["company"],
                        "job_url": job["url"],
                    },
                }
            )
        output["preferences"] = [
            {
                "profile_id": mapping[str(row["user_id"])],
                "enabled": row["watchlist_schools_enabled"],
            }
            for row in source["user_profiles"]
        ]
    else:
        raise SchoolMigrationError("unknown source origin")
    return prepare_batch(output).model_dump()


def _typed(table, raw):
    row = dict(raw)
    if set(row) - set(table.columns.keys()):
        raise SchoolMigrationError("reverse mapping has unknown source fields")
    for name, value in row.items():
        typ = table.c[name].type
        if value is None:
            continue
        if isinstance(typ, sa.Uuid):
            row[name] = uuid.UUID(str(value))
        elif isinstance(typ, sa.DateTime) and isinstance(value, str):
            row[name] = datetime.fromisoformat(value)
        elif isinstance(typ, sa.Date) and isinstance(value, str):
            row[name] = date.fromisoformat(value)
    return row


async def reverse_sync(
    session, tables, origin, bindings, current, *, original_monitors=None
):
    """Caller holds source locks and a frozen core snapshot; never deletes core data."""
    # E.15's reverse migrator only owns school status/drafts, not feedback.
    # Once F owns marks (including a deliberate clear), use its coordinated
    # reverse migration and freeze. Never silently discard this new authority.
    if any(row.get("feedback_recorded_at") is not None
           or row.get("feedback") is not None or row.get("feedback_implicit")
           for row in current["school_applications"]):
        raise SchoolMigrationError(
            "core feedback authority requires coordinated feedback rollback"
        )
    owners = {str(pid): _uid(origin, uid) for uid, pid in bindings.items()}
    if any(
        str(row["profile_id"]) not in owners
        for name in ("school_applications", "school_profile_preferences")
        for row in current[name]
    ):
        raise SchoolMigrationError("core snapshot contains an unbound school profile")
    if origin == "portfolio":
        transformed = {name: [] for name in _TABLES[origin]}
        for row in current["school_monitors"]:
            values = {
                **row["settings"],
                "id": row["id"],
                "school_id": row["external_ref"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            # Core-only optional metadata is not a source column. Refuse material
            # new values rather than dropping them in a rollback.
            for key in ("city", "scraping_params"):
                if values.pop(key, None) is not None:
                    raise SchoolMigrationError(
                        "core-only monitor metadata prevents lossless rollback"
                    )
            transformed["schools"].append(values)
        for row in current["school_job_details"]:
            values = {
                **row["metadata"],
                "id": row["id"],
                "school_id": row["monitor_id"],
                "notified_at": row["notified_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            values["notified"] = bool(row["notified_at"]) or values.get(
                "notified", False
            )
            values.pop("source_active", None)
            transformed["school_jobs"].append(values)
        for row in current["school_applications"]:
            transformed["school_applications"].append(
                {
                    **row["context"],
                    "id": row["id"],
                    "user_id": owners[str(row["profile_id"])],
                    "school_id": row["monitor_id"],
                    "school_job_id": row["school_job_id"],
                    "status": row["status"],
                    "draft_content": row["draft_content"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
        # Validate all fields/types before the first DELETE. Source constraints
        # remain enabled; caller's transaction rolls back every partial failure.
        transformed = {
            name: [_typed(tables[name], row) for row in rows]
            for name, rows in transformed.items()
        }
        for name in reversed(_TABLES[origin]):
            await session.execute(sa.delete(tables[name]))
        for name, rows in transformed.items():
            for row in rows:
                await session.execute(sa.insert(tables[name]).values(**row))
        for name, expected in transformed.items():
            actual = [
                dict(row)
                for row in (await session.execute(sa.select(tables[name]))).mappings()
            ]
            def keys(row):
                return str(row["id"])

            wanted = sorted(expected, key=keys)
            obtained = sorted(actual, key=keys)
            if len(wanted) != len(obtained) or any(
                digest(row) != digest({key: other[key] for key in row})
                for row, other in zip(wanted, obtained)
            ):
                raise SchoolMigrationError(
                    "source readback differs after reverse synchronization"
                )
        return {
            "verdict": "verified",
            "rows": {name: len(rows) for name, rows in transformed.items()},
        }
    if original_monitors is None:
        raise SchoolMigrationError("original Swiss monitor snapshot required")
    wanted = {str(row["id"]): row["settings"] for row in original_monitors}
    actual = {str(row["id"]): row["settings"] for row in current["school_monitors"]}
    if digest(wanted) != digest(actual):
        raise SchoolMigrationError(
            "Swiss monitor configuration changed; preserve core until rollback config is prepared"
        )
    matches, jobs, prefs = (
        tables["match_results"],
        tables["jobs"],
        tables["user_profiles"],
    )
    for row in current["school_applications"]:
        uid, ref = owners[str(row["profile_id"])], row["source_ref"]
        if not await session.scalar(sa.select(jobs.c.hash).where(jobs.c.hash == ref)):
            raise SchoolMigrationError(
                "source job missing; preserve core authority until repaired"
            )
        existing = await session.scalar(
            sa.select(matches.c.id).where(
                matches.c.user_id == uid, matches.c.job_hash == ref
            )
        )
        values = _typed(
            matches,
            {
                "application_status": row["status"],
                "draft_letter": row["draft_content"],
                "application_status_at": row["updated_at"],
            },
        )
        if existing is not None:
            await session.execute(
                sa.update(matches).where(matches.c.id == existing).values(**values)
            )
        else:
            await session.execute(
                sa.insert(matches).values(
                    **_typed(
                        matches,
                        {
                            **values,
                            "id": row["id"],
                            "user_id": uid,
                            "job_hash": ref,
                            "created_at": row["created_at"],
                            "score_embedding": 0,
                            "score_salary": 0,
                            "score_location": 0,
                            "score_recency": 0,
                            "score_llm": 0,
                            "score_final": 0,
                            "matching_skills": [],
                            "missing_skills": [],
                        },
                    )
                )
            )
    for row in current["school_profile_preferences"]:
        await session.execute(
            sa.update(prefs)
            .where(prefs.c.user_id == owners[str(row["profile_id"])])
            .values(watchlist_schools_enabled=row["enabled"])
        )
    # Read back material state: an UPDATE matching zero rows is not success.
    for row in current["school_applications"]:
        uid = owners[str(row["profile_id"])]
        actual = (
            await session.execute(
                sa.select(
                    matches.c.application_status,
                    matches.c.draft_letter,
                    matches.c.application_status_at,
                ).where(
                    matches.c.user_id == uid, matches.c.job_hash == row["source_ref"]
                )
            )
        ).one()
        expected = _typed(
            matches,
            {
                "application_status": row["status"],
                "draft_letter": row["draft_content"],
                "application_status_at": row["updated_at"],
            },
        )
        if digest(dict(actual._mapping)) != digest(expected):
            raise SchoolMigrationError("Swiss application readback differs")
    for row in current["school_profile_preferences"]:
        actual = (
            await session.execute(
                sa.select(prefs.c.watchlist_schools_enabled).where(
                    prefs.c.user_id == owners[str(row["profile_id"])]
                )
            )
        ).scalar_one_or_none()
        if actual is not row["enabled"]:
            raise SchoolMigrationError("Swiss preference readback differs")
    return {
        "verdict": "verified",
        "applications": len(current["school_applications"]),
        "preferences": len(current["school_profile_preferences"]),
    }
