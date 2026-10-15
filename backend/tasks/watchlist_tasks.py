"""Celery tasks específicas de la watchlist de colegios suizos.

- check_watchlist_health: monitor del propio módulo. Detecta fuentes de la
  watchlist que llevan > 24h sin completar en Core, están deshabilitadas o
  acumulan fallos. Emite notificaciones a los usuarios
  con watchlist_schools_enabled=True.

DECISIÓN D.1 (gate anti-doble-motor, §15bis): `send_watchlist_digest` SÍ se
gatea por la capacidad `matching` (se alimenta de `match_results` legacy, que
para un perfil migrado dejan de actualizarse). `check_watchlist_health` NO: avisa
del estado de las FUENTES, no del matching de nadie — es operativo y el core no
lo emite.

- send_watchlist_digest: digest diario con matches de la watchlist en el
  rango score 40-69 (los que NO disparan push inmediato).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from celery_app import celery_app

logger = logging.getLogger(__name__)


_WATCHLIST_SOURCES = (
    "swiss_schools_ecolint",
    "swiss_schools_hautlac",
    "swiss_schools_inspired",
    "swiss_schools_isb",
    "swiss_schools_iscs",
    "swiss_schools_isp",
    "swiss_schools_nae",
    "swiss_schools_zis",
)


def _get_watchlist_sources() -> tuple[str, ...]:
    """Fuentes escolares del contrato Core que alimentan esta watchlist."""
    return _WATCHLIST_SOURCES


class _CoreSourceHealth(BaseModel):
    source: str
    last_complete_at: AwareDatetime | None
    enabled_scopes: int = Field(ge=0)
    observed_scopes: int = Field(ge=0)
    max_consecutive_failures: int = Field(ge=0)
    update_in_progress: bool


async def _read_core_source_health(client, source: str) -> _CoreSourceHealth:
    try:
        response = await client.get(
            f"/harvest/sources/{source}/aggregate",
            params={"item_limit": 1, "top_n": 1},
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Core inaccesible al consultar {source}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"Core devolvió HTTP {response.status_code} para {source}")
    try:
        result = _CoreSourceHealth.model_validate(response.json())
    except (ValidationError, ValueError, TypeError) as exc:
        raise RuntimeError(f"Core devolvió salud inválida para {source}") from exc
    if result.source != source:
        raise RuntimeError(f"Core devolvió otra fuente al consultar {source}")
    return result


async def _core_watchlist_health(client_factory=None) -> list[_CoreSourceHealth]:
    from config import settings
    from services.catalog.core_client import default_client_factory

    factory = client_factory or default_client_factory
    if client_factory is None and not settings.CORE_CONSUMER_KEY:
        raise RuntimeError("CORE_CONSUMER_KEY no configurada")
    async with factory() as client:
        return list(
            await asyncio.gather(
                *(
                    _read_core_source_health(client, source)
                    for source in _WATCHLIST_SOURCES
                )
            )
        )


# Umbral de fuente silenciosa: si lleva más de N horas sin ciclo completo.
_SILENT_HOURS = 24

# G1/P3-21 — cooldown de re-notificación de salud: sin él, una fuente
# disabled generaba la MISMA notificación cada 6h (4/día) indefinidamente.
_HEALTH_NOTIFIED_KEY = "watchlist_health_notified:{source}:{kind}"
_HEALTH_NOTIFY_COOLDOWN_SECONDS = 24 * 3600

# G1/P3-20 — marca de agua del digest: la ventana fija now-24h con un
# schedule que deriva producía solapes (matches notificados dos veces) o
# huecos (matches nunca notificados).
_DIGEST_WATERMARK_KEY = "watchlist_digest:watermark"
# G3/P1-1 — marcador «ya avisado» por (usuario, oferta), que es lo que
# permite solapar la ventana sin duplicar (ver tasks/watermarks.py).
_DIGEST_SENT_PREFIX = "watchlist_digest:sent"


@celery_app.task(name="tasks.watchlist.check_health")
def check_watchlist_health() -> dict[str, Any]:
    """Comprueba que las fuentes de la watchlist están operativas en Core."""
    try:
        return asyncio.run(_check_health_async())
    except Exception as exc:
        logger.error("check_watchlist_health failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _check_health_async(client_factory=None) -> dict[str, Any]:
    from database import task_session

    issues: list[dict] = []
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=_SILENT_HOURS)
    sources = await _core_watchlist_health(client_factory)

    async with task_session() as db:
        # Fuentes de la watchlist con problemas según su autoridad única.
        for s in sources:
            if s.enabled_scopes == 0:
                issues.append(
                    {
                        "source": s.source,
                        "kind": "disabled",
                        "detail": "Core no tiene scopes habilitados",
                    }
                )
                continue
            if s.max_consecutive_failures > 0:
                issues.append(
                    {
                        "source": s.source,
                        "kind": "failures",
                        "detail": (
                            f"{s.max_consecutive_failures} fallos consecutivos en Core"
                        ),
                    }
                )
            if s.last_complete_at is None:
                issues.append(
                    {
                        "source": s.source,
                        "kind": "never_succeeded",
                        "detail": "Nunca completó una cosecha en Core",
                    }
                )
            elif s.last_complete_at < threshold and not s.update_in_progress:
                issues.append(
                    {
                        "source": s.source,
                        "kind": "silent",
                        "detail": (
                            f"Sin ciclo completo desde {s.last_complete_at.isoformat()} "
                            f"(>{_SILENT_HOURS}h)"
                        ),
                    }
                )

        if not issues:
            return {"status": "ok", "checked": len(sources), "issues": 0}

        # G1/P3-21 — cooldown: solo se notifican los problemas NUEVOS (no
        # avisados en las últimas 24h). El estado (details) se reporta entero.
        import redis

        from config import settings

        r = redis.from_url(settings.REDIS_URL)
        fresh_issues = [
            i
            for i in issues
            if not r.get(
                _HEALTH_NOTIFIED_KEY.format(source=i["source"], kind=i["kind"])
            )
        ]

        notified = 0
        if fresh_issues:
            # 2) Notificar a los usuarios con la watchlist activa
            from services.schools.preferences import enabled_users

            users = await enabled_users(db)
            notified = await _notify_users(db, users, fresh_issues)
            for issue in fresh_issues:
                r.set(
                    _HEALTH_NOTIFIED_KEY.format(
                        source=issue["source"], kind=issue["kind"]
                    ),
                    "1",
                    ex=_HEALTH_NOTIFY_COOLDOWN_SECONDS,
                )
        r.close()

        return {
            "status": "issues",
            "checked": len(sources),
            "issues": len(issues),
            "fresh_issues": len(fresh_issues),
            "users_notified": notified,
            "details": issues,
        }


async def _notify_users(db, users, issues: list[dict]) -> int:
    """Crea una notificación in-app para cada usuario afectado."""
    from models.notification import Notification

    title = "Vigilancia de colegios — problemas detectados"
    summary_lines = [f"{i['source']}: {i['detail']}" for i in issues[:5]]
    message = "\n".join(summary_lines)
    if len(issues) > 5:
        message += f"\n... y {len(issues) - 5} más"

    count = 0
    for user in users:
        try:
            n = Notification(
                user_id=user.id,
                event_type="watchlist_health",
                title=title,
                body=message,
                data={"issues": issues},
            )
            db.add(n)
            count += 1
        except Exception as e:
            logger.warning("No se pudo notificar a %s: %s", user.id, e)

    await db.commit()
    return count


@celery_app.task(name="tasks.watchlist.send_digest", bind=True, max_retries=1)
def send_watchlist_digest(self) -> dict[str, Any]:
    """Digest diario para matches de watchlist con score 40-69 (no push)."""
    try:
        return asyncio.run(_send_digest_async())
    except Exception as exc:
        # G3/P2-9: antes se devolvía un dict de error y Celery marcaba la tarea
        # como SUCCEEDED — con la marca de agua ya avanzada, la ventana entera
        # se perdía en silencio. Ahora la marca avanza DESPUÉS del commit y el
        # fallo se propaga para que haya reintento.
        logger.error("send_watchlist_digest failed: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _send_digest_async() -> dict[str, Any]:
    from sqlalchemy import and_, select

    from config import settings
    from database import task_session
    from models.job import Job
    from models.match_result import MatchResult
    from models.notification import Notification
    from models.user import User
    from services.matching.identity import core_profile_unlinked_sql

    # G1/P3-20 — ventana por MARCA DE AGUA (antes: now-24h fija, que con la
    # deriva del schedule producía solapes o huecos). Primera corrida:
    # lookback de 24h.
    # G3/P2-9 + G3/P1-1 — la marca ya NO avanza aquí: avanzaba ANTES de
    # trabajar y, si la BD fallaba, la tarea devolvía un dict de error con la
    # ventana ya consumida (perdida para siempre). Ahora avanza tras el commit
    # y RETROCEDIDA el lag de seguridad; la no-duplicación la garantiza el
    # marcador por (usuario, oferta), no el instante de la marca.
    import redis

    from tasks.watermarks import filter_unsent, save_watermark, unmark_sent

    now = datetime.now(timezone.utc)
    r = redis.from_url(settings.REDIS_URL)
    since = None
    raw = r.get(_DIGEST_WATERMARK_KEY)
    if raw:
        try:
            value = raw.decode() if isinstance(raw, bytes) else raw
            since = datetime.fromisoformat(value)
        except (ValueError, AttributeError):
            logger.warning("Marca de agua del digest inválida; ventana de 24h")
    if since is None:
        since = now - timedelta(hours=24)
    watchlist_sources = _get_watchlist_sources()

    try:
        async with task_session() as db:
            from services.schools.preferences import enabled_users

            # Preserve the anti-double-matcher gate; school preferences have
            # their own authority, independent of local/core matching.
            users = await enabled_users(
                db, extra_condition=core_profile_unlinked_sql(User.id)
            )

            notified = 0
            marked: set[str] = set()
            for user in users:
                stmt = (
                    select(MatchResult, Job)
                    .join(Job, Job.hash == MatchResult.job_hash)
                    .where(
                        MatchResult.user_id == user.id,
                        MatchResult.created_at >= since,
                        # Cota superior = la marca guardada: sin ella, un match
                        # creado entre la query y la marca se notificaría dos
                        # veces (el cierre de digest_tasks/P2-15).
                        MatchResult.created_at <= now,
                        Job.source.in_(watchlist_sources),
                        and_(
                            MatchResult.score_final
                            >= settings.WATCHLIST_DIGEST_MIN_SCORE,
                            MatchResult.score_final < settings.WATCHLIST_PUSH_THRESHOLD,
                        ),
                    )
                    .order_by(MatchResult.score_final.desc())
                    .limit(20)
                )
                rows = (await db.execute(stmt)).all()
                if not rows:
                    continue

                # Idempotencia por (usuario, oferta): la ventana solapa a propósito
                # para recuperar los matches que el commit de `run_matching` dejó
                # por debajo de la marca; el marcador impide re-avisar.
                markers = [f"{user.id}:{m.job_hash}" for m, _ in rows]
                fresh_markers = set(filter_unsent(r, _DIGEST_SENT_PREFIX, markers))
                fresh = [
                    (m, j)
                    for (m, j), marker in zip(rows, markers)
                    if marker in fresh_markers
                ]
                if not fresh:
                    continue
                marked.update(fresh_markers)

                lines = [
                    f"• {j.company or '?'} — {j.title[:60]} (score {m.score_final:.0f})"
                    for m, j in fresh
                ]
                db.add(
                    Notification(
                        user_id=user.id,
                        event_type="watchlist_digest",
                        title=f"Digest watchlist — {len(fresh)} matches potenciales",
                        body="\n".join(lines),
                        data={"count": len(fresh)},
                    )
                )
                notified += 1

            try:
                await db.commit()
            except Exception:
                # Sin commit no hay notificación: los marcadores deben caer para
                # que el reintento vuelva a construir el digest.
                unmark_sent(r, _DIGEST_SENT_PREFIX, marked)
                raise

            # La marca solo avanza cuando las notificaciones YA están persistidas.
            save_watermark(r, _DIGEST_WATERMARK_KEY, now)
            return {"status": "ok", "users_notified": notified}
    finally:
        r.close()
