"""Registro de la salud de cosecha por fuente (V.0).

Responsabilidad única: traducir el resultado de un run (nº de ofertas + fallos
de descarga recogidos por `utils.fetch_diagnostics`) a una fila de
`source_health`, y decidir si esa fuente merece alerta.

NO decide permisos (eso es `ComplianceEngine`) ni reintenta nada.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.source_health import (
    OUTCOME_EMPTY,
    OUTCOME_ERROR,
    OUTCOME_OK,
    SourceHealth,
)
from utils.fetch_diagnostics import FetchIssue, mark_chronic

logger = logging.getLogger(__name__)

# Longitud de la columna: recortamos aquí para no depender del error de la BD.
_DETAIL_MAX = 500


def _summarize(collected: list[FetchIssue]) -> str:
    """Resumen legible de los fallos: la causa raíz + cuántos más hubo.

    La cabecera es el primer issue registrado SALVO que alguno venga marcado
    `root_cause` (r3/H5, r4: flag tipado en FetchIssue, no prefijo de texto):
    hoy lo marca el detector de soft-blocks de BaseScraper — el parser
    registra su fallo estructural ANTES que el detector y `last_error_detail`
    acababa nombrando el síntoma. No se pierde nada: todos los issues siguen
    registrados (y logueados por el pipeline) y el contador "+N más" no
    cambia. Para cualquier run sin causa raíz marcada —todos los providers y
    el resto de caminos de los scrapers— la cabecera sigue siendo
    `collected[0]`, idéntica que antes. Empate (varias causas raíz): gana la
    PRIMERA registrada, igual que sin flag.
    """
    if not collected:
        return ""
    root_cause = next((i for i in collected if i.root_cause), collected[0])
    head = root_cause.describe()
    if len(collected) > 1:
        head = f"{head} (+{len(collected) - 1} más)"
    return head[:_DETAIL_MAX]


def _new_row(source_key: str) -> SourceHealth:
    """Fila nueva con contadores explícitos a 0: el `default=` de SQLAlchemy
    solo se aplica al INSERT, y aquí se incrementan ANTES del flush (serían
    None += 1)."""
    return SourceHealth(
        source_key=source_key,
        last_jobs_count=0,
        consecutive_errors=0,
        consecutive_empty=0,
        last_stored_count=0,
        consecutive_unstored=0,
    )


async def record_outcome(
    db: AsyncSession,
    source_key: str,
    outcome: str,
    job_count: int,
    collected: list[FetchIssue] | None = None,
) -> SourceHealth:
    """Persiste el resultado del run de una fuente y actualiza sus rachas.

    Devuelve la fila para que el llamante pueda decidir sobre ella (p.ej.
    `needs_alert`). No hace commit: lo hace el pipeline con su transacción.
    """
    collected = collected or []
    now = datetime.now(timezone.utc)

    row = (
        await db.execute(
            select(SourceHealth).where(SourceHealth.source_key == source_key)
        )
    ).scalar_one_or_none()
    if row is None:
        row = _new_row(source_key)
        db.add(row)

    row.last_attempt_at = now
    row.last_outcome = outcome
    row.last_jobs_count = job_count

    if outcome == OUTCOME_OK:
        row.last_success_at = now
        row.consecutive_errors = 0
        row.consecutive_empty = 0
    elif outcome == OUTCOME_ERROR:
        row.last_error_at = now
        row.last_error_detail = _summarize(collected)
        row.consecutive_errors += 1
        # La racha de vacíos NO se toca: un error no es un vacío.
    elif outcome == OUTCOME_EMPTY:
        row.consecutive_empty += 1

    return row


async def record_and_alert(
    db: AsyncSession,
    source_key: str,
    outcome: str,
    job_count: int,
    collected: list[FetchIssue] | None = None,
) -> str | None:
    """Registra el veredicto, CONFIRMA y devuelve el motivo de alerta (o None).

    Punto de entrada único para los dos pipelines (providers y scrapers).

    El commit es propio y acotado a propósito: la salud tiene que quedar
    registrada aunque el procesado posterior de las ofertas falle y haga
    rollback — es exactamente el caso que antes no dejaba rastro. Si el propio
    registro falla, se traga el error: la observabilidad NUNCA debe tumbar la
    cosecha.
    """
    try:
        row = await record_outcome(db, source_key, outcome, job_count, collected)
        # Solo la señal de DESCARGA: la de persistencia la evalúa
        # `record_storage` al final del run, cuando ya es actual — evaluarla
        # aquí duplicaba la alerta y reportaba rachas viejas ya recuperadas.
        motivo = _fetch_alert(row)
        await db.commit()
    except Exception as e:  # noqa: BLE001 — degradar, no romper la cosecha
        await db.rollback()
        logger.error("No se pudo registrar la salud de %s: %s", source_key, e)
        return None

    if motivo:
        logger.error("FUENTE DEGRADADA %s — %s", source_key, motivo)
    return motivo


async def record_storage(
    db: AsyncSession,
    source_key: str,
    attempted_count: int,
    stored_count: int,
) -> str | None:
    """Registra la señal de PERSISTENCIA de un run, CONFIRMA y devuelve el
    motivo de alerta (o None). Hermana de `record_and_alert`.

    `attempted_count` son las ofertas que se INTENTARON guardar — las que
    entran en el camino del savepoint del pipeline. NO es `len(jobs)`
    (lo descargado): las descartadas antes de persistir (p. ej. el filtro
    tech de `fetch_tasks`) nunca llegan al savepoint y contarlas produciría
    falsos "FUENTE DEGRADADA" en fuentes cuyo lote entero se filtra (F1).

    Señal SEPARADA de la de descarga a propósito (VD.3): una fuente puede
    descargar perfectamente y no guardar ni una fila (colisiones de clave,
    datos inválidos) — `stelle_admin` estuvo meses así con salud `ok`. No es
    un `outcome` nuevo porque perderíamos el dato de que la descarga funcionó.

    Misma disciplina defensiva que `record_and_alert`: commit propio y acotado,
    y si el registro falla se degrada sin tumbar la cosecha.
    """
    try:
        row = (
            await db.execute(
                select(SourceHealth).where(SourceHealth.source_key == source_key)
            )
        ).scalar_one_or_none()
        if row is None:
            row = _new_row(source_key)
            db.add(row)

        row.last_stored_count = stored_count

        if attempted_count == 0:
            # Sin intentos no hay información sobre la persistencia: la racha
            # no se toca y TAMPOCO se evalúa la alerta — re-emitirla aquí
            # repetiría cada run una racha vieja congelada sin evidencia
            # nueva (y duplicaría en `unhealthy` a una fuente que ya alertó
            # por descarga en este mismo run). La racha sigue registrada en
            # la fila para la lectura global de `needs_alert`.
            await db.commit()
            return None

        if stored_count == 0:
            row.consecutive_unstored += 1
        else:
            row.consecutive_unstored = 0

        # Solo la señal de PERSISTENCIA (SRP): un motivo de descarga saliendo
        # del registrador de persistencia sería confuso y ya lo emite
        # `record_and_alert` al principio del run.
        motivo = _storage_alert(row)
        await db.commit()
    except Exception as e:  # noqa: BLE001 — degradar, no romper la cosecha
        await db.rollback()
        logger.error("No se pudo registrar la persistencia de %s: %s", source_key, e)
        return None

    if motivo:
        logger.error("FUENTE DEGRADADA %s — %s", source_key, motivo)
    return motivo


def _alerta(racha: int, umbral: int, motivo: str) -> str:
    """Devuelve el motivo, marcado como crónico si ya avisaba en el run anterior.

    G7/P2-4: la racha es un contador MONÓTONO que no se limpia hasta que la
    fuente se arregla, así que una fuente rota mantiene su línea de `unhealthy`
    para siempre y con ella el WARNING del run. Que la racha valga EXACTAMENTE
    el umbral es la única corrida en que la fuente acaba de degradarse: esa es
    la noticia. A partir de ahí el aviso se publica igual, pero marcado.
    """
    return motivo if racha == umbral else mark_chronic(motivo)


def _fetch_alert(row: SourceHealth) -> str | None:
    """Señal de DESCARGA: rachas de error y de vacío.

    Umbrales separados porque exigen acciones distintas: una racha de errores
    es un portal caído o un feed muerto (hay que arreglarlo); una racha de
    vacíos puede ser estacionalidad o un selector roto (hay que mirarlo).
    """
    if row.consecutive_errors >= settings.SOURCE_HEALTH_ERROR_STREAK:
        return _alerta(
            row.consecutive_errors,
            settings.SOURCE_HEALTH_ERROR_STREAK,
            f"{row.consecutive_errors} runs seguidos con error"
            f" — último: {row.last_error_detail or 'sin detalle'}",
        )
    if row.consecutive_empty >= settings.SOURCE_HEALTH_EMPTY_STREAK:
        return _alerta(
            row.consecutive_empty,
            settings.SOURCE_HEALTH_EMPTY_STREAK,
            f"{row.consecutive_empty} runs seguidos sin traer ofertas",
        )
    return None


def _storage_alert(row: SourceHealth) -> str | None:
    """Señal de PERSISTENCIA: racha de no-guardados — una fuente que descarga
    bien pero pierde todo al persistir (colisiones, datos inválidos — hay que
    mirar la BD/el parser)."""
    if row.consecutive_unstored >= settings.SOURCE_HEALTH_UNSTORED_STREAK:
        return _alerta(
            row.consecutive_unstored,
            settings.SOURCE_HEALTH_UNSTORED_STREAK,
            f"{row.consecutive_unstored} runs seguidos descargando ofertas"
            " sin conseguir guardar ninguna",
        )
    return None


def needs_alert(row: SourceHealth) -> str | None:
    """Motivo por el que esta fuente merece alerta, o None si está sana.

    Lectura GLOBAL de salud (las tres señales). Cada registrador informa solo
    de la suya (`_fetch_alert` / `_storage_alert`): evaluarlas todas en ambos
    duplicaba alertas y reportaba rachas viejas ya recuperadas en el run.
    """
    return _fetch_alert(row) or _storage_alert(row)
