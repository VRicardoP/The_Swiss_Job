from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.source_compliance import SourceCompliance


class ComplianceEngine:
    """Verifies TOS compliance before scraping. Automatic kill-switch."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def can_scrape(self, source_key: str) -> bool:
        """Check is_allowed + robots_txt_ok for a source.

        Incluye el REINTENTO del kill-switch (V.0): una fuente apagada por
        bloqueos vuelve a intentarse pasadas `COMPLIANCE_RETRY_AFTER_HOURS`.
        Antes el apagado era permanente y nadie lo revisaba — `gastrojob` y
        `swiss_schools_isb` llevaban meses caídas SIN HABER FUNCIONADO NUNCA.
        `robots_txt_ok=False` NO se reintenta: eso es una prohibición, no un fallo.
        """
        result = await self.db.execute(
            select(SourceCompliance).where(SourceCompliance.source_key == source_key)
        )
        source = result.scalar_one_or_none()
        if source is None:
            return False
        if not source.robots_txt_ok:
            return False
        if source.is_allowed:
            return True
        return self._retry_window_elapsed(source)

    @staticmethod
    def _retry_window_elapsed(source: SourceCompliance) -> bool:
        """¿Toca reintentar una fuente apagada por el kill-switch?

        SIN efectos secundarios (es una consulta): no toca la fila ni hace
        commit. La rehabilitación duradera la hace `reset_blocks` cuando el
        reintento va bien; si vuelve a fallar, `report_block` refresca
        `last_blocked_at` y la ventana empieza de nuevo.
        """
        horas = settings.COMPLIANCE_RETRY_AFTER_HOURS
        if horas <= 0 or source.last_blocked_at is None:
            return False
        transcurrido = datetime.now(timezone.utc) - source.last_blocked_at
        return transcurrido >= timedelta(hours=horas)

    async def report_block(self, source_key: str, status_code: int) -> None:
        """Record a block event. If N consecutive blocks → kill-switch."""
        result = await self.db.execute(
            select(SourceCompliance).where(SourceCompliance.source_key == source_key)
        )
        source = result.scalar_one_or_none()
        if source is None:
            return

        now = datetime.now(timezone.utc)
        source.consecutive_blocks += 1
        source.last_blocked_at = now

        if (
            source.auto_disable_on_block
            and source.consecutive_blocks >= settings.COMPLIANCE_BLOCK_THRESHOLD
        ):
            source.is_allowed = False

        await self.db.commit()

    async def reset_blocks(self, source_key: str) -> None:
        """Reset consecutive block counter after a successful request.

        Marca también last_success_at, que el healthcheck usa para detectar
        scrapers silenciosos (sin éxito >24h, no necesariamente bloqueados).

        Y RE-HABILITA la fuente (V.0): si el éxito llega en la ventana de
        reintento del kill-switch, la recuperación tiene que ser DURADERA — si
        no, `is_allowed` seguiría en False para siempre y cada run dependería de
        volver a cumplir la ventana.
        """
        await self.db.execute(
            update(SourceCompliance)
            .where(SourceCompliance.source_key == source_key)
            .values(
                consecutive_blocks=0,
                is_allowed=True,
                last_success_at=datetime.now(timezone.utc),
            )
        )
        await self.db.commit()

    async def get_compliance_status(self) -> list[dict]:
        """Return status of all sources for admin panel."""
        result = await self.db.execute(
            select(SourceCompliance).order_by(SourceCompliance.source_key)
        )
        sources = result.scalars().all()
        return [
            {
                "source_key": s.source_key,
                "method": s.method,
                "is_allowed": s.is_allowed,
                "robots_txt_ok": s.robots_txt_ok,
                "rate_limit_seconds": s.rate_limit_seconds,
                "max_requests_per_hour": s.max_requests_per_hour,
                "consecutive_blocks": s.consecutive_blocks,
                "last_blocked_at": s.last_blocked_at.isoformat()
                if s.last_blocked_at
                else None,
                "tos_reviewed_at": s.tos_reviewed_at.isoformat()
                if s.tos_reviewed_at
                else None,
                "tos_notes": s.tos_notes,
            }
            for s in sources
        ]
