"""Marks belong to an observed school job, even before it joins the corpus.

No synthetic vacancy, external delivery or update of another profile's state.
The profile lock serializes this writer with watchlist edits and GDPR erasure.
"""

import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request

from jobhunt_core.feedback import set_vacancy_feedback
from jobhunt_core.api.deps import error_404, get_session, require_scope
from jobhunt_core.api.http_contract import json_response, request_hash
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.api.v1_feedback import FeedbackWrite, ImplicitWrite

router = APIRouter(prefix="/v1/profiles/{profile_id}/school-jobs", tags=["schools"])


async def _write(session, principal, profile_id, school_job_id, request, body, implicit):
    async def handler():
        job = (await session.execute(sa.text(
            "SELECT j.*,s.name AS school_name FROM school_job_details j "
            "JOIN school_monitors m ON m.id=j.monitor_id AND m.consumer_id=j.consumer_id "
            "JOIN schools s ON s.id=m.school_id WHERE j.id=:j AND j.consumer_id=:c"
        ), {"j": school_job_id, "c": principal.consumer_id})).mappings().one_or_none()
        if job is None:
            raise error_404("oferta escolar")
        # Establish the existing watchlist identity without changing its status,
        # draft or context. Even a concurrent old-style create cannot overwrite it.
        metadata = job["metadata"]
        await session.execute(sa.text(
            "INSERT INTO school_applications "
            "(id,profile_id,consumer_id,monitor_id,school_job_id,source_ref,status,context) "
            "VALUES (:id,:p,:c,:m,:j,:ref,'detected',CAST(:ctx AS jsonb)) "
            "ON CONFLICT(profile_id,source_ref) DO NOTHING"
        ), {"id": uuid.uuid4(), "p": profile_id, "c": principal.consumer_id,
            "m": job["monitor_id"], "j": school_job_id, "ref": job["source_ref"],
            "ctx": json.dumps({"job_title": metadata.get("title"),
                               "job_company": job["school_name"], "job_url": metadata.get("url"),
                               "detected_at": metadata.get("date_detected")})})
        data = body.model_dump(exclude_none=True) if implicit else body.model_dump()
        if implicit:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
            assignment = "feedback_implicit=feedback_implicit || CAST(:value AS jsonb)"
            value = json.dumps([data])
        else:
            assignment = "feedback=:value,feedback_recorded_at=clock_timestamp()"
            value = body.feedback
        # The existing source_ref must belong to this observation, not merely
        # match a string from another monitor. Do not silently re-parent a draft.
        result = await session.execute(sa.text(
            f"UPDATE school_applications SET {assignment},version=version+1,"
            "updated_at=GREATEST(updated_at,clock_timestamp()),school_job_id=:j "
            "WHERE profile_id=:p AND source_ref=:ref AND monitor_id=:m "
            "AND (school_job_id IS NULL OR school_job_id=:j) RETURNING id"
        ), {"value": value, "p": profile_id, "j": school_job_id,
            "ref": job["source_ref"], "m": job["monitor_id"]})
        if result.scalar_one_or_none() is None:
            from jobhunt_core.api.deps import ApiError
            raise ApiError(409, "school_identity_conflict", "la referencia pertenece a otro estado escolar")
        if not implicit and job["vacancy_id"] is not None:
            await set_vacancy_feedback(session, profile_id, job["vacancy_id"], body.feedback)
        field = "action" if implicit else "feedback"
        return 200, {"profile_id": str(profile_id), "school_job_id": str(school_job_id),
                     field: getattr(body, field)}

    code, payload = await run_idempotent(
        session, principal, f"{request.method} {request.url.path}",
        request_hash(body.model_dump()), request.headers.get("idempotency-key"),
        handler, profile_id=profile_id,
    )
    return json_response(code, payload)


@router.put("/{school_job_id}/feedback")
async def set_feedback(
    profile_id: uuid.UUID, school_job_id: uuid.UUID, body: FeedbackWrite, request: Request,
    session=Depends(get_session), principal=Depends(require_scope("schools:write")),
):
    return await _write(session, principal, profile_id, school_job_id, request, body, False)


@router.post("/{school_job_id}/implicit")
async def record_implicit(
    profile_id: uuid.UUID, school_job_id: uuid.UUID, body: ImplicitWrite, request: Request,
    session=Depends(get_session), principal=Depends(require_scope("schools:write")),
):
    return await _write(session, principal, profile_id, school_job_id, request, body, True)
