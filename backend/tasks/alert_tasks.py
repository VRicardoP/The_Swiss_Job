"""Celery: detección de ofertas de profesor de primaria (Suiza) → email.

Corre periódicamente (ver services/scheduler.py). Usa una marca de agua en
Redis (`first_seen_at` de la última corrida) para avisar SOLO de ofertas nuevas
y no re-enviar. Patrón `def task(): asyncio.run(_impl())` (Celery no es async).

DECISIÓN D.1 (gate anti-doble-motor, §15bis): esta tarea NO consulta
`jobhunt_routing` A PROPÓSITO. El core no tiene la capacidad de colegios/
docencia (diferida a Fase E): si se omitiera para un perfil migrado, NADIE
enviaría esta alerta. Es el reverso del invariante — se gatea lo que el core
puede duplicar (matching, digest), no todo. Revisar cuando el core la asuma.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from celery_app import celery_app

logger = logging.getLogger(__name__)

_WATERMARK_KEY = "teacher_alert:watermark"
# G3/P1-1 — marcador «ya avisado» por oferta: es lo que permite que la
# ventana solape sin reenviar (ver tasks/watermarks.py).
_SENT_PREFIX = "teacher_alert:sent"


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
    from tasks.watermarks import filter_unsent, save_watermark, unmark_sent

    if not settings.TEACHER_ALERT_ENABLED:
        return {"status": "disabled"}

    if not settings.TEACHER_ALERT_EMAIL:
        logger.warning(
            "Alerta profesor primaria: TEACHER_ALERT_EMAIL vacío — se omite el envío"
        )
        return {"status": "no_recipient"}

    email = EmailService()
    if not email.is_available:
        logger.warning(
            "Alerta profesor primaria: SMTP no configurado (SMTP_*) — se omite el envío"
        )
        return {"status": "no_smtp"}

    r = redis.from_url(settings.REDIS_URL)
    now = datetime.now(timezone.utc)
    watermark = _load_watermark(r, now, settings.TEACHER_ALERT_INITIAL_LOOKBACK_DAYS)

    # Prefiltro barato en BD: docencia (categoría H) activa y nueva desde la
    # marca. G1/P2-15: con cota SUPERIOR `<= now` — sin ella, una oferta
    # insertada entre la captura de `now` y la query entraba en ESTA corrida
    # (> watermark viejo) y en la SIGUIENTE (> now) → doble email. Es el mismo
    # cierre que ya aplica digest_tasks (`created_at <= now`).
    async with task_session() as db:
        stmt = (
            select(Job)
            .where(
                Job.category == "H",
                Job.is_active.is_(True),
                Job.duplicate_of.is_(None),
                Job.first_seen_at > watermark,
                Job.first_seen_at <= now,
            )
            .order_by(Job.first_seen_at)
        )
        candidates = list((await db.execute(stmt)).scalars().all())

    # Filtro fino de NIVEL primaria (marcadores multiidioma).
    matches = [
        j for j in candidates if is_primary_teacher_job(j.category, j.title, j.tags)
    ]

    # G3/P1-1: la marca se guarda con LAG (ventana solapada, ver
    # tasks/watermarks.py) y la idempotencia pasa a ser POR OFERTA, así que el
    # solape recupera lo que la cosecha commiteó tarde sin reenviar nada.
    fresh_hashes = set(filter_unsent(r, _SENT_PREFIX, [j.hash for j in matches]))
    fresh = [j for j in matches if j.hash in fresh_hashes]

    if fresh:
        subject, text, html = build_alert_email(fresh)
        try:
            email.send(settings.TEACHER_ALERT_EMAIL, subject, text, html)
        except Exception:
            # El envío falló: retirar los marcadores para que el retry (o la
            # corrida siguiente, que ya solapa) lo vuelva a intentar.
            unmark_sent(r, _SENT_PREFIX, fresh_hashes)
            r.close()
            raise
        logger.info(
            "Alerta profesor primaria: %d ofertas enviadas a %s",
            len(fresh),
            settings.TEACHER_ALERT_EMAIL,
        )

    # G4/P1-2 — la marca se guarda DESPUÉS del envío, dentro del camino de
    # éxito (mismo criterio que digest_tasks:149 y watchlist_tasks). Guardarla
    # antes perdía el lote ENTERO ante un fallo de SMTP: el `unmark_sent` de
    # arriba retira los marcadores por-oferta, pero la marca de agua ya había
    # avanzado y solo retrocede `NOTIFY_WATERMARK_LAG_MINUTES` (15 min) —
    # con la cosecha diaria y la alerta cada 6 h, TODA oferta de la ventana
    # tiene más de 15 minutos y quedaba por debajo de la marca nueva: no
    # volvía a entrar en ninguna corrida. Con `fresh` vacío se guarda igual
    # (no hubo nada que enviar, la ventana está limpia).
    save_watermark(r, _WATERMARK_KEY, now)

    r.close()

    return {
        "status": "success",
        "candidates": len(candidates),
        "matched": len(fresh),
        "already_sent": len(matches) - len(fresh),
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
