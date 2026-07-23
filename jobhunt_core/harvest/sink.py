"""Sink real de ingesta (A-04 + identidad A-05): slot + incarnación + revisión
RAW + re-enlace determinista, por LOTES.

A-05 (ADR-01, solo lo DETERMINISTA — nada semántico en Fase A):
- Guard de reciclado en el nivel exacto: empresa (tokens PF.5) distinta con
  contenido nuevo → cierra la incarnación y abre otra (vacante NUEVA).
- Cross-source fuerte: url_normalized vigente en OTRA fuente → attach a esa
  vacante + `link_evidence` (solo al CREAR la incarnación).
- Conflicto external_id↔URL: gana external_id; la URL queda como alias en
  `link_evidence`. Drift de URL entre fuentes y duplicados difusos intra-lote
  → `dedup_candidates` (pending; resolución = Fase B, jamás se funde aquí).

Contrato (CONTRATOS §1 + ADR-01/05, ticket A-04):
- El raw se persiste ANTES de normalizar: la revisión (`source_listing_revisions`)
  guarda el payload tal cual, colgando de la INCARNACIÓN.
- `last_seen_at` de la incarnación activa se refresca en CADA cosecha (también
  url/apply_url), aunque el contenido no cambie.
- Contenido cambiado ⇒ nueva revisión por `content_hash` (upsert idempotente:
  la EMISIÓN TOTAL de A-03 re-entrega todo y aquí se deduplica).
- Slot nuevo ⇒ vacante nueva + incarnación seq=1 (+ puntero primario). La
  RESOLUCIÓN de identidad (attach cross-source, guard de reciclado) es A-05:
  una vacante fresca por slot es el default seguro que A-05/merge refinan.

EFICIENCIA: todas las operaciones son por lote (executemany / ANY(...)): el
número de queries es O(1) respecto al tamaño del lote, no O(n).

Corre SIEMPRE dentro de la transacción del runner (sink+estado atómicos).
"""

import hashlib
import json
import logging
import uuid
from urllib.parse import urlsplit, urlunsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.harvest import identity
from jobhunt_core.harvest.types import RawListing

logger = logging.getLogger(__name__)

# Límites REALES del esquema (CONTRATOS §1 / core0002): validar en la frontera
# evita que UN listing sobredimensionado envenene el lote entero (rev. A-04 #2 —
# con la emisión total de A-03 el dato tóxico reaparecería en cada cosecha y
# bloquearía el scope indefinidamente).
MAX_EXTERNAL_ID_LEN = 200
MAX_URL_LEN = 1000


def _preprocess(listing: RawListing) -> tuple[str, str, str] | None:
    """(canon, chash, url_normalizada) — o None con CUARENTENA logueada.

    TODO el trabajo por-listing vive aquí DENTRO (rev. 2ª #2): serialización,
    hash, normalización de URL y validación UTF-8 — ninguna entrada individual
    puede abortar el lote válido (con la emisión total de A-03, un dato tóxico
    reaparecería en cada cosecha y bloquearía el scope para siempre)."""
    try:
        canon = canonical_payload(listing.payload)
        # encode() detecta surrogates sueltos (json.loads puede producirlos
        # desde escapes \\uD800 del feed): pasan dumps pero revientan en BD.
        chash = hashlib.sha256(canon.encode()).hexdigest()
        url_norm = normalize_url(listing.url)  # p.ej. 'https://[inv' → ValueError
        listing.external_id.encode()
        listing.url.encode()
        (listing.apply_url or "").encode()
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        # ascii(): el propio external_id puede ser incodificable — el log
        # nunca debe reventar por el dato que está aislando.
        logger.warning(
            "sink: listing %s EN CUARENTENA (preprocesado imposible: %s)",
            ascii(listing.external_id)[:80], exc,
        )
        return None
    reasons = _limit_violations(listing, url_norm)
    if reasons:
        logger.warning(
            "sink: listing %r EN CUARENTENA (%s) — no envenena el lote",
            listing.external_id[:80], "; ".join(reasons),
        )
        return None
    return canon, chash, url_norm


def _limit_violations(listing: RawListing, url_norm: str) -> list[str]:
    """Límites reales del esquema + NUL (Postgres lo rechaza en text y jsonb)."""
    reasons = []
    if len(listing.external_id) > MAX_EXTERNAL_ID_LEN:
        reasons.append(f"external_id > {MAX_EXTERNAL_ID_LEN}")
    if len(listing.url) > MAX_URL_LEN or len(url_norm) > MAX_URL_LEN:
        reasons.append(f"url > {MAX_URL_LEN}")
    if listing.apply_url and len(listing.apply_url) > MAX_URL_LEN:
        reasons.append(f"apply_url > {MAX_URL_LEN}")
    if (
        "\x00" in listing.external_id
        or "\x00" in listing.url
        or "\x00" in (listing.apply_url or "")
        or _payload_has_nul(listing.payload)
    ):
        reasons.append("NUL (rechazado por Postgres)")
    return reasons


