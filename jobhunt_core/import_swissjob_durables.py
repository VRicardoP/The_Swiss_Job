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
- job_filters legacy (patrones de exclusión POR USUARIO) →
  `profile_exclusions` (core0041), configuración AUTORITATIVA del perfil que
  la recuperación de candidatos aplica con la misma semántica que el legacy.
  Antes se escribían en una clave JSONB de las búsquedas guardadas que NINGÚN
  consumidor leía: el flip perdía comportamiento en silencio (revisión
  externa 2026-09-07, P1-4).

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


async def resolve_vacancies_by_incarnation_urls(session: AsyncSession, urls) -> dict:
    """Same clone/merge resolution in one corpus scan, grouped by exact URL."""
    result = {url: [] for url in urls}
    if not result:
        return result
    rows = (
        await session.execute(
            sa.text(
                "WITH RECURSIVE origen AS ("
                " SELECT DISTINCT i.url, i.vacancy_id AS vid FROM source_listing_incarnations i"
                " WHERE i.url = ANY(CAST(:urls AS text[]))"
                "), cadena AS ("
                " SELECT o.url, o.vid AS actual, ARRAY[o.vid] AS visitados FROM origen o"
                " UNION ALL"
                " SELECT c.url, v.merged_into, c.visitados || v.merged_into"
                " FROM cadena c JOIN vacancies v ON v.id=c.actual"
                " WHERE v.merged_into IS NOT NULL AND NOT(v.merged_into=ANY(c.visitados))"
                ") SELECT DISTINCT c.url,c.actual FROM cadena c"
                " JOIN vacancies v ON v.id=c.actual WHERE v.merged_into IS NULL"
                " ORDER BY c.url,c.actual"
            ),
            {"urls": list(result)},
        )
    ).all()
    for url, vid in rows:
        result[url].append(vid)
    return result


async def resolve_vacancies_by_incarnation_url(session: AsyncSession, url: str) -> list:
    """TODAS las vacantes ganadoras para una URL de oferta legacy.

    Revisión externa 2026-09-07 (P1-5): antes tomaba UNA encarnación
    (`ORDER BY seq DESC LIMIT 1`) y seguía fusiones con un bucle acotado que
    podía devolver el último *loser* visitado. Ahora:

    - se consideran TODAS las encarnaciones con esa url (cualquier fuente);
    - cada una se resuelve a su ganadora final por CTE recursivo, con
      detección de ciclo (`merged_into` circular no cuelga ni miente);
    - se devuelven TODAS las ganadoras distintas.

    Por qué todas y no una: la deriva de identidad (documentada en la salud de
    la cosecha) hace que una misma oferta re-listada entre como CLON, así que
    una url legacy resuelve a varias vacantes vivas — hasta 4 en el ensayo del
    2026-09-07. Enlazar a una arbitraria (lo que hacía la versión original)
    pierde la intención del usuario en las demás; rechazar la pierde entera.
    Son la MISMA oferta: el feedback se aplica a todas ellas.
    """
    filas = (await resolve_vacancies_by_incarnation_urls(session, [url]))[url]
    if len(filas) > 1:
        logger.info(
            "import_swissjob: la url %.80s resuelve a %d vacantes (clones de "
            "la misma oferta) — el feedback se aplica a todas",
            url,
            len(filas),
        )
    return list(filas)


async def migrate_feedback(
    session: AsyncSession, profile_id, rows: list[dict], manifest: dict
) -> dict:
    """rows: [{url, feedback, created_at}]. Escribe pvs preservando estado.

    Por cada fila tocada, el manifiesto guarda {vacancy_id, existed,
    feedback_antes, dismissed_at_antes} — rollback por VALORES exactos."""
    counts = {"migrated": 0, "kept_existing": 0, "unresolved": 0, "invalid_feedback": 0}
    # P1-5: varias entradas legacy pueden converger en la MISMA vacante. La
    # imagen previa se captura UNA sola vez por clave natural — si no, la
    # segunda captura el valor que acaba de escribir la primera y el rollback
    # lo restauraría.
    capturadas = {(e["profile_id"], e["vacancy_id"]) for e in manifest["pvs"]}
    for row in rows:
        fb = row.get("feedback")
        if fb not in VALID_FEEDBACK:
            counts["invalid_feedback"] += 1
            logger.warning("import_swissjob: feedback inválido %r — OMITIDO", fb)
            continue
        vids = await resolve_vacancies_by_incarnation_url(session, row["url"])
        if not vids:
            counts["unresolved"] += 1
            logger.warning(
                "import_swissjob: URL sin vacante core — OMITIDA: %.80s",
                row.get("url"),
            )
            continue
        for vid in vids:
            await _aplicar_feedback(
                session, profile_id, vid, row, fb, manifest, capturadas, counts
            )
    return counts


