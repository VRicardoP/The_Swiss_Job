"""Resincronización one-shot de user_profiles legacy→core (Fase 2, cierre v5).

Las columnas de preferencias (languages/locations/experience_years/salary_*/
remote_pref) se añadieron a la captura DESPUÉS del snapshot inicial, y ampliar
la whitelist no genera WAL retroactivo: las filas legacy existentes jamás
volverían a pasar por el proyector solas. Este one-shot cierra ese hueco POR
EL FLUJO NORMAL: relee la tabla autoritativa con el SELECT RO del rol del
core, vuelca filas op='U' de payload COMPLETO al staging (shadow_change_log,
la misma mesa que llena la captura) y drena el proyector. Nada de UPDATE
manual de profile_revisions ni de WAL artificial en legacy.

Idempotente en efecto: save_profile_revision dedupe por content_hash — una
segunda ejecución añade filas de log pero produce CERO revisiones nuevas
(criterio verificable en la salida: revisions_new == 0).

Uso (dentro del contenedor del core):

    python -m jobhunt_core.shadow.resync [--schema public]
"""
import argparse
import asyncio
import json
import sys

import sqlalchemy as sa

from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow import projector
from jobhunt_core.shadow.capture import TABLE_WHITELIST

_IDENT_OK = __import__("re").compile(r"^[a-z_][a-z0-9_]*$")


async def resync_profiles(session, legacy_schema: str = "public") -> dict:
    """Vuelca user_profiles autoritativo al staging (op='U', payload completo).

    Devuelve {"staged": n} — el drenaje del proyector va aparte (la sesión de
    staging debe COMMITear antes). Falla cerrado si a la tabla autoritativa le
    falta una columna requerida por la whitelist (misma disciplina que el
    backfill de la captura)."""
    if not _IDENT_OK.match(legacy_schema):
        raise ValueError(f"esquema legacy inválido: {legacy_schema!r}")
    spec = TABLE_WHITELIST["user_profiles"]
    existentes = {
        r[0] for r in (
            await session.execute(
                sa.text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :sch AND table_name = 'user_profiles'"
                ),
                {"sch": legacy_schema},
            )
        ).all()
    }
    faltan = spec["required"] - existentes
    if faltan:
        raise RuntimeError(
            f"{legacy_schema}.user_profiles sin columna(s) requerida(s): "
            f"{sorted(faltan)} — el resync no puede confirmar un contenido "
            "incompleto"
        )
    selected = sorted(spec["columns"] & existentes)
    col_list = ", ".join([f'"{spec["pk"]}"'] + [f'"{c}"' for c in selected])
    filas = (
        await session.execute(
            sa.text(
                f'SELECT {col_list} FROM "{legacy_schema}"."user_profiles" '
                f'ORDER BY "{spec["pk"]}"'
            )
        )
    ).all()
    # Posición del log: detrás de todo lo ya escrito. Las filas reales futuras
    # de la captura llegan con LSN mayor que el aplicado — sin colisión.
    pos = (
        await session.execute(
            sa.text(
                "SELECT COALESCE((SELECT MAX(lsn) FROM shadow_change_log), "
                "COALESCE((SELECT last_applied_lsn FROM shadow_capture_state "
                "WHERE id = 1), 0)) AS lsn"
            )
        )
    ).scalar_one()
    seq0 = (
        await session.execute(
            sa.text(
                "SELECT COALESCE(MAX(seq_in_tx), 0) FROM shadow_change_log "
                "WHERE lsn = :lsn"
            ),
            {"lsn": pos},
        )
    ).scalar_one()
    lote = []
    for i, fila in enumerate(filas, 1):
        payload = dict(zip(selected, fila[1:]))
        lote.append(
            {
                "lsn": pos, "seq": seq0 + i, "op": "U",
                "pk": str(fila[0]),
                # misma representación textual estable que la captura
                "payload": json.dumps(payload, default=str),
            }
        )
    if lote:
        await session.execute(
            sa.text(
                "INSERT INTO shadow_change_log "
                "(lsn, seq_in_tx, src_table, op, pk, payload) "
                "VALUES (:lsn, :seq, 'user_profiles', :op, :pk, "
                "CAST(:payload AS jsonb)) "
                "ON CONFLICT (lsn, seq_in_tx) DO NOTHING"
            ),
            lote,
        )
    return {"staged": len(lote)}


async def _main(argv) -> None:
    ap = argparse.ArgumentParser(prog="jobhunt_core.shadow.resync")
    ap.add_argument("--schema", default="public")
    args = ap.parse_args(argv)
    async with task_session_factory() as factory:
        async with factory() as s:
            staged = await resync_profiles(s, legacy_schema=args.schema)
            await s.commit()
    # Drenaje por el flujo NORMAL del proyector (single-flight, embeddings,
    # reevaluación canónica incluidos).
    totals = await projector.project_pending()
    print(json.dumps({"resync": staged, "projector": totals}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