def _payload_has_nul(value) -> bool:
    """NUL en los VALORES reales del payload (json.loads solo produce
    dict/list/str/num/bool/None). Buscar la secuencia escapada en la canónica
    daría falsos positivos con textos legítimos que contengan literalmente
    '\\u0000' (rev. 2ª #2)."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_payload_has_nul(k) or _payload_has_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_payload_has_nul(v) for v in value)
    return False


def normalize_url(url: str) -> str:
    """Clave de dedup por URL: esquema/host en minúsculas, sin fragmento ni
    barra final. La query SE CONSERVA (muchos portales llevan el id ahí)."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, parts.query, "")
    )


def canonical_payload(payload: dict) -> str:
    """Serialización canónica ÚNICA (auditoría A-04 #5): la MISMA cadena se
    hashea y se persiste como raw — un payload hasheable siempre es persistible
    (default=str en ambos usos), y solo se serializa una vez (#3).
    allow_nan=False (rev. 2ª #2): jsonb rechaza NaN/Infinity — mejor ValueError
    en la cuarentena de frontera que abortar el lote entero en el INSERT."""
    return json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False
    )


def content_hash(payload: dict) -> str:
    """Hash ESTABLE del payload raw (orden de claves canónico)."""
    return hashlib.sha256(canonical_payload(payload).encode()).hexdigest()


