"""Manifiesto INDEPENDIENTE de reconciliación de la migración del portfolio (C-4).

Cierra el hueco de la reconciliación (P2 rev. externa): los checksums de
import_portfolio_migrate se computan DESDE el destino, así que un error
DETERMINISTA (p.ej. transformar siempre min_score=60→0) pasaría rerun Y
comparación cross-BD porque AMBOS destinos fallan igual. Aquí se derivan del
ORIGEN, por una vía INDEPENDIENTE de la migración (segunda implementación que lee
los campos crudos del durable), los resultados ESPERADOS, se contrastan con el
DESTINO real y se persiste un manifiesto DURABLE antes del commit (para que el
informe sobreviva aunque el proceso muera tras confirmar).

Se apoya en las mismas reglas de mapeo que la parte 2 pero SIN pasar por ella:
si ambas coinciden, confianza; si divergen, hay bug de transformación.
"""

import json
import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.sink import normalize_url
from jobhunt_core.import_portfolio import PORTFOLIO_IMPORT_SOURCE
from jobhunt_core.import_portfolio_durables import (
    PORTFOLIO_CONSUMER,
    SAVED_SEARCH_NAME_MAX,
)

logger = logging.getLogger(__name__)


def _canon(value) -> str:
    """JSON canónico (claves ordenadas) para comparar filters origen↔destino sin
    depender del orden de claves."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _expected_saved_searches(users: list[dict]) -> set[tuple]:
    """Tuplas materiales ESPERADAS en el destino, derivadas del ORIGEN por una vía
    independiente (lee min_score/is_active/filters crudos): (external_ref, name,
    filters_canónico, min_score, is_active). invalid→{}+desactivada; sin name se
    omite (va a staged, no al destino). Dedup por tupla (misma regla que parte 2)."""
    expected: set[tuple] = set()
    for user in users:
        ref = str(user["external_ref"])
        for row in user.get("saved_searches") or []:
            name = row.get("name")
            if not name or not isinstance(name, str):
                continue
            name = name[:SAVED_SEARCH_NAME_MAX]
            raw = row.get("filters")
            try:
                filters = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                filters = None
            invalid = not isinstance(filters, dict)
            if invalid:
                filters = {}
            min_score = int(row.get("min_score") or 0)
            is_active = False if invalid else bool(row.get("is_active", True))
            expected.add((ref, name, _canon(filters), min_score, is_active))
    return expected


async def _actual_saved_searches(session: AsyncSession) -> set[tuple]:
    """Tuplas materiales REALES del destino (scopeadas al consumer portfolio)."""
    rows = await session.execute(
        sa.text(
            "SELECT p.external_ref, ss.name, ss.filters, ss.min_score, ss.is_active "
            "FROM saved_searches ss JOIN profiles p ON p.id = ss.profile_id "
            "JOIN consumers c ON c.id = p.consumer_id AND c.name = :consumer"
        ),
        {"consumer": PORTFOLIO_CONSUMER},
    )
    return {
        (r.external_ref, r.name, _canon(r.filters), int(r.min_score), bool(r.is_active))
        for r in rows
    }


def _expected_vacancy_keys(users: list[dict]) -> tuple[set[str], set[str]]:
    """(claves_limpias, claves_colisión) de url_normalized ESPERADAS del origen.
    Colisión = una clave con >1 url ORIGINAL distinta (no se sintetiza). Ignora
    urls ausentes/malformadas (van a staged). Vía independiente de synthesize."""
    by_key: dict[str, set[str]] = {}
    for user in users:
        for row in user.get("applications") or []:
            url = row.get("url")
            if not url:
                continue
            try:
                key = normalize_url(url)
            except ValueError:
                continue
            by_key.setdefault(key, set()).add(url)
    clean = {k for k, urls in by_key.items() if len(urls) == 1}
    collided = {k for k, urls in by_key.items() if len(urls) > 1}
    return clean, collided


async def _actual_vacancy_keys(session: AsyncSession) -> set[str]:
    """url_normalized de las vacantes-sombra portfolio-import presentables."""
    rows = await session.execute(
        sa.text(
            "SELECT sl.url_normalized FROM vacancies v "
            "JOIN source_listing_incarnations i "
            "  ON i.vacancy_id = v.id AND i.ended_at IS NULL "
            "JOIN source_listings sl ON sl.id = i.source_listing_id "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "WHERE v.merged_into IS NULL AND v.archived_at IS NULL"
        ),
        {"src": PORTFOLIO_IMPORT_SOURCE},
    )
    return {r.url_normalized for r in rows}


async def _preexisting_in_corpus(
    session: AsyncSession, keys: set[str]
) -> set[str]:
    """De `keys`, las url_normalized que YA existían en el corpus bajo OTRA fuente
    (candidatas a REUTILIZACIÓN vs vacante-sombra NUEVA) — inventario del cutover."""
    if not keys:
        return set()
    rows = await session.execute(
        sa.text(
            "SELECT DISTINCT sl.url_normalized FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name <> :src "
            "WHERE sl.url_normalized IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": PORTFOLIO_IMPORT_SOURCE, "keys": list(keys)},
    )
    return {r.url_normalized for r in rows}


async def reconcile(session: AsyncSession, users: list[dict]) -> dict:
    """Contrasta el DESTINO (ya migrado en esta sesión) contra lo ESPERADO del
    ORIGEN y devuelve el manifiesto {verdict, saved_searches, vacancies,
    divergences}. verdict='ok' si reconcilia; 'divergent' si hay bug de
    transformación (lista las divergencias). Llamar TRAS migrate_portfolio, ANTES
    del commit (así un dry-run también lo evalúa)."""
    exp_ss = _expected_saved_searches(users)
    act_ss = await _actual_saved_searches(session)
    clean_keys, collided_keys = _expected_vacancy_keys(users)
    act_keys = await _actual_vacancy_keys(session)
    preexisting = await _preexisting_in_corpus(session, clean_keys)

    divergences: list[str] = []
    missing_ss = exp_ss - act_ss
    extra_ss = act_ss - exp_ss
    if missing_ss:
        divergences.append(f"saved_searches esperadas ausentes en destino: {sorted(missing_ss)}")
    if extra_ss:
        divergences.append(f"saved_searches en destino no esperadas del origen: {sorted(extra_ss)}")
    if clean_keys != act_keys:
        divergences.append(
            f"vacantes-sombra esperadas != destino (faltan {sorted(clean_keys - act_keys)}, "
            f"sobran {sorted(act_keys - clean_keys)})"
        )

    manifest = {
        "verdict": "ok" if not divergences else "divergent",
        "saved_searches": {
            "expected": len(exp_ss), "actual": len(act_ss),
            "missing": sorted(missing_ss), "extra": sorted(extra_ss),
        },
        "vacancies": {
            "expected_clean": len(clean_keys), "actual": len(act_keys),
            "collisions": sorted(collided_keys),
            "preexisting_in_corpus": sorted(preexisting),  # candidatas a reutilización
            "new_shadows": len(clean_keys - preexisting),
        },
        "divergences": divergences,
    }
    logger.info(
        "import_portfolio_manifest: reconciliación %s (%d divergencias)",
        manifest["verdict"], len(divergences),
    )
    return manifest


async def persist_manifest(session: AsyncSession, manifest: dict) -> uuid.UUID:
    """Persiste el manifiesto en portfolio_migration_manifest (core0013) DENTRO de
    la transacción del llamador → se confirma atómicamente con la migración (o se
    revierte con ella en un dry-run). El informe sobrevive aunque el proceso muera
    tras el commit — P2 rev. externa. Devuelve el id de la fila."""
    manifest_id = uuid.uuid4()
    await session.execute(
        sa.text(
            "INSERT INTO portfolio_migration_manifest (id, verdict, manifest) "
            "VALUES (:id, :v, CAST(:m AS jsonb))"
        ),
        {
            "id": manifest_id, "v": manifest["verdict"],
            "m": json.dumps(manifest, ensure_ascii=False),
        },
    )
    return manifest_id
