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

# Quién resolvió. Subirla marca todo lo anterior como re-derivable sin borrar
# nada: `pending_titles` puede pedir también lo resuelto por una versión vieja.
DETECTOR_VERSION = "langdetect-1"


def normalise(title: str | None) -> str:
    """Clave canónica de un título. Cadena vacía = no hay título que resolver."""
    if not isinstance(title, str):
        return ""
    return title.strip()[:TITLE_MAX_LEN]


async def lookup(db: AsyncSession, titles) -> tuple[dict[str, str], set[str]]:
    """Devuelve (resueltos, vistos) en UNA consulta.

    - `resueltos`: {título: idioma} de los que ya tienen respuesta. El idioma
      resuelto como desconocido viaja como `''`, que es informativo: distingue
      «ya se intentó» de «aún no».
    - `vistos`: todos los títulos con fila, RESUELTOS O NO.

    Por qué hacen falta los dos (M1/T12): antes esto devolvía sólo los
    resueltos, y el router daba por «nunca visto» todo lo demás — así que
    reencolaba en CADA carga los títulos que ya estaban en cola esperando a la
    tarea de fondo. Un INSERT por página que en régimen permanente debía ser
    cero. Para el consumidor «pendiente» y «nunca visto» siguen siendo el mismo
    estado —no lo sé todavía—; la diferencia sólo importa para no reencolar.
    """
    claves = {normalise(t) for t in titles}
    claves.discard("")
    if not claves:
        return {}, set()
    filas = (
        await db.execute(
            sa.select(JobTitleLanguage.title, JobTitleLanguage.language).where(
                JobTitleLanguage.title.in_(claves)
            )
        )
    ).all()
    resueltos = {t: idioma for t, idioma in filas if idioma is not None}
    vistos = {t for t, _ in filas}
    return resueltos, vistos


async def record_pending(titles, *, session_factory=None) -> int:
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
    # M1/T12: sesión PROPIA. Antes encolaba con la sesión de la petición y le
    # hacía `commit()`, así que una LECTURA (un GET) cerraba la transacción de
    # quien la llamaba, confirmando de paso cualquier cosa que estuviera en
    # curso. Encolar es una comodidad para la tarea de fondo; no puede decidir
    # cuándo commitea la petición.
    from database import async_session

    fabrica = session_factory or async_session
    try:
        async with fabrica() as propia:
            resultado = await propia.execute(
                pg_insert(JobTitleLanguage)
                .values([{"title": c} for c in claves])
                .on_conflict_do_nothing(index_elements=["title"])
            )
            await propia.commit()
            return resultado.rowcount or 0
    except Exception:
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
    # L1/T12: UNA sentencia por lote, no una por título. Con lotes de 100 eran
    # 100 idas y vueltas a la base para escribir 100 filas diminutas.
    valores = sa.values(
        sa.column("titulo", sa.String),
        sa.column("idioma", sa.String),
        name="resueltos",
    ).data([(t, i or "") for t, i in resolved.items()])
    await db.execute(
        sa.update(JobTitleLanguage)
        .where(JobTitleLanguage.title == valores.c.titulo)
        .values(
            language=valores.c.idioma,
            detected_at=sa.func.now(),
            detector_version=DETECTOR_VERSION,
        )
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
