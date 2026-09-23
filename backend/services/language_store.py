"""Almacén del idioma DERIVADO de los títulos de oferta.

Saca la detección masiva del camino de respuesta (punto 5). El router LEE de
aquí; quien DETECTA es la tarea de fondo `tasks.language_tasks`. La semántica
de los tres estados está en el docstring de `models.title_language`.

Regla de oro del módulo: **ninguna función de aquí detecta nada**. Si alguna
vez vuelve a aparecer una llamada al detector en este fichero, ha vuelto el
problema que motivó escribirlo.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.title_language import TITLE_MAX_LEN, JobTitleLanguage

logger = logging.getLogger(__name__)


def normalise(title: str | None) -> str:
    """Clave canónica de un título. Cadena vacía = no hay título que resolver."""
    if not isinstance(title, str):
        return ""
    return title.strip()[:TITLE_MAX_LEN]


async def lookup(db: AsyncSession, titles) -> dict[str, str]:
    """{título: idioma} SÓLO de los títulos ya RESUELTOS.

    Los pendientes (`language IS NULL`) se omiten a propósito: para el
    consumidor, «pendiente» y «nunca visto» son el mismo estado — no lo sé
    todavía— y debe comportarse igual en los dos.

    El idioma resuelto como desconocido viaja como `''`, que es informativo:
    distingue «ya se intentó» de «aún no».
    """
    claves = {normalise(t) for t in titles}
    claves.discard("")
    if not claves:
        return {}
    filas = await db.execute(
        sa.select(JobTitleLanguage.title, JobTitleLanguage.language).where(
            JobTitleLanguage.title.in_(claves),
            JobTitleLanguage.language.is_not(None),
        )
    )
    return {titulo: idioma for titulo, idioma in filas.all()}


async def record_pending(db: AsyncSession, titles) -> int:
    """Encola los títulos que aún no tienen fila. Devuelve cuántos encoló.

    Es un INSERT idempotente, no una detección: cuesta una sentencia y sólo
    cuando aparece un título nunca visto. En régimen permanente inserta CERO.

    **Best-effort a propósito**: encolar es una comodidad para la tarea de
    fondo, no parte del contrato de la lectura. Si la escritura falla, la
    petición se sirve igual —sin indicador de idioma para ese título— en vez
    de convertir un fallo de conveniencia en un 500.
    """
    claves = sorted({normalise(t) for t in titles} - {""})
    if not claves:
        return 0
    try:
        resultado = await db.execute(
            pg_insert(JobTitleLanguage)
            .values([{"title": c} for c in claves])
            .on_conflict_do_nothing(index_elements=["title"])
        )
        await db.commit()
        return resultado.rowcount or 0
    except Exception:
        await db.rollback()
        logger.warning("language_store: no se pudieron encolar %d títulos", len(claves))
        return 0


async def pending_titles(db: AsyncSession, limit: int) -> list[str]:
    """Títulos encolados y sin resolver, los más antiguos primero."""
    filas = await db.execute(
        sa.select(JobTitleLanguage.title)
        .where(JobTitleLanguage.language.is_(None))
        .order_by(JobTitleLanguage.created_at)
        .limit(limit)
    )
    return list(filas.scalars())


async def store_resolved(db: AsyncSession, resolved: dict[str, str]) -> int:
    """Persiste {título: idioma} ya resuelto. `''` es un resultado válido."""
    if not resolved:
        return 0
    ahora = sa.func.now()
    for titulo, idioma in resolved.items():
        await db.execute(
            sa.update(JobTitleLanguage)
            .where(JobTitleLanguage.title == titulo)
            .values(language=idioma or "", detected_at=ahora)
        )
    await db.commit()
    return len(resolved)


async def pending_count(db: AsyncSession) -> int:
    return int(
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(JobTitleLanguage)
            .where(JobTitleLanguage.language.is_(None))
        )
        or 0
    )
