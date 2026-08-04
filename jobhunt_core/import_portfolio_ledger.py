"""Ledger del sink de la importación del portfolio (C-4 → entregable del ensayo §4,
adelantado en LOCAL con fixtures sintéticos; solo la ejecución sobre datos reales queda
gated al NAS).

Registro VERIFICABLE por ENTRADA de la síntesis de vacantes-sombra: para cada URL de durable
que entra en `synthesize_vacancies`, su DISPOSICIÓN y —cuando aplica— la vacante resultante:

- `created`   : esta ejecución SINTETIZÓ una vacante-sombra NUEVA (no existía antes).
- `reused`    : la clave se adjuntó a una vacante PREEXISTENTE (de otra fuente, o de una
                ejecución portfolio-import previa) — C-4 solo añadió el enlace, no la creó.
- `quarantine`: no se sintetizó; con RAZÓN precisa (no_url/malformed/collision_*).

Es la BASE de los otros entregables del §4 (procedencia exacta, verificación independiente,
rollback FK-safe): sin un registro por entrada, una cuarentena LEGÍTIMA no se distingue de un
listing válido PERDIDO por un fallo del sink (RUNBOOK §4). El scaffold de `reconcile` lee la
estructura del estado final, así que no podía hacer esa distinción; el ledger sí.

`created` vs `reused` se decide con EXACTITUD: se captura el conjunto de vacantes
portfolio-import ANTES de sintetizar (`snapshot_portfolio_vacancy_ids`; la fuente es
single-writer con scope deshabilitado → sin escritor concurrente, race-free) y, tras
sintetizar, una vacante cuya id YA estaba en ese snapshot, o que tiene una incarnación de OTRA
fuente, es `reused`. El módulo NO importa `import_portfolio` (recibe `source_name` por
parámetro) — dependencia unidireccional import_portfolio → ledger, sin ciclo.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest.sink import normalize_url

logger = logging.getLogger(__name__)

# Disposiciones.
CREATED = "created"
REUSED = "reused"
QUARANTINE = "quarantine"

# Razones de cuarentena (solo cuando disposition == QUARANTINE).
Q_NO_URL = "no_url"
Q_MALFORMED = "malformed"
Q_NO_TITLE = "no_title"  # sin título normalizable → el sink no crearía canónica (impresentable)
Q_LIMIT = "limit"  # url > MAX_URL_LEN → el sink la cuarentena (frontera replicada, rev. externa)
Q_COLLISION_INTRA = "collision_intra"
Q_COLLISION_CROSS_RUN = "collision_cross_run"
Q_COLLISION_CROSS_SOURCE = "collision_cross_source"

# Razones que corresponden a una COLISIÓN (subconjunto de las de cuarentena): son las urls
# que el llamador enruta a staging vía el set `collided` de synthesize_vacancies.
COLLISION_REASONS = frozenset(
    {Q_COLLISION_INTRA, Q_COLLISION_CROSS_RUN, Q_COLLISION_CROSS_SOURCE}
)


@dataclass(frozen=True)
class LedgerEntry:
    """Disposición de UNA url de durable en la síntesis. `external_id` = sha256 de la url
    normalizada (misma clave del sink); None si no hay url normalizable."""

    url: str | None
    url_normalized: str | None
    external_id: str | None
    disposition: str
    reason: str | None
    vacancy_id: uuid.UUID | None

    def as_dict(self) -> dict:
        return {
            "url": self.url,
            "url_normalized": self.url_normalized,
            "external_id": self.external_id,
            "disposition": self.disposition,
            "reason": self.reason,
            "vacancy_id": str(self.vacancy_id) if self.vacancy_id is not None else None,
        }


def _external_id(url_normalized: str) -> str:
    return hashlib.sha256(url_normalized.encode()).hexdigest()


def _safe_key(url: str) -> tuple[str | None, str | None]:
    """(url_normalized, external_id) o (None, None) si la url no produce una clave ESTABLE.
    normalize_url NO falla ante un surrogate suelto (urlsplit/urlunsplit no codifican), pero
    _external_id SÍ (.encode() estricto lanza UnicodeEncodeError, subclase de ValueError) —
    la MISMA familia de mojibake que el sink cuarentena como 'malformed'. Una url así no
    tiene external_id en el corpus (el sink tampoco pudo crearla): se registra con clave
    None, y persist_manifest sanea el campo `url` crudo (encode 'replace') al serializar."""
    try:
        urln = normalize_url(url)
        return urln, _external_id(urln)
    except ValueError:
        return None, None


async def snapshot_portfolio_vacancy_ids(
    session: AsyncSession, source_name: str
) -> set[str]:
    """IDs (str) de las vacantes-sombra `source_name` PRESENTABLES ya persistidas — tomado
    ANTES de sintetizar. Una vacante resultante cuya id esté aquí PREEXISTÍA (de una ejecución
    previa) → `reused`, aunque su única fuente sea `source_name`. Race-free: la fuente no
    tiene escritor concurrente (scope deshabilitado)."""
    rows = await session.execute(
        sa.text(
            "SELECT DISTINCT v.id::text AS id FROM vacancies v "
            "JOIN source_listing_incarnations i ON i.vacancy_id = v.id AND i.ended_at IS NULL "
            "JOIN source_listings sl ON sl.id = i.source_listing_id "
            "JOIN sources s ON s.id = sl.source_id AND s.name = :src "
            "WHERE v.merged_into IS NULL AND v.archived_at IS NULL"
        ),
        {"src": source_name},
    )
    return {r.id for r in rows}


async def _synthesized_vacancy_info(
    session: AsyncSession, url_normalizeds: list[str], source_name: str
) -> dict[str, dict]:
    """{url_normalized: {vacancy_id, has_other_source}} de las vacantes-sombra `source_name`
    de `url_normalizeds` tras sintetizar. `has_other_source` = la vacante tiene ADEMÁS una
    incarnación de OTRA fuente (→ preexistente, reused). UNA query batched."""
    keys = list({u for u in url_normalizeds if u})
    if not keys:
        return {}
    rows = await session.execute(
        sa.text(
            "SELECT sl.url_normalized AS urln, v.id AS vacancy_id, EXISTS ("
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
    out: dict[str, dict] = {}
    for r in rows:
        out[r.urln] = {"vacancy_id": r.vacancy_id, "has_other_source": r.has_other_source}
    return out


async def build_ledger(
    session: AsyncSession,
    synthesized: dict[str, str],
    quarantined: dict[str, str],
    before_vacancy_ids: set[str],
    no_url_count: int,
    source_name: str,
) -> list[LedgerEntry]:
    """Materializa el ledger tras la síntesis. `synthesized`: url_normalized→url original de
    las que SÍ se sintetizaron; `quarantined`: url→razón de cuarentena; `before_vacancy_ids`:
    snapshot pre-síntesis (para created vs reused); `no_url_count`: items sin url."""
    entries: list[LedgerEntry] = []
    info = await _synthesized_vacancy_info(session, list(synthesized), source_name)
    for urln, url in synthesized.items():
        vinfo = info.get(urln)
        if vinfo is None:
            # Marcada como sintetizada pero SIN vacante resoluble tras el sink: anomalía
            # (posible listing perdido). Se registra fielmente con vacancy_id None; el
            # verificador independiente (§4) señala un created/reused sin vacante.
            logger.error(
                "import_portfolio_ledger: url sintetizada sin vacante resoluble (%s) — "
                "posible pérdida del sink; el verificador §4 lo marcará",
                url,
            )
            entries.append(LedgerEntry(url, urln, _external_id(urln), CREATED, None, None))
            continue
        vac = vinfo["vacancy_id"]
        reused = vinfo["has_other_source"] or str(vac) in before_vacancy_ids
        disposition = REUSED if reused else CREATED
        entries.append(LedgerEntry(url, urln, _external_id(urln), disposition, None, vac))
    for url, reason in quarantined.items():
        # normalize + external_id JUNTOS bajo el guard: una url con surrogate pasa
        # normalize_url pero revienta en el .encode() de _external_id (UnicodeEncodeError ⊂
        # ValueError). Sin esto, un solo durable con mojibake en la url aborta el cutover.
        urln, ext = _safe_key(url)
        entries.append(LedgerEntry(url, urln, ext, QUARANTINE, reason, None))
    for _ in range(no_url_count):
        entries.append(LedgerEntry(None, None, None, QUARANTINE, Q_NO_URL, None))
    return entries