class RawListingSink:
    """ListingSink de producción (A-04)."""

    async def handle(
        self, session: AsyncSession, scope_id: str, listings: tuple[RawListing, ...]
    ) -> None:
        if not listings:
            return
        src = (
            await session.execute(
                sa.text(
                    "SELECT hs.source_id, s.name AS source_name "
                    "FROM harvest_scopes hs JOIN sources s ON s.id = hs.source_id "
                    "WHERE hs.id = :sid"
                ),
                {"sid": scope_id},
            )
        ).one()
        source_id, source_name = src.source_id, src.source_name

        # CUARENTENA de frontera (rev. #2 y 2ª #2): TODO el preprocesado
        # por-listing (canónica, hash, URL normalizada, UTF-8) ocurre dentro de
        # _preprocess ANTES de tocar la BD — y antes del advisory lock: un lote
        # 100% tóxico ni siquiera serializa la fuente.
        valid: list[RawListing] = []
        prep_by_ext: dict[str, tuple[str, str, str]] = {}
        for listing in listings:
            prep = _preprocess(listing)
            if prep is None:
                continue
            valid.append(listing)
            prep_by_ext[listing.external_id] = prep
        if not valid:
            return

        # SERIALIZACIÓN POR FUENTE (rev. A-04 #1): dos scopes de la MISMA fuente
        # tocan los mismos slots con DOS claves UNIQUE (external_id y
        # url_normalized) — ningún orden por una sola clave evita el deadlock
        # cruzado. El advisory lock transaccional (se libera en commit/rollback)
        # serializa por fuente y mantiene el paralelismo ENTRE fuentes.
        await session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:src, 0))"),
            {"src": str(source_id)},
        )

        # Dedup DENTRO del lote por external_id (la última aparición VÁLIDA gana).
        by_ext = {listing.external_id: listing for listing in valid}
        slot_by_ext, stored_urln = await self._ensure_slots(
            session, source_id, by_ext, prep_by_ext
        )
        inc_by_slot = await self._active_incarnations(session, list(slot_by_ext.values()))

        # A-05 · nivel EXACTO: contenido cambiado → DECISIÓN de reciclado
        # (ADR-01). Solo decide: el cierre ocurre bajo el lock de vacantes.
        fresh_exts, recycled = await self._recycle_guard(
            session, source_name, slot_by_ext, inc_by_slot, by_ext, prep_by_ext
        )
        recycled_incs = [inc_by_slot[s][0] for s in recycled]
        recycled_vacs = [inc_by_slot[s][1] for s in recycled]
        for slot_id in recycled:
            del inc_by_slot[slot_id]  # se cierra bajo el lock y reabre abajo
        orphans = [s for s in slot_by_ext.values() if s not in inc_by_slot]

        # Candidatas de attach (nivel 2) SIN lock: pre-selección barata.
        ext_by_slot = {v: k for k, v in slot_by_ext.items()}
        attach_by_urln = await self._attach_candidates(
            session, source_id,
            sorted({prep_by_ext[ext_by_slot[s]][2] for s in orphans if s not in set(recycled)}),
        )

        # PROTOCOLO DE LOCKS POR VACANTE (rev. A-05 #1/#2): TODAS las vacantes
        # que este run va a tocar (recicladas ∪ candidatas de attach) se
        # bloquean en UN solo FOR UPDATE ordenado por id (orden global entre
        # runs → sin deadlock) y se REVALIDA su vigencia bajo el lock: un
        # archive/merge que commiteó tras la pre-selección la expulsa del
        # attach (EPQ re-evalúa el WHERE) → vacante nueva. Cierre de
        # incarnaciones y reparación del primary ocurren BAJO estos locks:
        # cualquier escritor de vacancies (archive/merge/attach/reciclado)
        # debe usar este mismo protocolo.
        still_active = await self._lock_vacancies(
            session, list(set(recycled_vacs) | set(attach_by_urln.values()))
        )
        attach_by_urln = {
            u: v for u, v in attach_by_urln.items() if v in still_active
        }
        await self._close_incarnations(session, recycled_incs)

        # A-05 · nivel 2 (cross-source por url_normalized) + creación. Los
        # slots RECICLADOS jamás se attachean (ADR-01: reciclado = vacante
        # NUEVA; en particular no vuelven a la vacante que acaban de dejar).
        inc_by_slot, created_incs, evidence, pairs = await self._resolve_new_incarnations(
            session, source_name, slot_by_ext, inc_by_slot, by_ext,
            prep_by_ext, attach_by_urln, no_attach=set(recycled),
        )

        # Auditoría A-05 #1 + rev. #2: la vacante COMPARTIDA que pierde su
        # primary por el cierre y conserva activas reasigna el puntero
        # determinista BAJO el lock de vacante ya tomado (la re-lectura ve
        # solo estado commiteado o propio: dos fuentes reciclando a la vez se
        # serializan aquí). Sin activas se deja (mono-fuente: archivado ADR-07).
        await self._repair_primary_pointers(session, recycled_vacs)

        # A-05 · alias external_id↔URL (gana external_id, ADR-01) y drift de
        # URL entre fuentes (cierra la carrera de creación concurrente: nunca
        # attach tardío automático — candidato pending, resolución Fase B).
        evidence += await self._url_alias_evidence(
            session, source_id, slot_by_ext, stored_urln, prep_by_ext, inc_by_slot
        )
        pairs += await self._url_drift_pairs(session, slot_by_ext)
        await self._write_link_evidence(session, evidence)
        await self._write_dedup_candidates(session, pairs)

        await self._refresh_and_revise(
            session, slot_by_ext, inc_by_slot, by_ext, prep_by_ext,
            fresh_exts, created_incs,
        )

    async def _ensure_slots(
        self, session, source_id, by_ext: dict[str, RawListing], prep_by_ext
    ) -> tuple[dict[str, uuid.UUID], dict[str, str]]:
        """Upsert de slots (source_listings) por lote. Devuelve
        (external_id→id, external_id→url_normalized ALMACENADA) — la almacenada
        detecta el conflicto external_id↔URL (A-05)."""
        exts = list(by_ext)
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, external_id, url_normalized FROM source_listings "
                    "WHERE source_id = :src AND external_id = ANY(:exts)"
                ),
                {"src": source_id, "exts": exts},
            )
        ).all()
        slot_by_ext = {r.external_id: r.id for r in rows}
        stored_urln = {r.external_id: r.url_normalized for r in rows}

        # ORDEN GLOBAL DETERMINISTA también aquí (el test de carrera cazó el
        # deadlock que faltaba): dos runs con lotes en orden inverso insertan
        # los slots en el MISMO orden.
        missing = sorted(e for e in exts if e not in slot_by_ext)
        if missing:
            # ON CONFLICT DO NOTHING cubre la carrera y la colisión por
            # url_normalizada (dos external_id con la misma URL).
            await session.execute(
                sa.text(
                    "INSERT INTO source_listings (id, source_id, external_id, url_normalized) "
                    "VALUES (:id, :src, :ext, :urln) ON CONFLICT DO NOTHING"
                ),
                [
                    {
                        # URL normalizada PREcalculada en _preprocess (rev. 2ª
                        # #2): aquí ya no puede fallar ni se recalcula.
                        "id": uuid.uuid4(), "src": source_id, "ext": ext,
                        "urln": prep_by_ext[ext][2],
                    }
                    for ext in missing
                ],
            )
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT id, external_id, url_normalized FROM source_listings "
                        "WHERE source_id = :src AND external_id = ANY(:exts)"
                    ),
                    {"src": source_id, "exts": missing},
                )
            ).all()
            slot_by_ext.update({r.external_id: r.id for r in rows})
            stored_urln.update({r.external_id: r.url_normalized for r in rows})
            for ext in missing:
                if ext not in slot_by_ext:
                    # Colisión de url_normalized con OTRO slot de la MISMA
                    # fuente: la UNIQUE impide otro slot con esa URL — se salta
                    # con log (el alias cross-slot lo registra _url_alias_evidence
                    # cuando el external_id SÍ existe; ADR-01: gana external_id).
                    logger.warning(
                        "sink: listing %r saltado (URL normalizada ya pertenece "
                        "a otro slot)", ext,
                    )
        return slot_by_ext, stored_urln

    async def _active_incarnations(
        self, session, slot_ids
    ) -> dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]]:
        """slot → (incarnación ACTIVA, su vacante)."""
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, source_listing_id, vacancy_id "
                    "FROM source_listing_incarnations "
                    "WHERE source_listing_id = ANY(:ids) AND ended_at IS NULL"
                ),
                {"ids": slot_ids},
            )
        ).all()
        return {r.source_listing_id: (r.id, r.vacancy_id) for r in rows}

    async def _recycle_guard(
        self, session, source_name, slot_by_ext, inc_by_slot, by_ext, prep_by_ext
    ) -> tuple[set[str], list[uuid.UUID]]:
        """Nivel EXACTO con guard de reciclado (ADR-01/A-05).

        Para incarnaciones ACTIVAS cuyo contenido entrante es NUEVO, compara la
        identidad determinista (tokens de EMPRESA, PF.5) del raw vigente contra
        el entrante: empresa distinta → RECICLADO. SOLO DECIDE (rev. #2): el
        cierre lo hace _close_incarnations BAJO el lock de vacante. El
        coseno < SIM_RECYCLE queda diferido a Fase B con los embeddings: sin
        identidad completa NUNCA se recicla (conservador, no corromper).

        Devuelve (exts con contenido nuevo en incarnación CONSERVADA, slots
        reciclados)."""
        pairs = []  # (ext, inc_id, chash) de slots con incarnación activa
        for ext, slot_id in slot_by_ext.items():
            info = inc_by_slot.get(slot_id)
            if info is not None:
                pairs.append((ext, str(info[0]), prep_by_ext[ext][1]))
        if not pairs:
            return set(), []
        # PRE-FILTRO de pares exactos (A-04 #3): contenido ya visto en ESA
        # incarnación = ni revisión ni guard.
        existing = {
            (str(r.incarnation_id), r.content_hash)
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT r.incarnation_id, r.content_hash "
                        "FROM source_listing_revisions r "
                        "JOIN unnest(CAST(:ids AS uuid[]), CAST(:hs AS text[])) "
                        "  AS t(iid, chash) "
                        "ON r.incarnation_id = t.iid AND r.content_hash = t.chash"
                    ),
                    {"ids": [p[1] for p in pairs], "hs": [p[2] for p in pairs]},
                )
            ).all()
        }
        fresh = [(ext, iid) for ext, iid, chash in pairs if (iid, chash) not in existing]
        if not fresh:
            return set(), []
        # Raw VIGENTE solo de las incarnaciones con contenido nuevo. Sin
        # revisión previa = primer contenido de la incarnación: sin guard.
        latest_raw = {
            str(r.incarnation_id): r.raw
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT DISTINCT ON (incarnation_id) incarnation_id, raw "
                        "FROM source_listing_revisions "
                        "WHERE incarnation_id = ANY(:ids) "
                        "ORDER BY incarnation_id, fetched_at DESC, id"
                    ),
                    {"ids": sorted({iid for _, iid in fresh})},
                )
            ).all()
        }
        fresh_exts: set[str] = set()
        recycled: list[uuid.UUID] = []
        for ext, iid in fresh:
            old_raw = latest_raw.get(iid)
            if old_raw is not None and identity.should_recycle(
                identity.extract_identity(source_name, old_raw),
                identity.extract_identity(source_name, by_ext[ext].payload),
            ):
                recycled.append(slot_by_ext[ext])
                logger.info(
                    "sink: slot %r RECICLADO (empresa distinta) — se cierra la "
                    "incarnación y se abre otra con vacante nueva", ext,
                )
            else:
                fresh_exts.add(ext)
        return fresh_exts, recycled

    async def _attach_candidates(self, session, source_id, urls) -> dict:
        """Pre-selección de attach (nivel 2) SIN lock: vacantes ACTIVAS de
        otras fuentes con la misma URL normalizada (índice core0003). Empate
        multi-fuente → min(vacancy_id) determinista. La VIGENCIA se revalida
        después bajo el lock de vacante (rev. #1)."""
        if not urls:
            return {}
        attach_by_urln: dict[str, uuid.UUID] = {}
        for r in (
            await session.execute(
                sa.text(
                    "SELECT sl.url_normalized AS urln, i.vacancy_id "
                    "FROM source_listings sl "
                    "JOIN source_listing_incarnations i "
                    "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
                    "JOIN vacancies v ON v.id = i.vacancy_id "
                    "  AND v.archived_at IS NULL AND v.merged_into IS NULL "
                    "WHERE sl.url_normalized = ANY(:urls) AND sl.source_id != :src "
                    "ORDER BY sl.url_normalized, i.vacancy_id"
                ),
                {"urls": urls, "src": source_id},
            )
        ).all():
            attach_by_urln.setdefault(r.urln, r.vacancy_id)
        return attach_by_urln

    async def _lock_vacancies(self, session, vacancy_ids) -> set:
        """Bloquea las vacantes en orden GLOBAL determinista (ORDER BY id,
        FOR UPDATE) y devuelve las que siguen VIGENTES bajo el lock: si un
        archive/merge concurrente commiteó primero, EPQ re-evalúa el WHERE
        sobre la versión nueva y la fila cae del resultado (rev. #1). Las no
        vigentes no se bloquean — tampoco hace falta: ya no se attachea."""
        if not vacancy_ids:
            return set()
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id FROM vacancies "
                    "WHERE id = ANY(:ids) "
                    "AND archived_at IS NULL AND merged_into IS NULL "
                    "ORDER BY id FOR UPDATE"
                ),
                {"ids": sorted(set(vacancy_ids), key=str)},
            )
        ).all()
        return {r.id for r in rows}

    async def _close_incarnations(self, session, incarnation_ids) -> None:
        """Cierra las incarnaciones recicladas — SIEMPRE después de
        _lock_vacancies (rev. #2: dos fuentes reciclando la misma vacante
        compartida se serializan en el lock; la reparación posterior ya ve
        estado commiteado, nunca un cierre sin confirmar)."""
        if not incarnation_ids:
            return
        ids = sorted(str(i) for i in incarnation_ids)  # orden determinista
        await session.execute(
            sa.text(
                "UPDATE source_listing_incarnations SET ended_at = now() "
                "WHERE id = :iid AND ended_at IS NULL"
            ),
            [{"iid": iid} for iid in ids],
        )

    async def _resolve_new_incarnations(
        self, session, source_name, slot_by_ext, inc_by_slot,
        by_ext, prep_by_ext, attach_by_urln, no_attach=frozenset(),
    ):
        """Incarnaciones para slots SIN activa (nuevos, reabiertos, reciclados).

        Nivel 2 (ADR-01): `attach_by_urln` llega YA revalidado bajo el lock de
        vacante (rev. #1) — la nueva incarnación se attachea a esa vacante
        (+`link_evidence` url_normalized); el attach solo ocurre AL CREAR,
        jamás re-attach automático de existentes. Si no, vacante fresca.
        Duplicados difusos DENTRO del lote → `dedup_candidates`.

        Devuelve (inc_by_slot final, incs nuevas de este run, evidencia, pares)."""
        ext_by_slot = {v: k for k, v in slot_by_ext.items()}
        orphans = [s for s in slot_by_ext.values() if s not in inc_by_slot]
        if not orphans:
            return inc_by_slot, set(), [], []
        seq_rows = (
            await session.execute(
                sa.text(
                    "SELECT source_listing_id, COALESCE(MAX(seq), 0) AS max_seq "
                    "FROM source_listing_incarnations "
                    "WHERE source_listing_id = ANY(:ids) GROUP BY source_listing_id"
                ),
                {"ids": orphans},
            )
        ).all()
        max_seq = {r.source_listing_id: r.max_seq for r in seq_rows}

        new_rows = []
        for slot_id in orphans:
            ext = ext_by_slot[slot_id]
            listing = by_ext[ext]
            attached_vac = (
                None if slot_id in no_attach
                else attach_by_urln.get(prep_by_ext[ext][2])
            )
            new_rows.append(
                {
                    "iid": uuid.uuid4(),
                    "vid": attached_vac if attached_vac is not None else uuid.uuid4(),
                    "created": attached_vac is None,
                    "slot": str(slot_id),
                    "seq": max_seq.get(slot_id, 0) + 1,
                    "url": listing.url, "aurl": listing.apply_url,
                }
            )
        # ORDEN GLOBAL DETERMINISTA (auditoría A-04 #2).
        new_rows.sort(key=lambda r: r["slot"])
        creating = [r for r in new_rows if r["created"]]
        if creating:
            # Vacante fresca solo para lo NO attacheado.
            await session.execute(
                sa.text("INSERT INTO vacancies (id) VALUES (:vid)"),
                [{"vid": r["vid"]} for r in creating],
            )
        # ON CONFLICT contra el índice parcial (una activa por slot): si otro
        # run ganó la carrera, DO NOTHING → re-select del ganador + limpieza.
        await session.execute(
            sa.text(
                "INSERT INTO source_listing_incarnations "
                "(id, source_listing_id, vacancy_id, seq, url, apply_url) "
                "VALUES (:iid, :slot, :vid, :seq, :url, :aurl) "
                "ON CONFLICT (source_listing_id) WHERE ended_at IS NULL DO NOTHING"
            ),
            [
                {k: r[k] for k in ("iid", "slot", "vid", "seq", "url", "aurl")}
                for r in new_rows
            ],
        )
        winners = {
            r.source_listing_id: (r.id, r.vacancy_id)
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT id, source_listing_id, vacancy_id "
                        "FROM source_listing_incarnations "
                        "WHERE source_listing_id = ANY(:ids) AND ended_at IS NULL"
                    ),
                    {"ids": orphans},
                )
            ).all()
        }
        ours = [
            r for r in new_rows
            if winners.get(uuid.UUID(r["slot"]), (None, None))[0] == r["iid"]
        ]
        losers = [r for r in new_rows if r not in ours]
        lost_vids = [r["vid"] for r in losers if r["created"]]
        if lost_vids:
            # Perdimos la carrera: fuera SOLO nuestras vacantes creadas (las
            # attacheadas son de otros y no se tocan).
            await session.execute(
                sa.text("DELETE FROM vacancies WHERE id = ANY(:ids)"),
                {"ids": lost_vids},
            )
        pointer_rows = [
            {"iid": r["iid"], "vid": r["vid"]} for r in ours if r["created"]
        ]
        if pointer_rows:
            # Puntero primario SOLO en vacantes frescas: una vacante attacheada
            # conserva el primary de su fuente original (ADR-01).
            await session.execute(
                sa.text(
                    "UPDATE vacancies SET primary_incarnation_id = :iid WHERE id = :vid"
                ),
                pointer_rows,
            )
        evidence = [
            {
                "slot": r["slot"], "vac": r["vid"],
                "method": "url_normalized", "conf": identity.CONF_URL_ATTACH,
            }
            for r in ours if not r["created"]
        ]
        # Medio intra-lote: misma identidad difusa (PF.5) en DOS+ vacantes
        # CREADAS en este lote → candidatos (el primero contra el resto).
        pairs = []
        by_key: dict[str, list] = {}
        for r in ours:
            if not r["created"]:
                continue
            listing = by_ext[ext_by_slot[uuid.UUID(r["slot"])]]
            key = identity.fuzzy_key(
                *identity.extract_identity(source_name, listing.payload)
            )
            if key:
                by_key.setdefault(key, []).append(r["vid"])
        for vids in by_key.values():
            pairs += [
                {"a": vids[0], "b": other, "sim": identity.SIM_FUZZY_BATCH}
                for other in vids[1:]
            ]
        inc_by_slot = dict(inc_by_slot)
        inc_by_slot.update(winners)
        new_incs = {info[0] for info in winners.values()}
        return inc_by_slot, new_incs, evidence, pairs

    async def _repair_primary_pointers(self, session, vacancy_ids) -> None:
        """Reasigna el puntero primario de vacantes cuyo primary quedó CERRADO
        pero que conservan incarnaciones ACTIVAS (vacante compartida
        cross-source; auditoría A-05 #1). Elección DETERMINISTA: la activa más
        antigua (first_seen_at, id) — sin semántica, Fase A. La FK compuesta
        exige misma vacante: el pick sale de la propia vacante. Sin activas no
        se toca (mono-fuente: el archivado ADR-07 recoge la vacante muerta)."""
        if not vacancy_ids:
            return
        await session.execute(
            sa.text(
                "UPDATE vacancies v SET primary_incarnation_id = pick.iid "
                "FROM (SELECT DISTINCT ON (vacancy_id) vacancy_id, id AS iid "
                "      FROM source_listing_incarnations "
                "      WHERE vacancy_id = ANY(:vacs) AND ended_at IS NULL "
                "      ORDER BY vacancy_id, first_seen_at, id) pick "
                "WHERE v.id = pick.vacancy_id "
                "AND NOT EXISTS (SELECT 1 FROM source_listing_incarnations cur "
                "                WHERE cur.id = v.primary_incarnation_id "
                "                AND cur.ended_at IS NULL)"
            ),
            {"vacs": sorted(set(vacancy_ids), key=str)},
        )

    async def _url_alias_evidence(
        self, session, source_id, slot_by_ext, stored_urln, prep_by_ext, inc_by_slot
    ) -> list[dict]:
        """Conflicto external_id↔URL (ADR-01): el listing casó por external_id
        con el slot X pero su URL normalizada pertenece a OTRO slot de la misma
        fuente → GANA external_id (se procesa como X); la URL se registra como
        alias (evidencia X → vacante vigente del dueño de esa URL)."""
        drifted = {
            ext: prep_by_ext[ext][2]
            for ext, urln in stored_urln.items()
            if prep_by_ext[ext][2] != urln
        }
        if not drifted:
            return []
        vac_by_urln = {
            r.urln: r.vacancy_id
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT sl.url_normalized AS urln, i.vacancy_id "
                        "FROM source_listings sl "
                        "JOIN source_listing_incarnations i "
                        "  ON i.source_listing_id = sl.id AND i.ended_at IS NULL "
                        "WHERE sl.source_id = :src AND sl.url_normalized = ANY(:urls)"
                    ),
                    {"src": source_id, "urls": sorted(set(drifted.values()))},
                )
            ).all()
        }
        out = []
        for ext, urln in drifted.items():
            vac = vac_by_urln.get(urln)
            own = inc_by_slot.get(slot_by_ext[ext])
            if vac is not None and (own is None or own[1] != vac):
                out.append(
                    {
                        "slot": str(slot_by_ext[ext]), "vac": vac,
                        "method": "url_alias", "conf": identity.CONF_URL_ALIAS,
                    }
                )
        return out

    async def _url_drift_pairs(self, session, slot_by_ext) -> list[dict]:
        """Misma url_normalizada VIGENTE en dos fuentes con vacantes DISTINTAS
        (p.ej. creación concurrente en ambas: el attach solo ocurre al crear,
        jamás re-attach automático) → candidato medio idempotente (par único)."""
        rows = (
            await session.execute(
                sa.text(
                    "SELECT mi.vacancy_id AS a, oi.vacancy_id AS b "
                    "FROM source_listings me "
                    "JOIN source_listing_incarnations mi "
                    "  ON mi.source_listing_id = me.id AND mi.ended_at IS NULL "
                    "JOIN source_listings o "
                    "  ON o.url_normalized = me.url_normalized "
                    " AND o.source_id != me.source_id "
                    "JOIN source_listing_incarnations oi "
                    "  ON oi.source_listing_id = o.id AND oi.ended_at IS NULL "
                    "WHERE me.id = ANY(:ids) AND mi.vacancy_id != oi.vacancy_id"
                ),
                {"ids": list(slot_by_ext.values())},
            )
        ).all()
        return [{"a": r.a, "b": r.b, "sim": identity.SIM_URL_DRIFT} for r in rows]

    async def _write_link_evidence(self, session, rows) -> None:
        """Evidencia SIN spam: la emisión total re-detecta el alias en CADA
        cosecha — pre-filtro por (slot, vacante, método), solo inserta lo nuevo."""
        if not rows:
            return
        uniq = {(r["slot"], str(r["vac"]), r["method"]): r for r in rows}
        rows = list(uniq.values())
        existing = {
            (str(r.source_listing_id), str(r.vacancy_id), r.method)
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT le.source_listing_id, le.vacancy_id, le.method "
                        "FROM link_evidence le "
                        "JOIN unnest(CAST(:slots AS uuid[]), CAST(:vacs AS uuid[]), "
                        "            CAST(:methods AS text[])) AS t(s, v, m) "
                        "ON le.source_listing_id = t.s AND le.vacancy_id = t.v "
                        "AND le.method = t.m"
                    ),
                    {
                        "slots": [r["slot"] for r in rows],
                        "vacs": [str(r["vac"]) for r in rows],
                        "methods": [r["method"] for r in rows],
                    },
                )
            ).all()
        }
        fresh = [
            r for r in rows if (r["slot"], str(r["vac"]), r["method"]) not in existing
        ]
        if fresh:
            fresh.sort(key=lambda r: (r["slot"], str(r["vac"]), r["method"]))
            await session.execute(
                sa.text(
                    "INSERT INTO link_evidence "
                    "(id, source_listing_id, vacancy_id, method, confidence) "
                    "VALUES (:id, :slot, :vac, :method, :conf)"
                ),
                [{"id": uuid.uuid4(), **r} for r in fresh],
            )

    async def _write_dedup_candidates(self, session, pairs) -> None:
        """Candidatos medio (state=pending; la RESOLUCIÓN es Fase B — aquí
        jamás se funde). Par canónico único (LEAST/GREATEST) + DO NOTHING:
        idempotente ante re-cosechas y carreras."""
        if not pairs:
            return
        # La similitud NO puede depender del orden de llegada (rev. #3): en el
        # lote gana el MÁXIMO, y en BD GREATEST — solo mientras el candidato
        # siga pending (jamás se toca estado ni resolución).
        canon: dict[tuple[str, str], dict] = {}
        for p in pairs:
            a, b = str(p["a"]), str(p["b"])
            if a == b:
                continue
            key = (min(a, b), max(a, b))
            if key not in canon or p["sim"] > canon[key]["sim"]:
                canon[key] = p
        rows = [
            {"id": uuid.uuid4(), "a": p["a"], "b": p["b"], "sim": p["sim"]}
            for _, p in sorted(canon.items())
        ]
        if rows:
            await session.execute(
                sa.text(
                    "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity) "
                    "VALUES (:id, :a, :b, :sim) "
                    "ON CONFLICT (LEAST(vacancy_a, vacancy_b), "
                    "GREATEST(vacancy_a, vacancy_b)) DO UPDATE "
                    "SET similarity = GREATEST(dedup_candidates.similarity, "
                    "EXCLUDED.similarity) "
                    "WHERE dedup_candidates.state = 'pending'"
                ),
                rows,
            )

    async def _refresh_and_revise(
        self, session, slot_by_ext, inc_by_slot, by_ext, prep_by_ext,
        fresh_exts, new_incs,
    ) -> None:
        """`last_seen_at` (y url/apply) en CADA cosecha + revisión si procede.

        El PRE-FILTRO de contenido ya corrió en el guard (A-04 #3): aquí una
        revisión procede si (a) el contenido es nuevo en incarnación CONSERVADA
        (`fresh_exts`) o (b) la incarnación es NUEVA de este run (`new_incs` —
        incluye las de runs concurrentes ganadores: su contenido puede diferir
        del nuestro y el ON CONFLICT deduplica)."""
        refresh, candidates = [], []
        for ext, slot_id in slot_by_ext.items():
            info = inc_by_slot.get(slot_id)
            if info is None:
                continue
            inc_id = info[0]
            listing = by_ext[ext]
            refresh.append(
                {"iid": str(inc_id), "url": listing.url, "aurl": listing.apply_url}
            )
            # UNA sola serialización Y un solo hash: calculados en la
            # cuarentena de frontera y reutilizados aquí (#3/#5).
            canon, chash, _ = prep_by_ext[ext]
            if ext in fresh_exts or inc_id in new_incs:
                candidates.append(
                    {"id": uuid.uuid4(), "iid": str(inc_id), "chash": chash, "raw": canon}
                )
        if refresh:
            # Orden determinista → mismos locks en el mismo orden entre runs (#2).
            refresh.sort(key=lambda r: r["iid"])
            await session.execute(
                sa.text(
                    "UPDATE source_listing_incarnations "
                    "SET last_seen_at = now(), url = :url, apply_url = :aurl "
                    "WHERE id = :iid"
                ),
                refresh,
            )
        if candidates:
            candidates.sort(key=lambda r: r["iid"])  # orden determinista (#2)
            # ON CONFLICT se mantiene: cierra la carrera entre el pre-filtro y
            # el INSERT (el pre-filtro es optimización, no la corrección).
            await session.execute(
                sa.text(
                    "INSERT INTO source_listing_revisions "
                    "(id, incarnation_id, content_hash, raw) "
                    "VALUES (:id, :iid, :chash, CAST(:raw AS jsonb)) "
                    "ON CONFLICT (incarnation_id, content_hash) DO NOTHING"
                ),
                candidates,
            )
