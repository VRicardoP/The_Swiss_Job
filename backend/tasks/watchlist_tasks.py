"""Celery tasks específicas de la watchlist de colegios suizos.

- check_watchlist_health: monitor del propio módulo. Detecta scrapers de la
  watchlist que llevan > 24h sin ejecutarse con éxito o cuyos contadores de
  bloqueo en compliance están altos. Emite notificaciones a los usuarios
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

from celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_watchlist_sources() -> tuple[str, ...]:
    """Deriva del registry los source_keys que pertenecen a la watchlist.

    Cualquier scraper cuyo nombre empiece por `swiss_schools_` se considera
    parte de la watchlist. Esto evita la divergencia silenciosa que tenía
    la lista hardcoded cuando se añadían nuevos scrapers (Fase 2-3).
    """
    from scrapers import _SCRAPER_CLASSES

    return tuple(k for k in _SCRAPER_CLASSES if k.startswith("swiss_schools_"))


# Umbral de "scraper silencioso": si lleva más de N horas sin éxito
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
    """Comprueba que los scrapers de la watchlist están operativos."""
    try:
        return asyncio.run(_check_health_async())
    except Exception as exc:
        logger.error("check_watchlist_health failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _check_health_async() -> dict[str, Any]:
    from sqlalchemy import select

    from database import task_session
    from models.source_compliance import SourceCompliance
    from models.user import User
    from models.user_profile import UserProfile

    issues: list[dict] = []
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=_SILENT_HOURS)
    watchlist_sources = _get_watchlist_sources()

    async with task_session() as db:
        # 1) Fuentes de la watchlist con problemas
        stmt = select(SourceCompliance).where(
            SourceCompliance.source_key.in_(watchlist_sources)
        )
        sources = (await db.execute(stmt)).scalars().all()
        sources_by_key = {s.source_key: s for s in sources}

        # 1a) Sources registradas en el scraper registry pero sin fila en
        # source_compliance — error de seeding o migración perdida.
        for key in watchlist_sources:
            if key not in sources_by_key:
                issues.append(
                    {
                        "source": key,
                        "kind": "no_compliance_row",
                        "detail": "Scraper registrado sin source_compliance",
                    }
                )

        # 1b) Issues por fuente conocida
        for s in sources:
            if not s.is_allowed:
                issues.append(
                    {
                        "source": s.source_key,
                        "kind": "disabled",
                        "detail": "Compliance kill-switch activado",
                    }
                )
                continue
            if s.consecutive_blocks > 0:
                issues.append(
                    {
                        "source": s.source_key,
                        "kind": "blocks",
                        "detail": f"{s.consecutive_blocks} bloques consecutivos",
                    }
                )
            if s.last_blocked_at and s.last_blocked_at >= threshold:
                issues.append(
                    {
                        "source": s.source_key,
                        "kind": "recently_blocked",
                        "detail": f"Último bloqueo {s.last_blocked_at.isoformat()}",
                    }
                )
            # 1c) NUEVO: silencio sin éxito en >24h
            if s.last_success_at is None:
                issues.append(
                    {
                        "source": s.source_key,
                        "kind": "never_succeeded",
                        "detail": "Nunca completó un scrape exitosamente",
                    }
                )
            elif s.last_success_at < threshold:
                issues.append(
                    {
                        "source": s.source_key,
                        "kind": "silent",
                        "detail": (
                            f"Sin éxito desde {s.last_success_at.isoformat()} "
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
            users_stmt = (
                select(User)
                .join(UserProfile, UserProfile.user_id == User.id)
                .where(
                    User.is_active.is_(True),
                    UserProfile.watchlist_schools_enabled.is_(True),
                )
            )
            users = (await db.execute(users_stmt)).scalars().all()
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
    from models.user_profile import UserProfile
    from services.routing import CAPABILITY_MATCHING, legacy_owned_sql

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
            users_stmt = (
                select(User)
                .join(UserProfile, UserProfile.user_id == User.id)
                .where(
                    User.is_active.is_(True),
                    UserProfile.watchlist_schools_enabled.is_(True),
                    # Gate anti-doble-motor D.1 (§15bis), EN SQL: este digest se
                    # construye desde `match_results` LEGACY igual que el diario.
                    # Para un perfil migrado esos matches YA NO se actualizan (el
                    # gate de run_all_matches lo omite), así que seguir enviándolo
                    # sería correo con recomendaciones viejas para siempre.
                    legacy_owned_sql(User.id, CAPABILITY_MATCHING),
                )
            )
            users = (await db.execute(users_stmt)).scalars().all()

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
