"""CursorStore — carga y actualiza cursores incrementales por fuente/scope.

El cursor guarda una ventana corta de identidades (URLs) de las ofertas recientes
ya vistas. El pipeline la inyecta en el provider/scraper antes de `fetch_jobs` para
el early-stop, y tras el run la actualiza con lo recién visto.

Eje: el volumen de peticiones depende del número de ofertas NUEVAS, no del total.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.source_cursor import SourceCursor

logger = logging.getLogger(__name__)


class CursorStore:
    """Acceso a `source_cursors`. No hace commit: lo hace el pipeline llamante."""

    async def load(
        self, db: AsyncSession, source_key: str, scope_key: str = "default"
    ) -> SourceCursor:
        """Devuelve el cursor de la fuente/scope, creándolo vacío si no existe."""
        stmt = select(SourceCursor).where(
            SourceCursor.source_key == source_key,
            SourceCursor.scope_key == scope_key,
        )
        cursor = (await db.execute(stmt)).scalar_one_or_none()
        if cursor is None:
            cursor = SourceCursor(
                source_key=source_key,
                scope_key=scope_key,
                recent_identities=[],
            )
            db.add(cursor)
            await db.flush()  # asigna PK / valores server_default sin commitear
        return cursor

    @staticmethod
    def known_identities(cursor: SourceCursor) -> set[str]:
        """Conjunto de identidades ya vistas (para inyectar en `_known_urls`)."""
        return set(cursor.recent_identities or [])

    def update_after_run(
        self,
        cursor: SourceCursor,
        fetched_identities: list[str],
        new_count: int,
        pages_read: int,
    ) -> None:
        """Actualiza el cursor tras un run (mutación in-place; el llamante commitea).

        - `recent_identities`: prepende lo recién visto y recorta a la ventana máxima.
        - métricas EMA + rachas para presupuesto/observabilidad.
        """
        now = datetime.now(timezone.utc)

        # Ventana reciente: lo más nuevo primero, dedup preservando orden, con tope.
        cap = settings.CURSOR_RECENT_IDENTITIES_MAX
        merged: list[str] = []
        seen: set[str] = set()
        for ident in [*fetched_identities, *(cursor.recent_identities or [])]:
            if ident and ident not in seen:
                seen.add(ident)
                merged.append(ident)
            if len(merged) >= cap:
                break
        cursor.recent_identities = merged

        # Métricas (media móvil exponencial suave).
        alpha = 0.3
        cursor.avg_new_jobs_per_run = round(
            (1 - alpha) * (cursor.avg_new_jobs_per_run or 0.0) + alpha * new_count, 2
        )
        cursor.avg_pages_per_run = round(
            (1 - alpha) * (cursor.avg_pages_per_run or 0.0) + alpha * pages_read, 2
        )

        cursor.last_run_at = now
        cursor.last_success_at = now
        cursor.high_watermark_seen_at = now
        cursor.bootstrap_complete = True
        if new_count == 0:
            cursor.consecutive_empty_runs = (cursor.consecutive_empty_runs or 0) + 1
            cursor.last_empty_at = now
        else:
            cursor.consecutive_empty_runs = 0
