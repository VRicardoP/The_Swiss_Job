"""E.1: immutable document storage and metadata-only integration events.

The caller owns the transaction and acquires profile before document/receipt.
Generation/PDF rendering are NOT done here, particularly not under a DB lock.
Source refs/context preserve BFF associations without a FK to legacy tables.
"""

import hashlib
import json
import uuid

import sqlalchemy as sa

from jobhunt_core import outbox

_FROM = (
    "FROM generated_documents d JOIN profiles p ON p.id=d.profile_id "
    "AND p.consumer_id=:cid WHERE d.profile_id=:pid"
)


async def owner(session, profile_id, consumer_id, *, write=False):
    return (
        await session.execute(
            sa.text(
                "SELECT c.name FROM profiles p JOIN consumers c ON c.id=p.consumer_id "
                "WHERE p.id=:pid AND p.consumer_id=:cid "
                + ("FOR UPDATE OF p" if write else "FOR SHARE OF p")
            ),
            {"pid": profile_id, "cid": consumer_id},
        )
    ).scalar_one_or_none()


async def fetch(session, profile_id, document_id, consumer_id):
    return (
        (
            await session.execute(
                sa.text("SELECT d.* " + _FROM + " AND d.id=:did"),
                {"pid": profile_id, "did": document_id, "cid": consumer_id},
            )
        )
        .mappings()
        .one_or_none()
    )


async def page(session, profile_id, consumer_id, limit, cursor, doc_type, source_ref):
    params = {"pid": profile_id, "cid": consumer_id, "lim": limit + 1}
    sql = "SELECT d.* " + _FROM
    if cursor is not None:
        sql += " AND (d.created_at,d.id) < (:ts,:did)"
        params["ts"], params["did"] = cursor
    if doc_type is not None:
        sql += " AND d.doc_type=:kind"
        params["kind"] = doc_type
    if source_ref is not None:
        sql += " AND d.source_ref=:ref"
        params["ref"] = source_ref
    rows = (
        (
            await session.execute(
                sa.text(sql + " ORDER BY d.created_at DESC,d.id DESC LIMIT :lim"),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = rows[:limit]
    following = (
        (items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
    )
    return items, following


async def emit_changed(session, profile_id, document_id, destination, *, deleted=False):
    version = 2 if deleted else 1
    await outbox.emit(
        session,
        event_type="document.changed",
        natural_key=f"{document_id}:{version}",
        aggregate="document",
        aggregate_id=str(document_id),
        subject_profile_id=profile_id,
        version=version,
        destination=destination,
        payload={
            "document_id": str(document_id),
            "profile_id": str(profile_id),
            "version": version,
            "deleted": deleted,
        },
    )


async def create(session, profile_id, values, destination):
    """None for missing offer; otherwise INSERT + event, without commit."""
    if values["offer_revision_id"] is not None:
        offer = (
            await session.execute(
                sa.text("SELECT id FROM offer_revisions WHERE id=:id FOR KEY SHARE"),
                {"id": values["offer_revision_id"]},
            )
        ).scalar_one_or_none()
        if offer is None:
            return None
    document_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO generated_documents (id,profile_id,offer_revision_id,doc_type,content,"
            "output_hash,language,source_ref,context,model_used,generation_time_ms) "
            "VALUES (:id,:pid,:offer_revision_id,:doc_type,:content,:hash,:language,:source_ref,"
            "CAST(:context AS jsonb),:model_used,:generation_time_ms)"
        ),
        {
            **values,
            "id": document_id,
            "pid": profile_id,
            "hash": hashlib.sha256(values["content"].encode()).hexdigest(),
            "context": json.dumps(values["context"], ensure_ascii=False),
        },
    )
    await emit_changed(session, profile_id, document_id, destination)
    return document_id


async def delete(session, profile_id, document_id, destination):
    await emit_changed(session, profile_id, document_id, destination, deleted=True)
    await session.execute(
        sa.text("DELETE FROM generated_documents WHERE id=:did AND profile_id=:pid"),
        {"did": document_id, "pid": profile_id},
    )
