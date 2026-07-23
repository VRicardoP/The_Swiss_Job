"""Sink real de ingesta (A-04): slot + incarnación + revisión RAW, por LOTES.

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

from jobhunt_core.harvest.types import RawListing

logger = logging.getLogger(__name__)

# Límites REALES del esquema (CONTRATOS §1 / core0002): validar en la frontera
# evita que UN listing sobredimensionado envenene el lote entero (rev. A-04 #2 —
# con la emisión total de A-03 el dato tóxico reaparecería en cada cosecha y
# bloquearía el scope indefinidamente).
MAX_EXTERNAL_ID_LEN = 200
MAX_URL_LEN = 1000


def _valid_listing(listing: RawListing, canon: str) -> bool:
    """Cumple los límites del esquema; si no, se registra y se aísla (nunca se
    truncan claves de identidad ni se aborta el lote válido). `canon` es la
    serialización canónica YA calculada (única — se reutiliza para el hash)."""
    reasons = []
    if len(listing.external_id) > MAX_EXTERNAL_ID_LEN:
        reasons.append(f"external_id > {MAX_EXTERNAL_ID_LEN}")
    if len(listing.url) > MAX_URL_LEN or len(normalize_url(listing.url)) > MAX_URL_LEN:
        reasons.append(f"url > {MAX_URL_LEN}")
    if listing.apply_url and len(listing.apply_url) > MAX_URL_LEN:
        reasons.append(f"apply_url > {MAX_URL_LEN}")
    # NUL: Postgres lo rechaza en text Y en jsonb. json.dumps SIEMPRE escapa
    # los controles, asi que en `canon` un NUL aparece como la secuencia \\u0000.
    if (
        "\x00" in listing.external_id
        or "\x00" in listing.url
        or "\x00" in (listing.apply_url or "")
        or "\\u0000" in canon
    ):
        reasons.append("NUL byte (rechazado por Postgres)")
    if reasons:
        logger.warning(
            "sink: listing %r EN CUARENTENA (%s) — no envenena el lote",
            listing.external_id[:80], "; ".join(reasons),
        )
        return False
    return True


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
    (default=str en ambos usos), y solo se serializa una vez (#3)."""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)


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
        source_id = (
            await session.execute(
                sa.text("SELECT source_id FROM harvest_scopes WHERE id = :sid"),
                {"sid": scope_id},
            )
        ).scalar_one()

        # SERIALIZACIÓN POR FUENTE (rev. A-04 #1): dos scopes de la MISMA fuente
        # tocan los mismos slots con DOS claves UNIQUE (external_id y
        # url_normalized) — ningún orden por una sola clave evita el deadlock
        # cruzado. El advisory lock transaccional (se libera en commit/rollback)
        # serializa por fuente y mantiene el paralelismo ENTRE fuentes.
        await session.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:src, 0))"),
            {"src": str(source_id)},
        )

        # Validación de FRONTERA contra los límites del esquema (rev. #2): los
        # inválidos se aíslan con log; el lote válido sigue adelante. La
        # serialización canónica Y el hash se calculan UNA vez aquí.
        valid: list[RawListing] = []
        canon_by_ext: dict[str, tuple[str, str]] = {}
        for listing in listings:
            try:
                canon = canonical_payload(listing.payload)
                # encode() detecta surrogates sueltos (json.loads puede
                # producirlos): pasarían dumps pero reventarían hash/BD.
                chash = hashlib.sha256(canon.encode()).hexdigest()
            except (TypeError, ValueError, UnicodeEncodeError) as exc:
                logger.warning(
                    "sink: listing %r EN CUARENTENA (payload no serializable: %s)",
                    listing.external_id[:80], exc,
                )
                continue
            if not _valid_listing(listing, canon):
                continue
            valid.append(listing)
            canon_by_ext[listing.external_id] = (canon, chash)
        if not valid:
            return
        # Dedup DENTRO del lote por external_id (la última aparición VÁLIDA gana).
        by_ext = {listing.external_id: listing for listing in valid}
        slot_by_ext = await self._ensure_slots(session, source_id, by_ext)
        inc_by_slot = await self._ensure_incarnations(session, slot_by_ext, by_ext)
        await self._refresh_and_revise(
            session, slot_by_ext, inc_by_slot, by_ext, canon_by_ext
        )

    async def _ensure_slots(
        self, session, source_id, by_ext: dict[str, RawListing]
    ) -> dict[str, uuid.UUID]:
        """Upsert de slots (source_listings) por lote. Devuelve external_id→id."""
        exts = list(by_ext)
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, external_id FROM source_listings "
                    "WHERE source_id = :src AND external_id = ANY(:exts)"
                ),
                {"src": source_id, "exts": exts},
            )
        ).all()
        slot_by_ext = {r.external_id: r.id for r in rows}

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
                        "id": uuid.uuid4(), "src": source_id, "ext": ext,
                        "urln": normalize_url(by_ext[ext].url),
                    }
                    for ext in missing
                ],
            )
            rows = (
                await session.execute(
                    sa.text(
                        "SELECT id, external_id FROM source_listings "
                        "WHERE source_id = :src AND external_id = ANY(:exts)"
                    ),
                    {"src": source_id, "exts": missing},
                )
            ).all()
            slot_by_ext.update({r.external_id: r.id for r in rows})
            for ext in missing:
                if ext not in slot_by_ext:
                    # Colisión de url_normalized con OTRO slot: validación de
                    # frontera — se salta con log (identidad ambigua = A-05).
                    logger.warning(
                        "sink: listing %r saltado (URL normalizada ya pertenece "
                        "a otro slot)", ext,
                    )
        return slot_by_ext

    async def _ensure_incarnations(
        self, session, slot_by_ext: dict[str, uuid.UUID], by_ext
    ) -> dict[uuid.UUID, uuid.UUID]:
        """Incarnación ACTIVA por slot; crea vacante+incarnación para slots sin
        ella (seq = max previa + 1: un slot reciclado ya cerrado reabre aquí)."""
        slot_ids = list(slot_by_ext.values())
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, source_listing_id FROM source_listing_incarnations "
                    "WHERE source_listing_id = ANY(:ids) AND ended_at IS NULL"
                ),
                {"ids": slot_ids},
            )
        ).all()
        inc_by_slot = {r.source_listing_id: r.id for r in rows}

        orphan_slots = [s for s in slot_ids if s not in inc_by_slot]
        if not orphan_slots:
            return inc_by_slot
        seq_rows = (
            await session.execute(
                sa.text(
                    "SELECT source_listing_id, COALESCE(MAX(seq), 0) AS max_seq "
                    "FROM source_listing_incarnations "
                    "WHERE source_listing_id = ANY(:ids) GROUP BY source_listing_id"
                ),
                {"ids": orphan_slots},
            )
        ).all()
        max_seq = {r.source_listing_id: r.max_seq for r in seq_rows}
        ext_by_slot = {v: k for k, v in slot_by_ext.items()}

        new_rows = []
        for slot_id in orphan_slots:
            listing = by_ext[ext_by_slot[slot_id]]
            new_rows.append(
                {
                    "iid": uuid.uuid4(), "vid": uuid.uuid4(), "slot": str(slot_id),
                    "seq": max_seq.get(slot_id, 0) + 1,
                    "url": listing.url, "aurl": listing.apply_url,
                }
            )
        # ORDEN GLOBAL DETERMINISTA (auditoría A-04 #2): dos runs concurrentes
        # de la misma fuente adquieren los locks en el mismo orden → sin deadlock.
        new_rows.sort(key=lambda r: r["slot"])
        # Vacante fresca por slot nuevo (identidad la refina A-05/merge).
        await session.execute(
            sa.text("INSERT INTO vacancies (id) VALUES (:vid)"),
            [{"vid": r["vid"]} for r in new_rows],
        )
        # ON CONFLICT contra el índice parcial (una incarnación ACTIVA por
        # slot): si otro run de la MISMA fuente ganó la carrera, DO NOTHING —
        # se re-selecciona al ganador y se limpia nuestra vacante huérfana.
        await session.execute(
            sa.text(
                "INSERT INTO source_listing_incarnations "
                "(id, source_listing_id, vacancy_id, seq, url, apply_url) "
                "VALUES (:iid, :slot, :vid, :seq, :url, :aurl) "
                "ON CONFLICT (source_listing_id) WHERE ended_at IS NULL DO NOTHING"
            ),
            new_rows,
        )
        winners = {
            r.source_listing_id: r.id
            for r in (
                await session.execute(
                    sa.text(
                        "SELECT id, source_listing_id FROM source_listing_incarnations "
                        "WHERE source_listing_id = ANY(:ids) AND ended_at IS NULL"
                    ),
                    {"ids": orphan_slots},
                )
            ).all()
        }
        ours = [r for r in new_rows if winners.get(uuid.UUID(r["slot"])) == r["iid"]]
        losers = [r for r in new_rows if r not in ours]
        if losers:
            # Perdimos la carrera en esos slots: fuera nuestras vacantes huérfanas.
            await session.execute(
                sa.text("DELETE FROM vacancies WHERE id = ANY(:ids)"),
                {"ids": [r["vid"] for r in losers]},
            )
        if ours:
            await session.execute(
                sa.text(
                    "UPDATE vacancies SET primary_incarnation_id = :iid WHERE id = :vid"
                ),
                [{"iid": r["iid"], "vid": r["vid"]} for r in ours],
            )
        inc_by_slot.update(winners)
        return inc_by_slot

    async def _refresh_and_revise(
        self, session, slot_by_ext, inc_by_slot, by_ext, canon_by_ext
    ) -> None:
        """`last_seen_at` (y url/apply) en CADA cosecha + revisión si cambió."""
        refresh, candidates = [], []
        for ext, slot_id in slot_by_ext.items():
            inc_id = inc_by_slot.get(slot_id)
            if inc_id is None:
                continue
            listing = by_ext[ext]
            refresh.append(
                {"iid": str(inc_id), "url": listing.url, "aurl": listing.apply_url}
            )
            # UNA sola serialización Y un solo hash: calculados en la
            # validación de frontera y reutilizados aquí (#3/#5).
            canon, chash = canon_by_ext[ext]
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
            # PRE-FILTRO (auditoría #3): en régimen estable casi todo el lote ya
            # existe — un SELECT indexado (uq_slrev_incarnation_hash) evita
            # enviar/parsear payloads enteros que acabarían en DO NOTHING.
            # Pares ALINEADOS via unnest (rev. A-04 #3): ANY(ids) AND ANY(hs)
            # sería el producto cruzado y degeneraría en O(n²) con historiales
            # que compartan hashes; el join por (iid, chash) consulta EXACTAMENTE
            # los pares solicitados.
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
                        {
                            "ids": [c["iid"] for c in candidates],
                            "hs": [c["chash"] for c in candidates],
                        },
                    )
                ).all()
            }
            fresh = [c for c in candidates if (c["iid"], c["chash"]) not in existing]
            if fresh:
                fresh.sort(key=lambda r: r["iid"])  # orden determinista (#2)
                # ON CONFLICT se mantiene: cierra la carrera entre el SELECT y
                # el INSERT (el pre-filtro es optimización, no la corrección).
                await session.execute(
                    sa.text(
                        "INSERT INTO source_listing_revisions "
                        "(id, incarnation_id, content_hash, raw) "
                        "VALUES (:id, :iid, :chash, CAST(:raw AS jsonb)) "
                        "ON CONFLICT (incarnation_id, content_hash) DO NOTHING"
                    ),
                    fresh,
                )
