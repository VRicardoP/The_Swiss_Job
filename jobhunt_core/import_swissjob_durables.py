"""Migración de durables de SwissJob al core (Fase D, runbook 2026-09-04).

Alcance MÍNIMO y distinto del piloto: aquí NO se sintetiza ninguna vacante —
todo el feedback resuelve a vacantes que YA existen en el corpus (CDC/sombra).
Se migran:

- feedback de match_results (thumbs_up/thumbs_down) → profile_vacancy_state:
  thumbs_down implica además dismissed_at (la exclusión del feed canónico es
  por dismissed_at; el legacy excluía los thumbs_down de futuros matches).
  JAMÁS se pisa estado existente (ADR-03): feedback/dismissed_at solo se
  escriben si estaban NULL, y el manifiesto registra el estado ANTERIOR
  exacto de cada fila para un rollback por valores, no por heurística.
- saved_searches → REUTILIZA migrate_saved_searches del piloto (aprobada por
  revisión externa) + fix-up de notify_frequency/notify_push que el piloto no
  necesitaba (sus defaults pisarían los valores reales del usuario).
- job_filters legacy (patrones de exclusión POR USUARIO; el core no tiene
  tabla propia) → clave `exclude_title_contains` dentro del JSONB filters de
  las búsquedas del MISMO perfil (semántica efectiva conservada; mapeo
  documentado en el manifiesto).

El manifiesto de salida contiene la procedencia EXACTA (ids insertados vía
RETURNING, estado previo de cada pvs tocada) y rollback_import la deshace
por esos valores. Idempotente: re-ejecutar produce los mismos conteos.
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.import_portfolio_durables import migrate_saved_searches

logger = logging.getLogger(__name__)

VALID_FEEDBACK = {"thumbs_up", "thumbs_down"}
_MAX_MERGE_HOPS = 5


async def resolve_vacancy_by_incarnation_url(
    session: AsyncSession, url: str
) -> uuid.UUID | None:
    """vacancy_id para una URL de oferta legacy, o None.

    Resuelve por source_listing_incarnations.url (cualquier fuente — el
    corpus llegó por CDC, no por 'portfolio-import') y SIGUE la cadena de
    fusiones: el feedback pertenece a la vacante GANADORA. Una vacante
    archivada SÍ resuelve: el feedback es estado histórico del usuario y su
    conservación (PF.3) no exige que la oferta siga viva."""
    vid = (
        await session.execute(
            sa.text(
                "SELECT i.vacancy_id FROM source_listing_incarnations i "
                "WHERE i.url = :u ORDER BY i.seq DESC LIMIT 1"
            ),
            {"u": url},
        )
    ).scalar_one_or_none()
    hops = 0
    while vid is not None and hops < _MAX_MERGE_HOPS:
        merged = (
            await session.execute(
                sa.text("SELECT merged_into FROM vacancies WHERE id = :v"),
                {"v": vid},
            )
        ).scalar_one_or_none()
        if merged is None:
            return vid
        vid = merged
        hops += 1
    return vid


async def migrate_feedback(
    session: AsyncSession, profile_id, rows: list[dict], manifest: dict
) -> dict:
    """rows: [{url, feedback, created_at}]. Escribe pvs preservando estado.

    Por cada fila tocada, el manifiesto guarda {vacancy_id, existed,
    feedback_antes, dismissed_at_antes} — rollback por VALORES exactos."""
    counts = {"migrated": 0, "kept_existing": 0, "unresolved": 0,
              "invalid_feedback": 0}
    for row in rows:
        fb = row.get("feedback")
        if fb not in VALID_FEEDBACK:
            counts["invalid_feedback"] += 1
            logger.warning("import_swissjob: feedback inválido %r — OMITIDO", fb)
            continue
        vid = await resolve_vacancy_by_incarnation_url(session, row["url"])
        if vid is None:
            counts["unresolved"] += 1
            logger.warning(
                "import_swissjob: URL sin vacante core — OMITIDA: %.80s",
                row.get("url"),
            )
            continue
        antes = (
            await session.execute(
                sa.text(
                    "SELECT feedback, dismissed_at FROM profile_vacancy_state "
                    "WHERE profile_id = :p AND vacancy_id = :v FOR UPDATE"
                ),
                {"p": profile_id, "v": vid},
            )
        ).one_or_none()
        dismissed = row.get("created_at") if fb == "thumbs_down" else None
        if isinstance(dismissed, str):
            from datetime import datetime

            dismissed = datetime.fromisoformat(dismissed)
        manifest["pvs"].append({
            "profile_id": str(profile_id), "vacancy_id": str(vid),
            "existed": antes is not None,
            "feedback_antes": antes.feedback if antes else None,
            "dismissed_at_antes": (
                antes.dismissed_at.isoformat()
                if antes and antes.dismissed_at else None
            ),
            "feedback_escrito": fb,
        })
        if antes is not None and antes.feedback is not None:
            counts["kept_existing"] += 1  # ADR-03: jamás pisar estado
            continue
        await session.execute(
            sa.text(
                "INSERT INTO profile_vacancy_state "
                "(profile_id, vacancy_id, feedback, dismissed_at, updated_at) "
                "VALUES (:p, :v, :f, :d, clock_timestamp()) "
                "ON CONFLICT (profile_id, vacancy_id) DO UPDATE SET "
                "feedback = COALESCE(profile_vacancy_state.feedback, "
                "                    EXCLUDED.feedback), "
                "dismissed_at = COALESCE(profile_vacancy_state.dismissed_at, "
                "                        EXCLUDED.dismissed_at), "
                "updated_at = GREATEST(profile_vacancy_state.updated_at, "
                "                      clock_timestamp())"
            ),
            {"p": profile_id, "v": vid, "f": fb, "d": dismissed},
        )
        counts["migrated"] += 1
    return counts


async def attach_exclusion_filters(
    session: AsyncSession, profile_id, patterns: list[str], manifest: dict
) -> dict:
    """job_filters legacy (title_contains, exclusión por usuario) → clave
    `exclude_title_contains` en el filters de CADA búsqueda del perfil.
    Idempotente (la clave se escribe completa); el manifiesto guarda el
    filters ANTERIOR por búsqueda para rollback exacto."""
    counts = {"searches_updated": 0, "already_present": 0}
    if not patterns:
        return counts
    filas = (
        await session.execute(
            sa.text(
                "SELECT id, filters FROM saved_searches "
                "WHERE profile_id = :p FOR UPDATE"
            ),
            {"p": profile_id},
        )
    ).all()
    for fila in filas:
        actuales = dict(fila.filters or {})
        if actuales.get("exclude_title_contains") == patterns:
            counts["already_present"] += 1
            continue
        manifest["filters_fixups"].append({
            "saved_search_id": str(fila.id),
            "filters_antes": fila.filters,
        })
        actuales["exclude_title_contains"] = patterns
        await session.execute(
            sa.text(
                "UPDATE saved_searches SET filters = CAST(:f AS jsonb), "
                "updated_at = clock_timestamp(), revision = revision + 1 "
                "WHERE id = :id"
            ),
            {"f": sa_json(actuales), "id": fila.id},
        )
        counts["searches_updated"] += 1
    return counts


def sa_json(value) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


async def fixup_notify(
    session: AsyncSession, profile_id, rows: list[dict], manifest: dict
) -> dict:
    """El piloto no migraba notify_frequency/notify_push (sus defaults);
    SwissJob SÍ los tiene. Fix-up por (profile_id, name) tras el insert."""
    counts = {"fixed": 0, "not_found": 0}
    for row in rows:
        name = (row.get("name") or "")[:200]
        freq = row.get("notify_frequency")
        push = row.get("notify_push")
        if freq is None and push is None:
            continue
        antes = (
            await session.execute(
                sa.text(
                    "SELECT id, notify_frequency, notify_push "
                    "FROM saved_searches "
                    "WHERE profile_id = :p AND name = :n FOR UPDATE"
                ),
                {"p": profile_id, "n": name},
            )
        ).one_or_none()
        if antes is None:
            counts["not_found"] += 1
            continue
        if (str(antes.notify_frequency) == str(freq)
                and bool(antes.notify_push) == bool(push)):
            continue
        manifest["notify_fixups"].append({
            "saved_search_id": str(antes.id),
            "notify_frequency_antes": str(antes.notify_frequency),
            "notify_push_antes": bool(antes.notify_push),
        })
        await session.execute(
            sa.text(
                "UPDATE saved_searches "
                "SET notify_frequency = CAST(:f AS notify_frequency), "
                "notify_push = :push WHERE id = :id"
            ),
            {"f": freq, "push": bool(push), "id": antes.id},
        )
        counts["fixed"] += 1
    return counts


async def run_import(session: AsyncSession, plan: dict) -> dict:
    """Ejecuta la migración COMPLETA en la transacción de la sesión dada (el
    llamador comete o revierte). `plan` = {profiles: {external: profile_id},
    feedback: {external: [rows]}, saved_searches: {external: [rows]},
    exclusions: {external: [patterns]}}. Devuelve el MANIFIESTO."""
    manifest = {
        "version": "swissjob-durables-v1",
        "pvs": [], "saved_search_ids": [],
        "filters_fixups": [], "notify_fixups": [],
        "counts": {},
    }
    for externo, pid in plan["profiles"].items():
        pid = uuid.UUID(str(pid))
        antes_ids = {
            str(r)
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT id FROM saved_searches WHERE profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).scalars()
        }
        c_fb = await migrate_feedback(
            session, pid, plan.get("feedback", {}).get(externo, []), manifest)
        # Dedup por (profile_id, name) ANTES del insert del piloto: los
        # fix-ups posteriores (notify, exclusiones) mutan la tupla material
        # que usa su existence-check y un re-run insertaría duplicados. En
        # los durables de SwissJob el name es único por usuario.
        filas_ss = list(plan.get("saved_searches", {}).get(externo, []))
        nombres_previos = {
            r
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT name FROM saved_searches WHERE profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).scalars()
        }
        nuevas = [
            r for r in filas_ss
            if (r.get("name") or "")[:200] not in nombres_previos
        ]
        c_ss = await migrate_saved_searches(session, pid, nuevas)
        c_ss["existing"] = c_ss.get("existing", 0) + (
            len(filas_ss) - len(nuevas))
        despues = {
            str(r)
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT id FROM saved_searches WHERE profile_id = :p"
                    ),
                    {"p": pid},
                )
            ).scalars()
        }
        manifest["saved_search_ids"].extend(sorted(despues - antes_ids))
        c_nf = await fixup_notify(
            session, pid, plan.get("saved_searches", {}).get(externo, []),
            manifest)
        c_ex = await attach_exclusion_filters(
            session, pid, plan.get("exclusions", {}).get(externo, []), manifest)
        manifest["counts"][externo] = {
            "feedback": c_fb, "saved_searches": c_ss,
            "notify": c_nf, "exclusions": c_ex,
        }
    return manifest


async def rollback_import(session: AsyncSession, manifest: dict) -> dict:
    """Deshace por VALORES del manifiesto: pvs restaurada a su estado previo
    exacto (fila nueva sin otros campos ⇒ se borra; fila preexistente ⇒ se
    restauran feedback/dismissed_at anteriores, sin tocar nada más);
    búsquedas insertadas ⇒ DELETE por id; fix-ups ⇒ valores anteriores."""
    counts = {"pvs_deleted": 0, "pvs_restored": 0, "searches_deleted": 0,
              "filters_restored": 0, "notify_restored": 0}
    for e in manifest["pvs"]:
        if not e["existed"]:
            # Fila creada por la migración: se borra SOLO si no ha ganado
            # otros campos desde entonces (current_eval_id/saved/notes).
            borrada = (
                await session.execute(
                    sa.text(
                        "DELETE FROM profile_vacancy_state "
                        "WHERE profile_id = :p AND vacancy_id = :v "
                        "AND current_eval_id IS NULL AND saved_at IS NULL "
                        "AND notes IS NULL RETURNING vacancy_id"
                    ),
                    {"p": e["profile_id"], "v": e["vacancy_id"]},
                )
            ).scalar_one_or_none()
            if borrada is not None:
                counts["pvs_deleted"] += 1
                continue
        await session.execute(
            sa.text(
                "UPDATE profile_vacancy_state "
                "SET feedback = :f, dismissed_at = CAST(:d AS timestamptz) "
                "WHERE profile_id = :p AND vacancy_id = :v"
            ),
            {
                "f": e["feedback_antes"], "d": e["dismissed_at_antes"],
                "p": e["profile_id"], "v": e["vacancy_id"],
            },
        )
        counts["pvs_restored"] += 1
    for fx in manifest["filters_fixups"]:
        await session.execute(
            sa.text(
                "UPDATE saved_searches SET filters = CAST(:f AS jsonb) "
                "WHERE id = :id"
            ),
            {"f": sa_json(fx["filters_antes"] or {}),
             "id": fx["saved_search_id"]},
        )
        counts["filters_restored"] += 1
    for fx in manifest["notify_fixups"]:
        await session.execute(
            sa.text(
                "UPDATE saved_searches "
                "SET notify_frequency = CAST(:f AS notify_frequency), "
                "notify_push = :push WHERE id = :id"
            ),
            {"f": fx["notify_frequency_antes"],
             "push": fx["notify_push_antes"], "id": fx["saved_search_id"]},
        )
        counts["notify_restored"] += 1
    for sid in manifest["saved_search_ids"]:
        await session.execute(
            sa.text("DELETE FROM saved_searches WHERE id = :id"), {"id": sid})
        counts["searches_deleted"] += 1
    return counts
