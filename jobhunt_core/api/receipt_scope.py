"""Resolve a durable request's owner before taking its idempotency lock.

A deleted resource may still have a legitimate retry receipt. Its subject is
metadata outside the response body, so a DELETE receipt can be erased too.
Legacy nonempty responses already carry profile_id. Never infer ownership from
an arbitrary resource UUID or disclose cached personal data after profile erase.
"""

import uuid

import sqlalchemy as sa

from jobhunt_core.api.deps import ApiError, ensure_text_storable


async def resource_subject(session, principal, route, key, resource, resource_id):
    if key:
        ensure_text_storable(key, "Idempotency-Key", code="invalid_idempotency_key")
        saved_row = (
            await session.execute(
                sa.text(
                    "SELECT response, COALESCE(response->>'subject_profile_id', response->'body'->>'profile_id') AS subject "
                    "FROM idempotency_records WHERE consumer_id=:cid AND route=:route AND key=:key"
                ),
                {"cid": principal.consumer_id, "route": route, "key": key.strip()},
            )
        ).one_or_none()
        saved = saved_row.subject if saved_row else None
        if (
            saved_row
            and saved is None
            and route.startswith("DELETE ")
            and saved_row.response == {"status": 204, "body": None}
        ):
            # Old content-free DELETE receipts predate subject metadata. Preserve
            # their retry contract without guessing an owner. New receipts carry it.
            return None
        if saved is not None:
            try:
                return uuid.UUID(saved)
            except (ValueError, TypeError, AttributeError):
                raise ApiError(
                    503,
                    "idempotency_subject_unavailable",
                    "recibo sin propietario verificable",
                ) from None
    if resource == "saved_searches":
        query = "SELECT s.profile_id FROM saved_searches s JOIN profiles p ON p.id=s.profile_id WHERE s.id=:id AND p.consumer_id=:cid"
    elif resource == "applications":
        # Same priority as _lock_target: application UUID, then vacancy alias,
        # then a pure bookmark. Do not acquire a child lock before the profile.
        for query in (
            "SELECT a.profile_id FROM applications a JOIN profiles p ON p.id=a.profile_id WHERE a.id=:id AND p.consumer_id=:cid",
            "SELECT a.profile_id FROM applications a JOIN profiles p ON p.id=a.profile_id WHERE a.vacancy_id=:id AND p.consumer_id=:cid",
        ):
            rows = (
                (
                    await session.execute(
                        sa.text(query),
                        {"id": resource_id, "cid": principal.consumer_id},
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) > 1:
                raise ApiError(409, "ambiguous_id", "identidad de candidatura ambigua")
            if rows:
                return rows[0]
        query = "SELECT s.profile_id FROM profile_vacancy_state s JOIN profiles p ON p.id=s.profile_id WHERE s.vacancy_id=:id AND s.saved_at IS NOT NULL AND p.consumer_id=:cid"
    else:
        raise ValueError("unsupported durable resource")
    rows = (
        (
            await session.execute(
                sa.text(query), {"id": resource_id, "cid": principal.consumer_id}
            )
        )
        .scalars()
        .all()
    )
    if len(rows) > 1:
        raise ApiError(409, "ambiguous_id", "identidad de recurso ambigua")
    if not rows:
        raise ApiError(404, "not_found", "recurso inexistente")
    return rows[0]
