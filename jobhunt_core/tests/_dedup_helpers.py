"""Andamio de pruebas del dedup: el generador COMPLETO en una transacción.

DT-148 B4: esto vivía en `jobhunt_core/dedup.py` y no tenía ni un llamador de
producción —el barrido diario usa `scan_semantic_candidates_batch`, que acota
cada transacción por debajo de los límites del broker y del worker—. Se queda
porque varios tests comprueban el RESULTADO AGREGADO de una pasada entera
(exactos intra, léxicos y candidatos ANN de un tirón), que por lotes exigiría
reconstruirlo a mano en cada caso.

Importa privados de `dedup` a propósito: es andamio del módulo que prueba, no
una segunda implementación.
"""

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core import embeddings
from jobhunt_core.config import settings
from jobhunt_core.dedup import _CORPUS_SQL, _finish_scan, _scan_ann_rows


async def scan_semantic_candidates(
    session: AsyncSession, window_hours: int | None = None
) -> dict:
    """Ejecuta el generador completo (barridos exacto, léxico y ANN)."""
    if window_hours is None:
        window_hours = int(settings.CORE_DEDUP_SCAN_WINDOW_H)
    models = await embeddings.active_models(session)
    if not models:
        return {"status": "sin_modelo", "escaneadas": 0, "candidatos_nuevos": 0}
    model_id = models[0].id

    sql = _CORPUS_SQL
    params: dict = {"mid": model_id}
    if window_hours > 0:
        sql += "AND orv.created_at >= now() - make_interval(hours => :ventana) "
        params["ventana"] = window_hours
    rows = (await session.execute(sa.text(sql), params)).all()
    inserted = await _scan_ann_rows(
        session,
        rows,
        model_id=model_id,
        sim_min=float(settings.CORE_DEDUP_SIM_MIN),
        k=int(settings.CORE_DEDUP_KNN),
    )
    exact, lexical = await _finish_scan(
        session,
        None
        if window_hours <= 0
        else datetime.now(timezone.utc) - timedelta(hours=window_hours),
    )
    return {
        "status": "ok",
        "escaneadas": len(rows),
        "candidatos_nuevos": inserted,
        "candidatos_exactos_intra": exact,
        "candidatos_lexicos": lexical,
    }
