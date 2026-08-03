"""Verificación estructural INDEPENDIENTE de la importación del portfolio (§4, parte 3;
adelantada en LOCAL, ejecución sobre datos reales gated al NAS).

`reconcile` (scaffold C-4) lee la ESTRUCTURA del estado final, así que un fallo del sink que
PIERDA un listing válido NO se distingue de una cuarentena legítima (ambos: "no hay vacante").
Este verificador SÍ los distingue usando el LEDGER (parte 1) como CONTRATO de lo que la síntesis
DIJO haber hecho, y contrastándolo contra el estado real leído de forma INDEPENDIENTE:

- CADA url de origen (no vacía) debe tener UNA entrada en el ledger (COMPLETITUD — ninguna se
  pierde en silencio antes de la síntesis).
- ledger `created` ⇒ existe una vacante-sombra portfolio-import PRESENTABLE con ESE vacancy_id y
  SIN otra fuente. Si no existe → LISTING PERDIDO (el sink debía crearla y no lo hizo), NO una
  cuarentena. Si tiene otra fuente → debería ser `reused`.
- ledger `reused` ⇒ existe la vacante (presentable) con ese vacancy_id. Si no → perdido.
- ledger `quarantine:collision_*` ⇒ la url NO resuelve a ninguna vacante portfolio-import (no se
  sintetizó — cuarentena LEGÍTIMA). Si resuelve → cuarentena espuria (inconsistencia).
- CROSS-CHECK entre oráculos INDEPENDIENTES: {vacancy_id de las `created` del ledger} ==
  procedencia exacta de `vacancies` (parte 2). Dos derivaciones distintas del mismo hecho.

ALCANCE (división con reconcile): este verificador cubre la ESTRUCTURA del CORPUS (síntesis del
sink: lo que reconcile NO podía distinguir — perdido vs cuarentena). Los DURABLES
(applications/profile_vacancy_state/saved_searches) los verifica `reconcile` por VALORES
MATERIALES contra el ORIGEN, en la MISMA transacción que aborta el cutover si divergen. Juntos
(reconcile material + verify estructural) cubren durables y corpus; por eso aquí no se re-verifican
los durables (sería redundante con reconcile).

Es de SOLO LECTURA (no muta). Devuelve verdict 'verified' | 'discrepant' + la lista de
discrepancias. NO importa import_portfolio (recibe source_name) — sin ciclo. La query del estado
final es PROPIA (no reusa la del ledger) para que un bug en la síntesis no se enmascare a sí mismo.
"""

import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_CREATED = "created"
_REUSED = "reused"
_QUARANTINE = "quarantine"
_COLLISION_REASONS = ("collision_intra", "collision_cross_run", "collision_cross_source")


async def _portfolio_vacancies_for_keys(
    session: AsyncSession, url_normalizeds: list[str], source_name: str
) -> dict[str, dict]:
    """{url_normalized: {vacancy_id(str), has_other_source(bool)}} de la vacante-sombra
    portfolio-import PRESENTABLE de cada clave. Lectura INDEPENDIENTE del estado FINAL (query
    propia del verificador). Una clave SIN vacante presentable NO aparece en el dict."""
    keys = list({u for u in url_normalizeds if u})
    if not keys:
        return {}
    rows = await session.execute(
        sa.text(
            "SELECT sl.url_normalized AS urln, v.id::text AS vacancy_id, EXISTS ("
            "  SELECT 1 FROM source_listing_incarnations oi "
            "  JOIN source_listings osl ON osl.id = oi.source_listing_id "
            "  JOIN sources os ON os.id = osl.source_id AND os.name <> :src "
            "  WHERE oi.vacancy_id = v.id AND oi.ended_at IS NULL) AS has_other_source "
            "FROM source_listings sl "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN source_listing_incarnations i "
            "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "WHERE sl.url_normalized IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"src": source_name, "keys": keys},
    )
    return {r.urln: {"vacancy_id": r.vacancy_id, "has_other_source": r.has_other_source} for r in rows}


