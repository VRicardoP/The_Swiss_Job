"""Persist school observations through the existing corpus sink, never by URL alone.

The school monitor remains the source of contact/alert metadata. The sink owns
offer identity, revisions and merging. Ambiguous URLs remain visible quarantine
and do not leave a false listing attached to the corpus (savepoint rollback).
There is no network I/O here; existing producers submit finished observations.
"""

import hashlib
import json
import uuid
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
        same_identity = existing["metadata"].get("title") == values[
            "title"
        ] and existing["metadata"].get("url") == values.get("url")
        if (
            source_active
            and same_identity
            and existing["quarantine_reason"] in ("source_inactive", "awaiting_corpus")
        ):
            vacancy_id, reason = await link_observation(
                session, monitor, values, publish_missing=publish_missing
            )
            metadata = {**existing["metadata"], "source_active": True}
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
        elif source_active and same_identity and publish_missing:
            await link_observation(session, monitor, values, publish_missing=True)
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
