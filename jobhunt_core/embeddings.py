"""Embeddings de ofertas por text_hash (A-06, ADR-02).

- `offer_embeddings(text_hash, model_id, vector(384))` PARTICIONADA por
  model_id: registrar un modelo CREA su partición (regla operativa core0002);
  cada partición hereda su propio HNSW del índice padre.
- Clavado en `text_hash`: dos revisiones con el mismo texto comparten vector;
  cambiar salario NO re-embebe (el text_hash no incluye salario).
- Escritura con CONCURRENCIA OPTIMISTA: `ON CONFLICT (text_hash, model_id)
  DO NOTHING` — dos workers embebiendo el mismo texto no chocan.
- Backend de encoding INYECTABLE (costura): el real es sentence-transformers
  (import perezoso — la imagen sin ML sigue arrancando; los tests usan un
  backend determinista sin modelo).
"""

import logging
import re
import threading
import uuid

import sqlalchemy as sa

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

# Dimensión de la columna vector en Fase A (ADR-02): otra dimensión = nuevo
# ciclo expand/contract, JAMÁS reutilizar la columna.
EMBED_DIM = 384

# version = commit SHA de HF (rev. A-06 2ª #3): una ref MÓVIL (main/tag)
# resolvería a pesos distintos tras un reinicio del worker — espacios
# vectoriales diferentes bajo el mismo model_id.
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")


class SentenceTransformerBackend:
    """Backend real POR MODELO (rev. A-06 #3): carga `name` en la revisión
    INMUTABLE `version` (tag/commit de HF) — `model_id` identifica al encoder
    REAL, nunca un backend global compartido. Carga perezosa y thread-safe:
    el import de ML solo ocurre si de verdad se embebe."""

    def __init__(self, name: str, version: str):
        self._name = name
        self._version = version
        self._model = None
        self._lock = threading.Lock()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            batch_size=settings.CORE_EMBEDDING_BATCH_SIZE,
        )
        return [v.tolist() for v in vectors]

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from sentence_transformers import SentenceTransformer

                    self._model = SentenceTransformer(
                        self._name, revision=self._version, device="cpu"
                    )
        return self._model


# Fábrica de backends por (name, version) — INYECTABLE (tests) y cacheada:
# cada modelo registrado resuelve SU encoder. A-07 (perfiles) debe reutilizar
# exactamente esta resolución.
_backend_factory = None
_backends: dict[tuple[str, str], object] = {}


def get_backend(name: str, version: str):
    key = (name, version)
    if key not in _backends:
        factory = _backend_factory or SentenceTransformerBackend
        _backends[key] = factory(name, version)
    return _backends[key]


def set_backend_factory(factory) -> None:
    """Sustituye la fábrica (tests) y vacía la caché; None restaura la real."""
    global _backend_factory
    _backend_factory = factory
    _backends.clear()


async def register_model(
    session, name: str, version: str, dim: int = EMBED_DIM, active: bool = True
) -> uuid.UUID:
    """Alta idempotente del modelo + SU partición de offer_embeddings.

    dim != 384 se rechaza explícito: la columna es vector(384) — otra
    dimensión exige expand/contract (ADR-02), nunca una partición aquí."""
    if dim != EMBED_DIM:
        raise ValueError(
            f"dim={dim}: la columna de Fase A es vector({EMBED_DIM}); otra "
            "dimensión requiere expand/contract (ADR-02)"
        )
    if not _SHA40_RE.fullmatch(version):
        raise ValueError(
            f"version {version!r} debe ser un commit SHA INMUTABLE (40 hex) del "
            "modelo: una ref móvil (main/tag) resolvería a pesos distintos bajo "
            "el mismo model_id (rev. A-06 2ª #3)"
        )
    await session.execute(
        sa.text(
            "INSERT INTO embedding_models (id, name, version, dim, active) "
            "VALUES (:id, :name, :version, :dim, :active) "
            "ON CONFLICT (name, version) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(), "name": name, "version": version,
            "dim": dim, "active": active,
        },
    )
    # Validar la fila REAL bajo lock (rev. A-06 #4): tras el DO NOTHING la
    # existente puede tener otra dimensión — jamás dar éxito sobre ella.
    row = (
        await session.execute(
            sa.text(
                "SELECT id, dim, active FROM embedding_models "
                "WHERE name = :name AND version = :version FOR UPDATE"
            ),
            {"name": name, "version": version},
        )
    ).one()
    if row.dim != dim:
        raise ValueError(
            f"modelo {name}/{version} ya registrado con dim={row.dim} != {dim}: "
            "la dimensión de un modelo registrado es INMUTABLE (expand/contract)"
        )
    # `active` SÍ se actualiza: el registro es una declaración operativa
    # idempotente (activar/desactivar re-declarando).
    if row.active != active:
        await session.execute(
            sa.text("UPDATE embedding_models SET active = :a WHERE id = :id"),
            {"a": active, "id": row.id},
        )
    model_id = row.id
    # Partición del modelo (idempotente). El nombre deriva del id: estable.
    partition = f"offer_embeddings_{model_id.hex[:16]}"
    schema = settings.CORE_DB_SCHEMA
    await session.execute(
        sa.text(
            f"CREATE TABLE IF NOT EXISTS {schema}.{partition} "
            f"PARTITION OF {schema}.offer_embeddings "
            f"FOR VALUES IN ('{model_id}')"
        )
    )
    return model_id


