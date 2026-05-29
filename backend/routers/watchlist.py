"""Endpoints específicos de la watchlist de colegios suizos.

Cubre:
- Transición del state machine de candidatura (POST /status)
- Generación de borrador de carta (POST /draft)
- Exportación de eventos de calendario (.ics) (GET /calendar.ics)
- Listado de colegios vigilados con metadata (GET /schools)
"""

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.rate_limit import limiter
from core.security import get_current_user
from database import get_db
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from schemas.match import (
    ApplicationStatusRequest,
    ApplicationStatusResponse,
    GenerateDraftRequest,
    GenerateDraftResponse,
)
from scrapers.swiss_schools_config import (
    SCHOOLS,
    resolve_school_from_job,
)
from services.groq_service import GroqService
from services.letter_generator import generate_draft_letter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])


def _get_groq(request: Request) -> GroqService:
    redis_client = getattr(request.app.state, "redis_client", None)
    return GroqService(redis_client=redis_client)


# ── Listado de colegios ────────────────────────────────────────────────────


@router.get("/schools")
async def list_schools(current_user: User = Depends(get_current_user)):
    """Devuelve la lista de colegios vigilados con metadata pública."""
    return {
        "schools": [
            {
                "id": s.id,
                "name": s.name,
                "city": s.city,
                "group_tier": s.group_tier,
                "policy": s.policy,
                "contact_email": s.contact_email,
                "contact_name": s.contact_name,
                "template_id": s.template_id,
                "application_url": s.application_url,
                "careers_url": s.careers_url,
                "notes": s.notes,
            }
            for s in SCHOOLS
        ]
    }


# ── State machine de candidatura ───────────────────────────────────────────


@router.post(
    "/match/{job_hash}/status", response_model=ApplicationStatusResponse
)
async def update_application_status(
    job_hash: str,
    body: ApplicationStatusRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Actualiza el estado de la candidatura en el state machine."""
    stmt = select(MatchResult).where(
        MatchResult.user_id == current_user.id,
        MatchResult.job_hash == job_hash,
    )
    match = (await db.execute(stmt)).scalar_one_or_none()
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Match result not found",
        )

    match.application_status = body.application_status
    match.application_status_at = datetime.now(timezone.utc)
    await db.commit()

    return ApplicationStatusResponse(
        status="success",
        job_hash=job_hash,
        application_status=body.application_status,
    )


# ── Generación de borrador ─────────────────────────────────────────────────


@router.post(
    "/match/{job_hash}/draft", response_model=GenerateDraftResponse
)
@limiter.limit("10/minute")
async def generate_draft(
    job_hash: str,
    body: GenerateDraftRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Genera borrador de carta de presentación usando plantilla del colegio.

    Si el job no pertenece a un colegio de la watchlist devuelve 400.
    El borrador se persiste en MatchResult.draft_letter.
    """
    # Cargar match + job
    stmt = (
        select(MatchResult, Job)
        .join(Job, Job.hash == MatchResult.job_hash)
        .where(
            MatchResult.user_id == current_user.id,
            MatchResult.job_hash == job_hash,
        )
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Match result not found",
        )
    match, job = row

    # Identificar colegio
    school = resolve_school_from_job(job)
    if school is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This job is not part of the watchlist (no school metadata).",
        )

    # Resolver perfil del usuario para placeholders
    await db.refresh(current_user, ["profile"])
    profile = current_user.profile

    template_id = body.template_override or school.template_id
    groq = _get_groq(request)

    draft = await generate_draft_letter(
        groq=groq,
        school=school,
        job=job,
        profile=profile,
        template_id=template_id,
    )

    match.draft_letter = draft
    # Si está en "detected" o "reviewed", avanzar a "drafted"
    if match.application_status in ("detected", "reviewed"):
        match.application_status = "drafted"
        match.application_status_at = datetime.now(timezone.utc)

    await db.commit()

    return GenerateDraftResponse(
        status="success",
        job_hash=job_hash,
        draft_letter=draft,
    )


@router.get("/match/{job_hash}/draft", response_class=Response)
async def get_draft(
    job_hash: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Devuelve el borrador guardado (texto plano)."""
    stmt = select(MatchResult.draft_letter).where(
        MatchResult.user_id == current_user.id,
        MatchResult.job_hash == job_hash,
    )
    draft = (await db.execute(stmt)).scalar_one_or_none()
    if not draft:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No draft available",
        )
    return Response(content=draft, media_type="text/plain; charset=utf-8")


# ── Calendar export .ics ────────────────────────────────────────────────────


@router.get("/match/{job_hash}/calendar.ics")
async def export_calendar(
    job_hash: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Genera un fichero .ics con los 3 eventos de la candidatura:
    1. Enviar candidatura (+2 días hábiles desde detección)
    2. Follow-up (+14 días)
    3. Recordatorio deadline si está en draft_letter o description (best-effort)
    """
    stmt = (
        select(MatchResult, Job)
        .join(Job, Job.hash == MatchResult.job_hash)
        .where(
            MatchResult.user_id == current_user.id,
            MatchResult.job_hash == job_hash,
        )
    )
    row = (await db.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Match result not found")
    match, job = row

    school = resolve_school_from_job(job)
    school_name = school.name if school else (job.company or "Job")

    base = match.created_at or datetime.now(timezone.utc)
    apply_by = base + timedelta(days=2)
    followup = base + timedelta(days=14)

    ics = _build_ics(
        events=[
            (
                f"swissjob-apply-{job_hash}",
                f"Enviar candidatura — {school_name}",
                apply_by,
                job.url or "",
                f"Job: {job.title}",
            ),
            (
                f"swissjob-followup-{job_hash}",
                f"Follow-up — {school_name}",
                followup,
                job.url or "",
                f"Comprobar si han respondido. Job: {job.title}",
            ),
        ],
    )

    return Response(
        content=ics,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="swissjob-{job_hash}.ics"'
            ),
        },
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_ics(
    events: list[tuple[str, str, datetime, str, str]],
) -> str:
    """Construye un fichero ICS minimal a partir de tuplas
    (uid, summary, start_utc, url, description).
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//SwissJobHunter//Watchlist//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    now_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for uid, summary, start, url, desc in events:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start_str = start.strftime("%Y%m%dT%H%M%SZ")
        end_str = (start + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{uid}@swissjob",
            f"DTSTAMP:{now_str}",
            f"DTSTART:{start_str}",
            f"DTEND:{end_str}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(desc)}",
            f"URL:{_ics_escape(url)}",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )
