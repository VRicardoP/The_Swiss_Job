"""JobRepository — database operations for job upsert and dedup management."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import case, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job

logger = logging.getLogger(__name__)

# Campos de contenido que un provider re-suministra y que afectan a
# visualización/matching. title/company/url NO entran: están fijados por el hash
# (hash = MD5(title+company+url)), así que no cambian en un conflicto.
_CONTENT_FIELDS: tuple[str, ...] = (
    "description",
    "description_snippet",
    "location",
    "canton",
    "salary_min_chf",
    "salary_max_chf",
    "salary_original",
    "salary_currency",
    "salary_period",
    "language",
    "seniority",
    "contract_type",
    "remote",
    "tags",
    "logo",
    "employment_type",
    "category",
)

# Columnas que NO se refrescan desde el provider en un conflicto: identidad,
# marca de primera vista y estado gestionado por el sistema (dedup, embedding,
# timestamps de actividad). Se tratan aparte o se conservan.
_SYSTEM_MANAGED: frozenset[str] = frozenset(
    {
        "hash",
        "source",
        "first_seen_at",
        "last_seen_at",
        "is_active",
        "embedding",
        "content_hash",
        "duplicate_of",
        "url_last_check",
    }
)


def _content_hash(values: dict) -> str:
    """MD5 estable de los campos de contenido, para detectar cambios (PF.1)."""
    payload = {k: values.get(k) for k in _CONTENT_FIELDS}
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.md5(raw.encode()).hexdigest()


class JobRepository:
    """Encapsulates all DB operations for jobs."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_job(self, job_dict: dict) -> bool:
        """Insert a new job or refresh an existing one (INSERT ... ON CONFLICT).

        En un conflicto (hash ya visto) refresca el CONTENIDO mutable — una oferta
        re-vista puede haber cambiado descripción, salario, tags... — además de
        last_seen_at/is_active, y actualiza content_hash. Si el contenido cambió,
        invalida el embedding (lo pone a NULL) para que el pipeline lo re-embeba y
        re-matchee con datos frescos, manteniendo contenido y embedding coherentes
        (PF.1). Devuelve True si la oferta es nueva, False si ya existía.
        """
        # Filtrar a columnas que existen en el modelo Job.
        valid_columns = {c.key for c in Job.__table__.columns}
        values = {k: v for k, v in job_dict.items() if k in valid_columns}
        values["content_hash"] = _content_hash(values)

        # Determinar si es nueva antes del upsert (para el valor de retorno).
        existing = await self.db.execute(
            select(Job.hash).where(Job.hash == values["hash"])
        )
        is_new = existing.scalar_one_or_none() is None

        stmt = pg_insert(Job).values(**values)
        # Refrescar el contenido mutable desde el provider; conservar identidad y
        # estado gestionado por el sistema.
        set_ = {
            col: getattr(stmt.excluded, col)
            for col in values
            if col not in _SYSTEM_MANAGED
        }
        set_["last_seen_at"] = datetime.now(timezone.utc)
        # Reactivar SOLO si NO es duplicado: una oferta archivada que reaparece se
        # reactiva; un duplicado re-visto sigue inactivo (no vuelve a los feeds).
        set_["is_active"] = case((Job.duplicate_of.isnot(None), False), else_=True)
        set_["content_hash"] = stmt.excluded.content_hash
        # El embedding se construye de title+company+description+tags
        # (JobMatcher.build_job_text). En un conflicto, title/company están fijados
        # por el hash, así que solo pueden cambiar description/tags: invalidamos el
        # embedding (NULL → re-embed + re-match) SOLO si esos cambian. Es
        # independiente de content_hash (que versiona MÁS campos): así ni un cambio
        # de logo/salario re-embebe texto idéntico (eficiencia), ni una fila anterior
        # a la columna (content_hash NULL) conserva un embedding obsoleto (correctitud).
        set_["embedding"] = case(
            (
                or_(
                    Job.description.is_distinct_from(stmt.excluded.description),
                    Job.tags.is_distinct_from(stmt.excluded.tags),
                ),
                null(),
            ),
            else_=Job.embedding,
        )

        await self.db.execute(
            stmt.on_conflict_do_update(index_elements=["hash"], set_=set_)
        )
        return is_new

    async def mark_duplicate(self, job_hash: str, canonical_hash: str) -> None:
        """Mark a job as a duplicate of another (deactivate it)."""
        await self.db.execute(
            update(Job)
            .where(Job.hash == job_hash)
            .values(duplicate_of=canonical_hash, is_active=False)
        )

    async def get_active_count(self) -> int:
        """Count active, non-duplicate jobs."""
        result = await self.db.execute(
            select(func.count())
            .select_from(Job)
            .where(Job.is_active.is_(True), Job.duplicate_of.is_(None))
        )
        return result.scalar_one()