async def _aplicar_feedback(
    session, profile_id, vid, row, fb, manifest, capturadas, counts
) -> None:
    """Escribe el feedback en UNA pareja (perfil, vacante): preserva el estado
    existente (ADR-03) y captura UNA sola imagen previa por clave natural."""
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
    clave = (str(profile_id), str(vid))
    if clave not in capturadas:
        capturadas.add(clave)
        manifest["pvs"].append(
            {
                "profile_id": str(profile_id),
                "vacancy_id": str(vid),
                "existed": antes is not None,
                "feedback_antes": antes.feedback if antes else None,
                "dismissed_at_antes": (
                    antes.dismissed_at.isoformat()
                    if antes and antes.dismissed_at
                    else None
                ),
                "feedback_escrito": fb,
            }
        )
    if antes is not None and antes.feedback is not None:
        counts["kept_existing"] += 1  # ADR-03: jamás pisar estado
        return
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


async def attach_exclusion_filters(
    session: AsyncSession, profile_id, exclusiones: list, manifest: dict
) -> dict:
    """`exclusiones`: [{kind, pattern}] (kind ∈ title_contains|tag_contains)
    → `profile_exclusions` (core0041). Idempotente por PK natural; el
    manifiesto guarda SOLO las filas realmente insertadas, para un rollback
    que no borre exclusiones preexistentes."""
    counts = {"insertadas": 0, "ya_presentes": 0, "invalidas": 0}
    for e in exclusiones or []:
        kind = (e or {}).get("kind")
        patron = ((e or {}).get("pattern") or "").strip()
        if kind not in ("title_contains", "tag_contains") or not patron:
            counts["invalidas"] += 1
            logger.warning("import_swissjob: exclusión inválida %r", e)
            continue
        insertada = (
            await session.execute(
                sa.text(
                    "INSERT INTO profile_exclusions "
                    "(profile_id, kind, pattern) VALUES (:p, :k, :pat) "
                    "ON CONFLICT DO NOTHING RETURNING pattern"
                ),
                {"p": profile_id, "k": kind, "pat": patron},
            )
        ).scalar_one_or_none()
        if insertada is None:
            counts["ya_presentes"] += 1
            continue
        manifest["exclusiones"].append(
            {
                "profile_id": str(profile_id),
                "kind": kind,
                "pattern": patron,
            }
        )
        counts["insertadas"] += 1
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
        if str(antes.notify_frequency) == str(freq) and bool(antes.notify_push) == bool(
            push
        ):
            continue
        manifest["notify_fixups"].append(
            {
                "saved_search_id": str(antes.id),
                "notify_frequency_antes": str(antes.notify_frequency),
                "notify_push_antes": bool(antes.notify_push),
            }
        )
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
        "pvs": [],
        "saved_search_ids": [],
        "exclusiones": [],
        "notify_fixups": [],
        "counts": {},
    }
    for externo, pid in plan["profiles"].items():
        pid = uuid.UUID(str(pid))
        antes_ids = {
            str(r)
            for r in (
                await session.execute(
                    sa.text("SELECT id FROM saved_searches WHERE profile_id = :p"),
                    {"p": pid},
                )
            ).scalars()
        }
        c_fb = await migrate_feedback(
            session, pid, plan.get("feedback", {}).get(externo, []), manifest
        )
        # Dedup por (profile_id, name) ANTES del insert del piloto: los
        # fix-ups posteriores (notify, exclusiones) mutan la tupla material
        # que usa su existence-check y un re-run insertaría duplicados. En
        # los durables de SwissJob el name es único por usuario.
        filas_ss = list(plan.get("saved_searches", {}).get(externo, []))
        nombres_previos = {
            r
            for r in (
                await session.execute(
                    sa.text("SELECT name FROM saved_searches WHERE profile_id = :p"),
                    {"p": pid},
                )
            ).scalars()
        }
        nuevas = [
            r for r in filas_ss if (r.get("name") or "")[:200] not in nombres_previos
        ]
        c_ss = await migrate_saved_searches(session, pid, nuevas)
        c_ss["existing"] = c_ss.get("existing", 0) + (len(filas_ss) - len(nuevas))
        despues = {
            str(r)
            for r in (
                await session.execute(
                    sa.text("SELECT id FROM saved_searches WHERE profile_id = :p"),
                    {"p": pid},
                )
            ).scalars()
        }
        manifest["saved_search_ids"].extend(sorted(despues - antes_ids))
        c_nf = await fixup_notify(
            session, pid, plan.get("saved_searches", {}).get(externo, []), manifest
        )
        c_ex = await attach_exclusion_filters(
            session, pid, plan.get("exclusions", {}).get(externo, []), manifest
        )
        manifest["counts"][externo] = {
            "feedback": c_fb,
            "saved_searches": c_ss,
            "notify": c_nf,
            "exclusions": c_ex,
        }
    return manifest


async def rollback_import(session: AsyncSession, manifest: dict) -> dict:
    """Deshace por VALORES del manifiesto: pvs restaurada a su estado previo
    exacto (fila nueva sin otros campos ⇒ se borra; fila preexistente ⇒ se
    restauran feedback/dismissed_at anteriores, sin tocar nada más);
    búsquedas insertadas ⇒ DELETE por id; fix-ups ⇒ valores anteriores."""
    counts = {
        "pvs_deleted": 0,
        "pvs_restored": 0,
        "searches_deleted": 0,
        "exclusiones_borradas": 0,
        "notify_restored": 0,
    }
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
        # La marca previa viaja por el manifiesto como CADENA ISO (JSON no
        # tiene timestamps): asyncpg valida el TIPO del parámetro antes de
        # cualquier CAST, así que hay que devolverla a datetime. Solo se
        # notaba si la fila previa YA tenía dismissed_at — el caso real de
        # los thumbs_down migrados (ensayo fiel 2026-09-07).
        previo = e["dismissed_at_antes"]
        if isinstance(previo, str):
            from datetime import datetime

            previo = datetime.fromisoformat(previo)
        await session.execute(
            sa.text(
                "UPDATE profile_vacancy_state "
                "SET feedback = :f, dismissed_at = :d "
                "WHERE profile_id = :p AND vacancy_id = :v"
            ),
            {
                "f": e["feedback_antes"],
                "d": previo,
                "p": e["profile_id"],
                "v": e["vacancy_id"],
            },
        )
        counts["pvs_restored"] += 1
    for e in manifest.get("exclusiones", []):
        await session.execute(
            sa.text(
                "DELETE FROM profile_exclusions WHERE profile_id = :p "
                "AND kind = :k AND pattern = :pat"
            ),
            {"p": e["profile_id"], "k": e["kind"], "pat": e["pattern"]},
        )
        counts["exclusiones_borradas"] += 1
    for fx in manifest["notify_fixups"]:
        await session.execute(
            sa.text(
                "UPDATE saved_searches "
                "SET notify_frequency = CAST(:f AS notify_frequency), "
                "notify_push = :push WHERE id = :id"
            ),
            {
                "f": fx["notify_frequency_antes"],
                "push": fx["notify_push_antes"],
                "id": fx["saved_search_id"],
            },
        )
        counts["notify_restored"] += 1
    for sid in manifest["saved_search_ids"]:
        await session.execute(
            sa.text("DELETE FROM saved_searches WHERE id = :id"), {"id": sid}
        )
        counts["searches_deleted"] += 1
    return counts
