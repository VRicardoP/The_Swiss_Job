"""Celery tasks específicas de la watchlist de colegios suizos.

- check_watchlist_health: monitor del propio módulo. Detecta scrapers de la
  watchlist que llevan > 24h sin ejecutarse con éxito o cuyos contadores de
  bloqueo en compliance están altos. Emite notificaciones a los usuarios
  con watchlist_schools_enabled=True.

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
                issues.append({
                    "source": key,
                    "kind": "no_compliance_row",
                    "detail": "Scraper registrado sin source_compliance",
                })

        # 1b) Issues por fuente conocida
        for s in sources:
            if not s.is_allowed:
                issues.append({
                    "source": s.source_key,
                    "kind": "disabled",
                    "detail": "Compliance kill-switch activado",
                })
                continue
            if s.consecutive_blocks > 0:
                issues.append({
                    "source": s.source_key,
                    "kind": "blocks",
                    "detail": f"{s.consecutive_blocks} bloques consecutivos",
                })
            if s.last_blocked_at and s.last_blocked_at >= threshold:
                issues.append({
                    "source": s.source_key,
                    "kind": "recently_blocked",
                    "detail": f"Último bloqueo {s.last_blocked_at.isoformat()}",
                })
            # 1c) NUEVO: silencio sin éxito en >24h
            if s.last_success_at is None:
                issues.append({
                    "source": s.source_key,
                    "kind": "never_succeeded",
                    "detail": "Nunca completó un scrape exitosamente",
                })
            elif s.last_success_at < threshold:
                issues.append({
                    "source": s.source_key,
                    "kind": "silent",
                    "detail": (
                        f"Sin éxito desde {s.last_success_at.isoformat()} "
                        f"(>{_SILENT_HOURS}h)"
                    ),
                })

        if not issues:
            return {"status": "ok", "checked": len(sources), "issues": 0}

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
        notified = await _notify_users(db, users, issues)

        return {
            "status": "issues",
            "checked": len(sources),
            "issues": len(issues),
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


@celery_app.task(name="tasks.watchlist.send_digest")
def send_watchlist_digest() -> dict[str, Any]:
    """Digest diario para matches de watchlist con score 40-69 (no push)."""
    try:
        return asyncio.run(_send_digest_async())
    except Exception as exc:
        logger.error("send_watchlist_digest failed: %s", exc)
        return {"status": "error", "error": str(exc)}


async def _send_digest_async() -> dict[str, Any]:
    from sqlalchemy import and_, select

    from config import settings
    from database import task_session
    from models.job import Job
    from models.match_result import MatchResult
    from models.notification import Notification
    from models.user import User
    from models.user_profile import UserProfile

    # Ventana digest: matches creados en las últimas 24h
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    watchlist_sources = _get_watchlist_sources()

    async with task_session() as db:
        users_stmt = (
            select(User)
            .join(UserProfile, UserProfile.user_id == User.id)
            .where(
                User.is_active.is_(True),
                UserProfile.watchlist_schools_enabled.is_(True),
            )
        )
        users = (await db.execute(users_stmt)).scalars().all()

        notified = 0
        for user in users:
            stmt = (
                select(MatchResult, Job)
                .join(Job, Job.hash == MatchResult.job_hash)
                .where(
                    MatchResult.user_id == user.id,
                    MatchResult.created_at >= since,
                    Job.source.in_(watchlist_sources),
                    and_(
                        MatchResult.score_final
                            >= settings.WATCHLIST_DIGEST_MIN_SCORE,
                        MatchResult.score_final
                            < settings.WATCHLIST_PUSH_THRESHOLD,
                    ),
                )
                .order_by(MatchResult.score_final.desc())
                .limit(20)
            )
            rows = (await db.execute(stmt)).all()
            if not rows:
                continue

            lines = [
                f"• {j.company or '?'} — {j.title[:60]} (score {m.score_final:.0f})"
                for m, j in rows
            ]
            db.add(Notification(
                user_id=user.id,
                event_type="watchlist_digest",
                title=f"Digest watchlist — {len(rows)} matches potenciales",
                body="\n".join(lines),
                data={"count": len(rows)},
            ))
            notified += 1

        await db.commit()
        return {"status": "ok", "users_notified": notified}
