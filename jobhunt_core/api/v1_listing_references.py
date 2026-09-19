"""Resolve an upstream listing identity without a legacy database dependency.

This is an identity lookup, not a search or a preferred-source decision. Multiple
current vacancies for the same reference are ambiguous and fail closed. Archived
identities remain addressable; each consuming operation applies its own eligibility.
"""

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query

from jobhunt_core import applications
from jobhunt_core.api.deps import ApiError, Principal, ensure_json_storable, error_404, get_session, require_scope

router = APIRouter(prefix="/v1")


@router.get("/listing-references")
async def resolve_listing_reference(
    external_id: str = Query(..., min_length=1, max_length=1000),
    source: str | None = Query(None, min_length=1, max_length=200),
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("vacancies:read")),
):
    ensure_json_storable({"external_id": external_id, "source": source})
    rows = (await session.execute(sa.text(
        "SELECT DISTINCT i.vacancy_id FROM source_listings sl "
        "JOIN sources s ON s.id=sl.source_id "
        "JOIN source_listing_incarnations i ON i.source_listing_id=sl.id "
        "WHERE sl.external_id=:ref AND i.ended_at IS NULL "
        "AND (CAST(:src AS text) IS NULL OR s.name=:src) LIMIT 2"
    ), {"ref": external_id, "src": source})).scalars().all()
    if not rows:
        raise error_404("referencia de oferta")
    if len(rows) != 1:
        raise ApiError(409, "ambiguous_reference", "referencia de oferta ambigua")
    vid = await applications.resolve_direct(session, rows[0]) or rows[0]
    return {"vacancy_id": str(vid)}
