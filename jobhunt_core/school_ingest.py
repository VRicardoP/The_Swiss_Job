"""Persist school observations through the existing corpus sink, never by URL alone.

The school monitor remains the source of contact/alert metadata. The sink owns
offer identity, revisions and merging. Ambiguous URLs remain visible quarantine
and do not leave a false listing attached to the corpus (savepoint rollback).
There is no network I/O here; existing producers submit finished observations.
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import sqlalchemy as sa

from jobhunt_core.harvest.identity import register_extractor
from jobhunt_core.harvest.normalize import register_normalizer
from jobhunt_core.harvest.sink import (
    MAX_URL_LEN,
    RawListingSink,
    _preprocess,
    normalize_url,
)
from jobhunt_core.harvest.types import RawListing

SOURCE_NAME = "school-observation"
SCOPE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "jobhunt:school-observation")
NATIVE_SCHOOL_SOURCE_PREFIX = "swiss_schools_"


def register_handlers():
    register_extractor(SOURCE_NAME, lambda raw: (raw.get("title"), raw.get("company")))
    register_normalizer(SOURCE_NAME, lambda raw: raw)


async def _scope(session):
    await session.execute(
        sa.text(
            "INSERT INTO sources(id,name,tier) VALUES (:id,:name,0) ON CONFLICT(name) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "name": SOURCE_NAME},
    )
    sid = (
        await session.execute(
            sa.text("SELECT id FROM sources WHERE name=:name"), {"name": SOURCE_NAME}
        )
    ).scalar_one()
    await session.execute(
        sa.text(
            "INSERT INTO harvest_scopes(id,source_id,params,tier,enabled) VALUES (:id,:sid,'{}',0,false) "
            "ON CONFLICT(id) DO NOTHING"
        ),
        {"id": SCOPE_ID, "sid": sid},
    )
    owner = (
        await session.execute(
            sa.text("SELECT source_id FROM harvest_scopes WHERE id=:id"),
            {"id": SCOPE_ID},
        )
    ).scalar_one()
    if owner != sid:
        raise RuntimeError("school observation scope has a different source owner")
    # Same lock as RawListingSink: the pre-check cannot race another observation.
    await session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:s,0))"), {"s": str(sid)}
    )
    return sid


def _fold(value):
    return " ".join((value or "").casefold().split())


async def _existing(session, source_id, external_id):
    return (
        (
            await session.execute(
                sa.text(
                    "SELECT v.id,v.archived_at,v.merged_into,r.content,i.url,i.id AS incarnation_id,"
                    "v.primary_incarnation_id FROM source_listings sl "
                    "JOIN source_listing_incarnations i ON i.source_listing_id=sl.id AND i.ended_at IS NULL "
                    "JOIN vacancies v ON v.id=i.vacancy_id "
                    "LEFT JOIN offer_revisions r ON r.id=v.current_offer_revision_id "
                    "WHERE sl.source_id=:s AND sl.external_id=:e"
                ),
                {"s": source_id, "e": external_id},
            )
        )
        .mappings()
        .one_or_none()
    )


def _agrees(row, payload, url):
    return (
        row
        and row["archived_at"] is None
        and row["merged_into"] is None
        and row["content"]
        and row["url"] == url
        and _fold(row["content"].get("title")) == _fold(payload["title"])
        and _fold(row["content"].get("company")) == _fold(payload["company"])
    )


async def link_observation(session, monitor, observation, *, publish_missing=False):
    url = observation.get("url")
    if not url:
        return None, "no_url"
    if len(url.encode()) > MAX_URL_LEN:
        return None, "url_limit"
    if not observation["title"].strip():
        return None, "no_title"
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            return None, "invalid_url"
        normalized = normalize_url(url)
        if len(normalized.encode()) > MAX_URL_LEN:
            return None, "url_limit"
    except ValueError:
        return None, "invalid_url"
    # A careers-page URL does not identify one job, even with a title appended
    # by the extractor. Preserve the observation without inventing a job URL.
    page = monitor["settings"].get("jobs_page_url")
    if page:
        try:
            if normalized == normalize_url(page):
                return None, "listing_page_url"
        except ValueError:
            pass
    payload = {
        "title": observation["title"],
        "company": monitor["settings"]["name"],
        "description": observation.get("description_snippet"),
        "location": monitor["settings"].get("city"),
        "tags": [monitor["external_ref"]],
        "salary": None,
        "remote": None,
    }
    external = hashlib.sha256(normalized.encode()).hexdigest()
    raw = RawListing(external_id=external, url=url, payload=payload, apply_url=url)
    if _preprocess(raw) is None:
        return None, "sink_boundary"
    register_handlers()
    source_id = await _scope(session)
    existing = await _existing(session, source_id, external)
    if existing:
        await session.execute(
            sa.text("SELECT id FROM vacancies WHERE id=:v FOR UPDATE"),
            {"v": existing["id"]},
        )
        existing = await _existing(session, source_id, external)
        if not _agrees(existing, payload, url):
            return None, "url_identity_conflict"
        # A second consumer must not replace a full canonical description with
        # its shorter extraction. Only a LIVE observation refreshes presence.
        if publish_missing:
            await session.execute(
                sa.text(
                    "UPDATE source_listing_incarnations SET last_seen_at=clock_timestamp() "
                    "WHERE id=:id AND ended_at IS NULL"
                ),
                {"id": existing["incarnation_id"]},
            )
        return existing["id"], None
    async with session.begin_nested() as savepoint:
        await RawListingSink().handle(session, str(SCOPE_ID), (raw,))
        linked = await _existing(session, source_id, external)
        if not _agrees(linked, payload, url):
            await savepoint.rollback()
            return None, "url_identity_conflict"
        # The sink holds the vacancy lock through commit. A distinct fragment
        # merged away by URL normalization must not be silently accepted.
        urls = (
            (
                await session.execute(
                    sa.text(
                        "SELECT url FROM source_listing_incarnations WHERE vacancy_id=:v AND ended_at IS NULL"
                    ),
                    {"v": linked["id"]},
                )
            )
            .scalars()
            .all()
        )
        if any(other != url for other in urls):
            await savepoint.rollback()
            return None, "url_identity_conflict"
        if linked["primary_incarnation_id"] != linked["incarnation_id"]:
            # This is only an extension of another source\'s vacancy. Do not
            # leave an extra active incarnation delaying its future retirement.
            await savepoint.rollback()
            return linked["id"], None
        if not publish_missing:
            await savepoint.rollback()
            return None, "awaiting_corpus"
        return linked["id"], None


async def record_observation(
    session,
    monitor,
    values,
    *,
    observation_id=None,
    source_active=True,
    publish_missing=False,
):
    """Caller locks the monitor. Return (id, inserted) without a commit."""
    existing = (
        (
            await session.execute(
                sa.text(
                    "SELECT id,quarantine_reason,metadata FROM school_job_details WHERE monitor_id=:m AND source_ref=:ref"
                ),
                {"m": monitor["id"], "ref": values["dedup_key"]},
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        metadata = dict(existing["metadata"])
        same_title = metadata.get("title") == values["title"]
        old_url = metadata.get("url")
        new_url = values.get("url")
        same_identity = same_title and (not old_url or old_url == new_url)
        enriched = False
        if same_identity:
            for field in ("url", "description_snippet"):
                if not metadata.get(field) and values.get(field):
                    metadata[field] = values[field]
                    enriched = True
            if enriched:
                metadata["content_hash"] = values["content_hash"]
        observation = {**values, **metadata}
        if (
            source_active
            and same_identity
            and (
                publish_missing
                or existing["quarantine_reason"]
                in ("source_inactive", "awaiting_corpus")
            )
        ):
            vacancy_id, reason = await link_observation(
                session, monitor, observation, publish_missing=publish_missing
            )
            metadata["source_active"] = True
            await session.execute(
                sa.text(
                    "UPDATE school_job_details SET vacancy_id=:v,quarantine_reason=:r,"
                    "metadata=CAST(:m AS jsonb),updated_at=clock_timestamp() WHERE id=:id"
                ),
                {
                    "id": existing["id"],
                    "v": vacancy_id,
                    "r": reason,
                    "m": json.dumps(metadata),
                },
            )
        return existing["id"], False
    vacancy_id, reason = (
        await link_observation(
            session, monitor, values, publish_missing=publish_missing
        )
        if source_active
        else (None, "source_inactive")
    )
    jid = observation_id or uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO school_job_details(id,monitor_id,consumer_id,source_ref,vacancy_id,"
            "quarantine_reason,metadata,created_at) VALUES (:id,:m,:c,:ref,:v,:reason,CAST(:data AS jsonb),:ts)"
        ),
        {
            "id": jid,
            "m": monitor["id"],
            "c": monitor["consumer_id"],
            "ref": values["dedup_key"],
            "v": vacancy_id,
            "reason": reason,
            "data": json.dumps(
                {**values, "notified": False, "source_active": source_active},
                default=str,
            ),
            "ts": values["date_detected"],
        },
    )
    return jid, True


def _scored_observation(monitor, values):
    """Apply the authoritative public-job scores for one consumer monitor."""
    from jobhunt_core.school_role import classify_role
    from jobhunt_core.school_urgency import score_urgency

    settings = monitor["settings"]
    description = values.get("description_snippet") or ""
    return {
        **values,
        "role_score": classify_role(values["title"], description),
        "urgency_score": score_urgency(
            settings,
            job_text=f"{values['title']} {description}",
            deadline=values.get("deadline"),
        ),
        "template": settings.get("template_letter"),
    }


async def record_shared_observation(session, monitor, values, *, publish_missing=False):
    """Record one live extraction for every active monitor of the same school.

    The authenticated monitor remains the response identity. Other consumers
    receive only the public observation and their own monitor-derived scores;
    contact and alert settings never cross the tenant boundary.
    """
    await session.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"school-observation:{monitor['school_id']}"},
    )
    monitors = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_monitors WHERE school_id=:school AND "
                    "(id=:source OR (COALESCE((settings->>'is_active')::boolean,false) "
                    "AND settings->>'monitoring_mode'='scrape')) "
                    "ORDER BY (id=:source) DESC,id"
                ),
                {"school": monitor["school_id"], "source": monitor["id"]},
            )
        )
        .mappings()
        .all()
    )
    source_result = None
    vacancy_id = None
    for target in monitors:
        is_source = target["id"] == monitor["id"]
        if not is_source and vacancy_id is not None:
            existing = await session.scalar(
                sa.text(
                    "SELECT id FROM school_job_details "
                    "WHERE monitor_id=:monitor AND vacancy_id=:vacancy LIMIT 1"
                ),
                {"monitor": target["id"], "vacancy": vacancy_id},
            )
            if existing is not None:
                continue
        result = await record_observation(
            session,
            target,
            _scored_observation(target, values),
            publish_missing=publish_missing if is_source else False,
        )
        if is_source:
            source_result = result
            vacancy_id = await session.scalar(
                sa.text("SELECT vacancy_id FROM school_job_details WHERE id=:id"),
                {"id": result[0]},
            )
    if source_result is None:
        raise RuntimeError("source school monitor disappeared during observation")
    return source_result


def _native_source_ref(source_name, external_id, title, detected_at):
    identity = hashlib.sha256(
        f"{source_name}\0{external_id}\0{title}".encode("utf-8")
    ).hexdigest()
    return f"native:{identity}:{detected_at:%Y-%m}"


async def _project_native_observation(session, monitor, vacancy_id, values, cutoff):
    """Attach one native corpus vacancy while preserving recent legacy identity."""
    existing = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_job_details WHERE monitor_id=:monitor AND ("
                    "source_ref=:ref OR vacancy_id=:vacancy OR "
                    "(metadata->>'title'=:title AND created_at>=:cutoff)) "
                    "ORDER BY (source_ref=:ref) DESC,(vacancy_id=:vacancy) DESC,created_at DESC "
                    "LIMIT 1 FOR UPDATE"
                ),
                {
                    "monitor": monitor["id"],
                    "ref": values["dedup_key"],
                    "vacancy": vacancy_id,
                    "title": values["title"],
                    "cutoff": cutoff,
                },
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        metadata = dict(existing["metadata"])
        enriched = False
        for field in ("url", "description_snippet"):
            if not metadata.get(field) and values.get(field):
                metadata[field] = values[field]
                enriched = True
        if enriched:
            metadata["content_hash"] = values["content_hash"]
        metadata["source_active"] = True
        if (
            existing["vacancy_id"] != vacancy_id
            or existing["quarantine_reason"] is not None
            or enriched
            or not existing["metadata"].get("source_active", False)
        ):
            await session.execute(
                sa.text(
                    "UPDATE school_job_details SET vacancy_id=:vacancy,quarantine_reason=NULL,"
                    "metadata=CAST(:metadata AS jsonb),updated_at=clock_timestamp() WHERE id=:id"
                ),
                {
                    "id": existing["id"],
                    "vacancy": vacancy_id,
                    "metadata": json.dumps(metadata, default=str),
                },
            )
        return False

    created = await session.scalar(
        sa.text(
            "INSERT INTO school_job_details(id,monitor_id,consumer_id,source_ref,vacancy_id,"
            "quarantine_reason,metadata,created_at) VALUES "
            "(:id,:monitor,:consumer,:ref,:vacancy,NULL,CAST(:metadata AS jsonb),:detected) "
            "ON CONFLICT(monitor_id,source_ref) DO NOTHING RETURNING id"
        ),
        {
            "id": uuid.uuid4(),
            "monitor": monitor["id"],
            "consumer": monitor["consumer_id"],
            "ref": values["dedup_key"],
            "vacancy": vacancy_id,
            "metadata": json.dumps(
                {**values, "notified": False, "source_active": True}, default=str
            ),
            "detected": values["date_detected"],
        },
    )
    return created is not None


async def project_native_listings(session, source_name, listings):
    """Project native school vacancies to every active monitor of that school."""
    if not source_name.startswith(NATIVE_SCHOOL_SOURCE_PREFIX) or not listings:
        return 0

    by_external = {listing.external_id: listing for listing in listings}
    keys = {
        f"CH:{school_id}"
        for listing in by_external.values()
        if (school_id := listing.payload.get("school_id"))
    }
    if not keys:
        return 0
    monitors = (
        (
            await session.execute(
                sa.text(
                    "SELECT m.*,s.school_key FROM school_monitors m "
                    "JOIN schools s ON s.id=m.school_id "
                    "WHERE s.school_key=ANY(:keys) "
                    "AND COALESCE((m.settings->>'is_active')::boolean,false) "
                    "AND m.settings->>'monitoring_mode'='scrape'"
                ),
                {"keys": sorted(keys)},
            )
        )
        .mappings()
        .all()
    )
    if not monitors:
        return 0

    vacancies = {
        row.external_id: row.vacancy_id
        for row in (
            await session.execute(
                sa.text(
                    "SELECT sl.external_id,i.vacancy_id FROM source_listings sl "
                    "JOIN sources s ON s.id=sl.source_id "
                    "JOIN source_listing_incarnations i ON i.source_listing_id=sl.id "
                    "AND i.ended_at IS NULL WHERE s.name=:source "
                    "AND sl.external_id=ANY(:external_ids)"
                ),
                {"source": source_name, "external_ids": sorted(by_external)},
            )
        ).all()
    }
    if not vacancies:
        return 0

    from jobhunt_core.harvest import normalize

    detected_at = datetime.now(timezone.utc)
    cutoff = detected_at - timedelta(days=30)
    monitors_by_key = {}
    for monitor in monitors:
        monitors_by_key.setdefault(monitor["school_key"], []).append(monitor)

    created = 0
    for external_id, vacancy_id in vacancies.items():
        listing = by_external[external_id]
        school_id = listing.payload.get("school_id")
        content = normalize.normalize_offer(source_name, listing.payload)
        if not isinstance(school_id, str) or content is None:
            continue
        description = content.get("description") or ""
        source_ref = _native_source_ref(
            source_name, external_id, content["title"], detected_at
        )
        base = {
            "source": "official",
            "title": content["title"],
            "url": listing.url,
            "description_snippet": description[:1000] or None,
            "content_hash": normalize.offer_content_hash(content),
            "dedup_key": source_ref,
            "date_detected": detected_at,
            "date_posted": None,
            "deadline": None,
        }
        for monitor in monitors_by_key.get(f"CH:{school_id}", ()):
            created += await _project_native_observation(
                session,
                monitor,
                vacancy_id,
                _scored_observation(monitor, base),
                cutoff,
            )
    return created