async def verify_migration(
    session: AsyncSession,
    users: list[dict],
    ledger: list[dict],
    provenance: dict[str, list[str]],
    source_name: str,
) -> dict:
    """Verifica que cada url de origen está contabilizada (migrada o cuarentena legítima),
    distinguiendo un listing PERDIDO de una cuarentena. `ledger`/`provenance` = los del
    manifiesto (parte 1/2). Solo lectura. Devuelve {verdict, discrepancies, checked}."""
    discrepancies: list[str] = []

    # 1. COMPLETITUD: cada url de origen (no vacía) tiene entrada en el ledger.
    input_urls = {
        row["url"]
        for u in users
        for row in (u.get("applications") or [])
        if row.get("url")
    }
    ledger_urls = {e["url"] for e in ledger if e["url"] is not None}
    lost_before = input_urls - ledger_urls
    if lost_before:
        discrepancies.append(
            f"{len(lost_before)} url(s) de origen SIN entrada en el ledger (perdidas antes "
            f"de sintetizar): {sorted(lost_before)[:5]}"
        )

    # 2-4. Estado FINAL independiente de las urls sintetizadas (created/reused).
    synth = [e for e in ledger if e["disposition"] in (_CREATED, _REUSED)]
    final = await _portfolio_vacancies_for_keys(
        session, [e["url_normalized"] for e in synth], source_name
    )
    for e in synth:
        got = final.get(e["url_normalized"])
        if got is None:
            discrepancies.append(
                f"ledger {e['disposition']} de {e['url']} pero NO hay vacante-sombra "
                f"presentable → LISTING PERDIDO (fallo del sink), no cuarentena"
            )
            continue
        if got["vacancy_id"] != e["vacancy_id"]:
            discrepancies.append(
                f"ledger {e['disposition']} de {e['url']}: vacancy_id {e['vacancy_id']} "
                f"≠ el del estado final {got['vacancy_id']}"
            )
        if e["disposition"] == _CREATED and got["has_other_source"]:
            discrepancies.append(
                f"ledger created de {e['url']} pero la vacante tiene OTRA fuente → "
                f"debería ser reused (clasificación errónea)"
            )

    # 5. QUARANTINE por colisión: la url ORIGINAL colisionada NO debe quedar VINCULADA a una
    # vacante portfolio-import (la clave puede existir por la url GANADORA de otra ejecución;
    # lo ilegítimo es que ESTA url resuelva — habría un vínculo falso que la cuarentena evita).
    quarantined = [
        e for e in ledger if e["disposition"] == _QUARANTINE and e["reason"] in _COLLISION_REASONS
    ]
    for e in quarantined:
        if await _url_resolves(session, e["url"], source_name):
            discrepancies.append(
                f"ledger quarantine:{e['reason']} de {e['url']} pero la url RESUELVE a una "
                f"vacante portfolio-import → cuarentena espuria (vínculo falso no evitado)"
            )

    # 6. CROSS-CHECK oráculos independientes: created del ledger == procedencia de vacancies.
    # Un created con vacancy_id None es un LISTING PERDIDO (build_ledger, anomalía del sink) — ya
    # se marcó como PERDIDO en el paso 2-4; se EXCLUYE aquí (si no, mezclaría None y str y
    # `sorted` lanzaría TypeError, crasheando el verificador en el caso que existe para detectar).
    created_vac = {
        e["vacancy_id"]
        for e in ledger
        if e["disposition"] == _CREATED and e["vacancy_id"] is not None
    }
    prov_vac = set(provenance.get("vacancies", []))
    if created_vac != prov_vac:
        discrepancies.append(
            f"desacuerdo oráculos: vacancies created del ledger {sorted(created_vac)[:5]} "
            f"≠ procedencia de vacancies {sorted(prov_vac)[:5]} "
            f"(faltan {len(created_vac - prov_vac)}, sobran {len(prov_vac - created_vac)})"
        )

    verdict = "verified" if not discrepancies else "discrepant"
    checked = {
        "input_urls": len(input_urls),
        "ledger_entries": len(ledger),
        "synthesized": len(synth),
        "quarantined_collision": len(quarantined),
        "created_vacancies": len(created_vac),
    }
    if discrepancies:
        logger.error(
            "import_portfolio_verify: DISCREPANT — %s", " | ".join(discrepancies)
        )
    else:
        logger.info("import_portfolio_verify: verified (%s)", checked)
    return {"verdict": verdict, "discrepancies": discrepancies, "checked": checked}


async def _url_resolves(session: AsyncSession, url: str, source_name: str) -> bool:
    """True si esa url ORIGINAL tiene una incarnación ACTIVA portfolio-import en una vacante
    presentable. Distingue "la url colisionada quedó vinculada" (ilegítimo) de "la clave
    existe por la url ganadora" (legítimo)."""
    row = await session.execute(
        sa.text(
            "SELECT 1 FROM source_listing_incarnations i "
            "JOIN source_listings sl ON sl.id = i.source_listing_id "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "JOIN vacancies v ON v.id = i.vacancy_id "
            "  AND v.merged_into IS NULL AND v.archived_at IS NULL "
            "WHERE i.ended_at IS NULL AND i.url = :url LIMIT 1"
        ),
        {"src": source_name, "url": url},
    )
    return row.first() is not None
