"""Celery: digest diario de matches por email.

Resume los mejores matches NUEVOS de cada usuario desde el último envío. Usa una
marca de agua en Redis (como la alerta de profesor) para no re-enviar. Patrón
`def task(): asyncio.run(_impl())` (Celery no es async).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)

_WATERMARK_KEY = "daily_digest:watermark"
# G3/P1-1 — marcador «ya enviado» por (usuario, oferta): permite que la
# ventana solape sin reenviar (ver tasks/watermarks.py).
_SENT_PREFIX = "daily_digest:sent"


@celery_app.task(name="tasks.digest_tasks.send_daily_digest", bind=True, max_retries=1)
def send_daily_digest(self) -> dict[str, Any]:
    """Envía a cada usuario un email con sus mejores matches nuevos."""
    try:
        return asyncio.run(_send_daily_digest_async())
    except Exception as exc:
        logger.error("send_daily_digest failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _send_daily_digest_async() -> dict[str, Any]:
    import redis
    from sqlalchemy import or_, select

    from config import settings
    from database import task_session
    from models.job import Job
    from models.match_result import NEGATIVE_FEEDBACK, MatchResult
    from models.user import User
    from services.daily_digest import build_digest_email
    from services.email_service import EmailService
    from services.routing import CAPABILITY_MATCHING, legacy_owned_sql
    from tasks.watermarks import filter_unsent, save_watermark, unmark_sent

    if not settings.DAILY_DIGEST_ENABLED:
        return {"status": "disabled"}

    email = EmailService()
    if not email.is_available:
        logger.warning(
            "Digest diario: SMTP no configurado (SMTP_*) — se omite el envío"
        )
        return {"status": "no_smtp"}

    r = redis.from_url(settings.REDIS_URL)
    now = datetime.now(timezone.utc)
    watermark = _load_watermark(r, now, settings.DAILY_DIGEST_INITIAL_LOOKBACK_HOURS)

    # Matches NUEVOS (created_at > marca) por encima del umbral, de usuarios
    # activos con email, ordenados por usuario y score. Excluye feedback negativo.
    async with task_session() as db:
        stmt = (
            select(
                MatchResult.user_id,
                User.email,
                Job.title,
                Job.company,
                Job.location,
                Job.canton,
                Job.url,
                Job.hash,
                MatchResult.score_final,
            )
            .join(User, User.id == MatchResult.user_id)
            .join(Job, Job.hash == MatchResult.job_hash)
            .where(
                MatchResult.created_at > watermark,
                # Cota superior: los matches creados entre `now` y guardar la marca
                # no deben re-entrar en la siguiente corrida (evita duplicados).
                MatchResult.created_at <= now,
                MatchResult.score_final >= settings.DAILY_DIGEST_MIN_SCORE,
                User.is_active.is_(True),
                User.email.is_not(None),
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                or_(
                    MatchResult.feedback.is_(None),
                    MatchResult.feedback.notin_(list(NEGATIVE_FEEDBACK)),
                ),
                # Gate anti-doble-motor D.1 (§15bis), EN SQL: a un usuario cuyo
                # matching ya es del core no se le materializa NI una fila —
                # su digest saldria de match_results legacy viejos mientras el
                # core le envia el suyo. Decision registrada: se usa la
                # dimension 'matching' porque el digest deriva de sus
                # resultados; la dimension NOTIFICATIONS propia del plan se
                # aplaza a cuando el core emita digests.
                legacy_owned_sql(MatchResult.user_id, CAPABILITY_MATCHING),
            )
            .order_by(MatchResult.user_id, MatchResult.score_final.desc())
        )
        rows = (await db.execute(stmt)).all()

    # Agrupar por usuario preservando el orden por score (ya viene ordenado).
    by_user: dict[str, dict] = {}
    for row in rows:
        entry = by_user.setdefault(str(row.user_id), {"email": row.email, "jobs": []})
        if len(entry["jobs"]) >= settings.DAILY_DIGEST_MAX_JOBS:
            continue
        entry["jobs"].append(
            {
                "title": row.title,
                "company": row.company,
                "location": row.canton or row.location,
                "url": row.url,
                "score": row.score_final,
                "hash": row.hash,
            }
        )

    sent = 0
    failures = 0
    for user_id, data in by_user.items():
        if not data["jobs"]:
            continue
        # G3/P1-1: idempotencia POR (usuario, oferta). La ventana solapa a
        # propósito para recuperar lo que la transacción de matching commiteó
        # después de la marca; el marcador impide que ese solape reenvíe.
        marker_ids = [f"{user_id}:{j['hash']}" for j in data["jobs"]]
        fresh_ids = set(filter_unsent(r, _SENT_PREFIX, marker_ids))
        fresh_jobs = [
            j for j, marker in zip(data["jobs"], marker_ids) if marker in fresh_ids
        ]
        if not fresh_jobs:
            continue
        subject, text, html = build_digest_email(fresh_jobs)
        try:
            email.send(data["email"], subject, text, html)
            sent += 1
        except Exception as exc:  # un fallo por usuario no aborta el resto
            failures += 1
            unmark_sent(r, _SENT_PREFIX, fresh_ids)
            logger.warning("Digest: fallo enviando a %s: %s", data["email"], exc)

    # Solo avanzar la marca si NADIE falló: un fallo transitorio de SMTP no debe
    # dejar matches por debajo de la marca y perderlos para siempre. Si hubo
    # fallos, la próxima corrida reintenta (a costa de reenviar a quien sí recibió).
    if failures == 0:
        save_watermark(r, _WATERMARK_KEY, now)
    else:
        logger.warning(
            "Digest: %d envío(s) fallaron; marca NO avanzada, se reintentará", failures
        )
    r.close()

    logger.info("Digest diario: %d usuarios notificados (%d fallos)", sent, failures)
    return {
        "status": "success",
        "users_notified": sent,
        "failures": failures,
        "candidates": len(rows),
    }


def _load_watermark(r, now: datetime, lookback_hours: int) -> datetime:
    """Lee la marca de Redis; en la primera corrida mira `lookback_hours` atrás."""
    raw = r.get(_WATERMARK_KEY)
    if raw:
        try:
            value = raw.decode() if isinstance(raw, bytes) else raw
            return datetime.fromisoformat(value)
        except (ValueError, AttributeError):
            logger.warning("Marca de agua del digest inválida; reiniciando ventana")
    return now - timedelta(hours=lookback_hours)
