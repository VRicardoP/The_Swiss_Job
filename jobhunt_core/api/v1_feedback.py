"""Feedback on native corpus identities, independent of a BFF's local Job.

Uses the existing profile state/events and applications write authority. Profile
lock, receipt and mutation share one transaction; implicit events never overwrite
explicit feedback or the evaluator's current_eval_id. No legacy writer is enabled
or disabled by installing these endpoints.
"""

import json
import uuid
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from jobhunt_core import applications
from jobhunt_core.feedback import effective_feedback_sql, effective_feedback_batch_sql, set_vacancy_feedback
from jobhunt_core.api.deps import ApiError, Principal, error_404, get_session, require_scope
from jobhunt_core.api.http_contract import WRITE_RESPONSES, json_response, request_hash
from jobhunt_core.api.idempotency import run_idempotent

router = APIRouter(prefix="/v1")
MAX_FEEDBACK_CONTEXT = 50000


class FeedbackWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: Literal["thumbs_up", "thumbs_down", "applied", "dismissed"] | None


class ImplicitWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["opened", "view_time", "saved", "applied", "dismissed", "skipped"]
    duration_ms: int | None = Field(default=None, ge=0, le=9223372036854775807, strict=True)


async def _target(session, profile_id, vacancy_id):
    vid = await applications.resolve_direct(session, vacancy_id, profile_id)
    if vid is None:
        # Clearing one's own historical feedback must remain possible after an
        # archive. This does not reopen a vacancy in the shared corpus/feed.
        vid = await session.scalar(sa.text(
            "SELECT v.id FROM vacancies v JOIN profile_vacancy_state s ON s.vacancy_id=v.id "
            "WHERE v.id=:v AND s.profile_id=:p AND v.merged_into IS NULL "
            "AND (s.feedback IS NOT NULL OR s.dismissed_at IS NOT NULL)"
        ), {"v": vacancy_id, "p": profile_id})
    if vid is None:
        raise error_404("vacante")
    return vid


async def _event(session, profile_id, vacancy_id, kind, data):
    await session.execute(sa.text(
        "INSERT INTO profile_vacancy_events (profile_id,vacancy_id,kind,data,created_at) "
        "VALUES (:p,:v,:kind,CAST(:data AS jsonb),clock_timestamp())"
    ), {"p": profile_id, "v": vacancy_id, "kind": kind, "data": json.dumps(data)})


@router.put("/profiles/{profile_id}/vacancies/{vacancy_id}/feedback", responses=WRITE_RESPONSES)
async def set_feedback(
    profile_id: uuid.UUID, vacancy_id: uuid.UUID, body: FeedbackWrite, request: Request,
    session=Depends(get_session), principal: Principal = Depends(require_scope("applications:write")),
):
    async def handler():
        vid = await _target(session, profile_id, vacancy_id)
        await set_vacancy_feedback(session, profile_id, vid, body.feedback)
        await _event(session, profile_id, vid, "feedback", body.model_dump())
        return 200, {"profile_id": str(profile_id), "vacancy_id": str(vid), "feedback": body.feedback}

    status, payload = await run_idempotent(
        session, principal, f"PUT {request.url.path}", request_hash(body.model_dump()),
        request.headers.get("idempotency-key"), handler, profile_id=profile_id,
    )
    return json_response(status, payload)


@router.post("/profiles/{profile_id}/vacancies/{vacancy_id}/implicit", responses=WRITE_RESPONSES)
async def record_implicit(
    profile_id: uuid.UUID, vacancy_id: uuid.UUID, body: ImplicitWrite, request: Request,
    session=Depends(get_session), principal: Principal = Depends(require_scope("applications:write")),
):
    async def handler():
        vid = await _target(session, profile_id, vacancy_id)
        await _event(session, profile_id, vid, "implicit", body.model_dump(exclude_none=True))
        return 200, {"profile_id": str(profile_id), "vacancy_id": str(vid), "action": body.action}

    status, payload = await run_idempotent(
        session, principal, f"POST {request.url.path}", request_hash(body.model_dump()),
        request.headers.get("idempotency-key"), handler, profile_id=profile_id,
    )
    return json_response(status, payload)


@router.get("/profiles/{profile_id}/feedback")
async def list_positive_feedback(
    profile_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
    session=Depends(get_session), principal: Principal = Depends(require_scope("matches:read")),
):
    """SwissJob's saved feed is positive feedback, not application bookmarks.

    Preserve historical marks, even without a current evaluation or on an
    archived vacancy. Count and page share a single statement snapshot. Ownership
    is checked again inside the statement, not just by the initial 404 check.
    """
    owner = await applications.profile_owner(session, profile_id, principal.consumer_id)
    if owner is None:
        raise error_404("perfil")
    school_feedback = (
        "CASE WHEN j.vacancy_id IS NULL THEN a.feedback ELSE ("
        + effective_feedback_sql("j.vacancy_id", "a.profile_id") + ") END"
    )
    row = (await session.execute(sa.text(
        "WITH marked AS MATERIALIZED ("
        " SELECT s.vacancy_id,CAST(NULL AS uuid) AS school_job_id,s.vacancy_id::text AS job_ref,"
        " s.feedback,s.updated_at,"
        " COALESCE(e.score_final,0) AS score_final,COALESCE(e.scores,'{}'::jsonb) AS scores,"
        " e.explanation,o.content,src.name AS source,i.url FROM profile_vacancy_state s"
        " JOIN profiles p ON p.id=s.profile_id AND p.consumer_id=:cid"
        " LEFT JOIN match_evaluations e ON e.id=s.current_eval_id"
        " AND e.profile_id=s.profile_id AND e.vacancy_id=s.vacancy_id"
        " JOIN vacancies v ON v.id=s.vacancy_id"
        " LEFT JOIN offer_revisions o ON o.id=v.current_offer_revision_id"
        " LEFT JOIN source_listing_incarnations i ON i.id=v.primary_incarnation_id"
        " LEFT JOIN source_listings sl ON sl.id=i.source_listing_id"
        " LEFT JOIN sources src ON src.id=sl.source_id"
        " WHERE s.profile_id=:pid AND s.feedback IN ('thumbs_up','applied')"
        " AND NOT EXISTS (SELECT 1 FROM school_applications a JOIN school_job_details j"
        " ON j.id=a.school_job_id WHERE a.profile_id=:pid AND j.vacancy_id=s.vacancy_id"
        " AND (a.feedback_recorded_at IS NOT NULL OR a.feedback IS NOT NULL))"
        f" UNION ALL SELECT j.vacancy_id,j.id,a.source_ref,({school_feedback}),a.updated_at,"
        " COALESCE(e.score_final,0),COALESCE(e.scores,'{}'::jsonb),e.explanation,"
        " jsonb_build_object('title',j.metadata->>'title','company',sch.name,"
        " 'description',j.metadata->>'description_snippet','tags',jsonb_build_array(m.external_ref)),"
        " j.metadata->>'source',j.metadata->>'url'"
        " FROM school_applications a JOIN profiles p ON p.id=a.profile_id AND p.consumer_id=:cid"
        " JOIN school_job_details j ON j.id=a.school_job_id"
        " JOIN school_monitors m ON m.id=a.monitor_id JOIN schools sch ON sch.id=m.school_id"
        " LEFT JOIN profile_vacancy_state s ON s.profile_id=a.profile_id AND s.vacancy_id=j.vacancy_id"
        " LEFT JOIN match_evaluations e ON e.id=s.current_eval_id"
        " AND e.profile_id=s.profile_id AND e.vacancy_id=s.vacancy_id"
        " WHERE a.profile_id=:pid AND (a.feedback_recorded_at IS NOT NULL OR a.feedback IS NOT NULL)"
        f" AND ({school_feedback}) IN ('thumbs_up','applied')"
        "), page AS ("
        " SELECT * FROM marked ORDER BY score_final DESC,job_ref LIMIT :lim OFFSET :off"
        ") SELECT (SELECT count(*) FROM marked) AS total,"
        " COALESCE((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.score_final DESC,p.job_ref)"
        " FROM page p),'[]'::jsonb) AS items"
    ), {"pid": profile_id, "cid": principal.consumer_id, "lim": limit, "off": offset})).one()
    return {"items": row.items, "total": row.total}


@router.get("/profiles/{profile_id}/feedback-context")
async def feedback_context(
    profile_id: uuid.UUID,
    session=Depends(get_session), principal: Principal = Depends(require_scope("matches:read")),
):
    """Single-snapshot evidence for the BFF's existing rejection-pattern analysis.

    One item per corpus vacancy, plus unlinked school observations. Include
    unmarked history (the rejection-rate denominator) and archived vacancies.
    Never return a truncated sample disguised as the whole collection. Only
    title/company/tags/feedback cross this boundary, not raw listings or CVs.
    """
    if await applications.profile_owner(session, profile_id, principal.consumer_id) is None:
        raise error_404("perfil")
    # Manual whole-history analysis has a bounded budget distinct from feed reads.
    await session.execute(sa.text("SET LOCAL statement_timeout='40s'"))
    effective = effective_feedback_batch_sql(":pid")
    rows = (await session.execute(sa.text(
        "WITH owned AS MATERIALIZED (SELECT id FROM profiles WHERE id=:pid AND consumer_id=:cid),"
        " targets AS (SELECT s.vacancy_id FROM profile_vacancy_state s JOIN owned p ON p.id=s.profile_id"
        " UNION SELECT j.vacancy_id FROM school_applications a JOIN owned p ON p.id=a.profile_id"
        " JOIN school_job_details j ON j.id=a.school_job_id WHERE j.vacancy_id IS NOT NULL),"
        f" latest AS MATERIALIZED ({effective}),"
        " context AS (SELECT 'vacancy:'||v.id::text AS identity,"
        " COALESCE(o.content->>'title','') AS title,COALESCE(o.content->>'company','') AS company,"
        " COALESCE(o.content->'tags','[]'::jsonb) AS tags,"
        " latest.feedback FROM targets t JOIN vacancies v ON v.id=t.vacancy_id"
        " LEFT JOIN latest ON latest.vacancy_id=v.id"
        " LEFT JOIN offer_revisions o ON o.id=v.current_offer_revision_id WHERE v.merged_into IS NULL"
        " UNION ALL SELECT 'school:'||j.id::text,COALESCE(j.metadata->>'title',''),"
        " COALESCE(sch.name,''),jsonb_build_array(m.external_ref),a.feedback"
        " FROM school_applications a JOIN owned p ON p.id=a.profile_id"
        " JOIN school_job_details j ON j.id=a.school_job_id"
        " JOIN school_monitors m ON m.id=a.monitor_id JOIN schools sch ON sch.id=m.school_id"
        " WHERE j.vacancy_id IS NULL)"
        " SELECT * FROM context ORDER BY identity LIMIT :cap"
    ), {"pid":profile_id, "cid":principal.consumer_id, "cap": MAX_FEEDBACK_CONTEXT + 1})).mappings().all()
    if len(rows) > MAX_FEEDBACK_CONTEXT:
        raise ApiError(503, "feedback_context_too_large", "El análisis requiere un contexto completo más pequeño")
    return {"items":[dict(row) for row in rows]}
