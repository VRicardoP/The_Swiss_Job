"""Celery: detección de ofertas de profesor de primaria (Suiza) → email.

Corre periódicamente (ver services/scheduler.py). Usa una marca de agua en
Redis (`first_seen_at` de la última corrida) para avisar SOLO de ofertas nuevas
y no re-enviar. Patrón `def task(): asyncio.run(_impl())` (Celery no es async).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)

_WATERMARK_KEY = "teacher_alert:watermark"


@celery_app.task(
    name="tasks.alert_tasks.detect_teacher_alerts",
    bind=True,
    max_retries=1,
)
def detect_teacher_alerts(self) -> dict[str, Any]:
    """Detecta ofertas nuevas de profesor de primaria y envía el email de aviso."""
    try:
        return asyncio.run(_detect_and_notify())
    except Exception as exc:
        logger.error("detect_teacher_alerts failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _detect_and_notify() -> dict[str, Any]:
    import redis
    from sqlalchemy import select

    from config import settings
    from database import task_session
    from models.job import Job
    from services.email_service import EmailService
    from services.teacher_alert import build_alert_email, is_primary_teacher_job

    if not settings.TEACHER_ALERT_ENABLED:
        return {"status": "disabled"}

    email = EmailService()
    if not email.is_available:
        logger.warning(
            "Alerta profesor primaria: SMTP no configurado (SMTP_*) — se omite el envío"
        )
        return {"status": "no_smtp"}

    r = redis.from_url(settings.REDIS_URL)
    now = datetime.now(timezone.utc)
    watermark = _load_watermark(r, now, settings.TEACHER_ALERT_INITIAL_LOOKBACK_DAYS)

    # Prefiltro barato en BD: docencia (categoría H) activa y nueva desde la marca.
    async with task_session() as db:
        stmt = (
            select(Job)
            .where(
                Job.category == "H",
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.first_seen_at > watermark,
            )
            .order_by(Job.first_seen_at)
        )
        candidates = list((await db.execute(stmt)).scalars().all())

    # Filtro fino de NIVEL primaria (marcadores multiidioma).
    matches = [
        j for j in candidates if is_primary_teacher_job(j.category, j.title, j.tags)
    ]

    if matches:
        subject, text, html = build_alert_email(matches)
        email.send(settings.TEACHER_ALERT_EMAIL, subject, text, html)
        logger.info(
            "Alerta profesor primaria: %d ofertas enviadas a %s",
            len(matches),
            settings.TEACHER_ALERT_EMAIL,
        )

    # Avanzar la marca aunque no haya matches (evita re-escanear lo ya visto).
    _save_watermark(r, now)
    r.close()

    return {
        "status": "success",
        "candidates": len(candidates),
        "matched": len(matches),
    }


def _load_watermark(r, now: datetime, lookback_days: int) -> datetime:
    """Lee la marca de Redis; en la primera corrida mira `lookback_days` atrás."""
    raw = r.get(_WATERMARK_KEY)
    if raw:
        try:
            value = raw.decode() if isinstance(raw, bytes) else raw
            return datetime.fromisoformat(value)
        except (ValueError, AttributeError):
            logger.warning("Marca de agua inválida en Redis; reiniciando ventana")
    return now - timedelta(days=lookback_days)


def _save_watermark(r, now: datetime) -> None:
    r.set(_WATERMARK_KEY, now.isoformat())