async def active_models(session) -> list:
    return (
        await session.execute(
            sa.text("SELECT id, name, version, dim FROM embedding_models WHERE active")
        )
    ).all()


async def pending_offer_texts(session, model_id, limit: int = 200) -> list:
    """text_hash (con un content representativo) de las revisiones VIGENTES de
    vacantes activas que aún no tienen vector para este modelo. DISTINCT por
    text_hash: el mismo texto en N vacantes se embebe UNA vez."""
    return (
        await session.execute(
            sa.text(
                "SELECT DISTINCT ON (o.text_hash) o.text_hash, o.content "
                "FROM vacancies v "
                "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
                "LEFT JOIN offer_embeddings e "
                "  ON e.text_hash = o.text_hash AND e.model_id = :mid "
                "WHERE e.text_hash IS NULL "
                "AND v.archived_at IS NULL AND v.merged_into IS NULL "
                "ORDER BY o.text_hash "
                "LIMIT :lim"
            ),
            {"mid": model_id, "lim": limit},
        )
    ).all()


async def store_offer_embeddings(session, model_id, items: list[dict]) -> int:
    """items = [{'text_hash': ..., 'vector': [float x384]}]. OPTIMISTA:
    DO NOTHING sobre la PK (text_hash, model_id). Devuelve filas insertadas."""
    if not items:
        return 0
    for it in items:
        if len(it["vector"]) != EMBED_DIM:
            raise ValueError(
                f"vector de {len(it['vector'])} dims para text_hash "
                f"{it['text_hash'][:12]} (esperadas {EMBED_DIM})"
            )
    rows = sorted(
        (
            {
                "th": it["text_hash"], "mid": model_id,
                # pgvector en texto: '[f1,f2,...]' + CAST — sin dependencia del
                # codec binario de asyncpg para el tipo vector.
                "vec": "[" + ",".join(repr(float(x)) for x in it["vector"]) + "]",
            }
            for it in items
        ),
        key=lambda r: r["th"],
    )
    # Pre-filtro: no se envían vectores que acabarían en DO NOTHING (y el
    # rowcount de executemany en asyncpg no es fiable: -1). El ON CONFLICT se
    # mantiene para la carrera entre el SELECT y el INSERT; el conteo es
    # informativo (una carrera perdida puede sobrecontar en 1).
    existing = {
        r.text_hash
        for r in (
            await session.execute(
                sa.text(
                    "SELECT text_hash FROM offer_embeddings "
                    "WHERE model_id = :mid AND text_hash = ANY(:ths)"
                ),
                {"mid": model_id, "ths": [r["th"] for r in rows]},
            )
        ).all()
    }
    fresh = [r for r in rows if r["th"] not in existing]
    if not fresh:
        return 0
    await session.execute(
        sa.text(
            "INSERT INTO offer_embeddings (text_hash, model_id, vector) "
            "VALUES (:th, :mid, CAST(:vec AS vector)) "
            "ON CONFLICT (text_hash, model_id) DO NOTHING"
        ),
        fresh,
    )
    return len(fresh)
