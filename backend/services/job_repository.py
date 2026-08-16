"""JobRepository — database operations for job upsert and dedup management."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, null, or_, select, update
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
        # tags en la frontera (r2/H3): la columna es JSONB y el CASE del
        # ON CONFLICT aplica jsonb_array_length sobre el entrante — un None
        # (serializado como `null` JSONB) o cualquier no-lista abortaba el
        # savepoint en PostgreSQL y la oferta NO se persistía. None significa
        # "sin tags" y se normaliza a [] (en altas guarda lista válida; en
        # re-vistas el CASE de abajo decide igual que con [] explícito). Un
        # no-lista (una cadena, un dict) es un bug del productor, no un dato
        # degradado: coaccionarlo a [] podría machacar tags buenas con
        # description real, así que se rechaza ANTES de tocar la BD. Ningún
        # productor actual emite None ni no-listas (comprobado): esto es una
        # aserción de frontera, no puede crear falsos positivos (G2).
        if "tags" in values:
            if values["tags"] is None:
                values["tags"] = []
            elif not isinstance(values["tags"], list):
                raise ValueError(
                    f"tags debe ser una lista, no {type(values['tags']).__name__}"
                )
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
        # published_at: si el run actual NO trae la fecha (fallo del detalle,
        # cambio de DOM), conservar el valor bueno ya conocido en vez de
        # machacarlo con NULL. Única excepción al SET genérico (V.1 / ADR-10).
        if "published_at" in set_:
            set_["published_at"] = func.coalesce(
                stmt.excluded.published_at, Job.published_at
            )
        # description: mismo patrón que published_at — si el run actual llega
        # con description vacía (fallo parcial del detalle, p. ej. thehub), se
        # conserva la no vacía ya almacenada en vez de machacarla; NULLIF
        # equipara "" a NULL para el COALESCE. Sin esto, la description buena
        # se perdía en la re-vista, cambiaba el content_hash y el embedding se
        # re-generaba sobre texto vacío (y otra vez al recuperarse el detalle).
        # Los valores EFECTIVOS (los que de verdad quedan en la fila) parten
        # de la columna almacenada: si el campo NO viene en el payload, el
        # ON CONFLICT no lo toca y `excluded.<campo>` sería NULL — comparar
        # el embedding contra ese NULL (is_distinct_from) lo invalidaba
        # aunque el texto de la fila no cambiara.
        effective_description = Job.description
        if "description" in set_:
            effective_description = func.coalesce(
                func.nullif(stmt.excluded.description, ""), Job.description
            )
            set_["description"] = effective_description
        # description_snippet: misma protección que description. El entrante
        # degradado suele llegar como NULL (_snippet("") devuelve None en
        # BaseJobProvider), pero un "" asignado directamente también debe
        # contar como degradado — de ahí el NULLIF, como en description
        # (sin él, un "" entrante pisaba el snippet bueno).
        if "description_snippet" in set_:
            set_["description_snippet"] = func.coalesce(
                func.nullif(stmt.excluded.description_snippet, ""),
                Job.description_snippet,
            )
        # tags: en los providers salen de extract_job_skills(title, description),
        # así que un detalle fallido (thehub) las degrada a [] aunque la fila
        # tenga tags buenas. Señal de degradación: description entrante vacía Y
        # tags entrantes vacías ⇒ se conservan las almacenadas. Con description
        # entrante real, unas tags vacías SÍ pisan (re-cálculo legítimo). Las
        # fuentes que emiten description "" SIEMPRE (jobgether, publicjobs,
        # stelle_admin) derivan sus tags de título/campos de la API — fijados
        # por el hash — así que para ellas conservar es un no-op o la misma
        # protección deseada, nunca una pérdida (V2-1/C9, VD.9).
        # Mismo arranque que effective_description: sin `tags` en el payload
        # la columna se conserva y el embedding no debe invalidarse.
        effective_tags = Job.tags
        if "tags" in set_:
            effective_tags = case(
                (
                    and_(
                        func.coalesce(stmt.excluded.description, "") == "",
                        func.jsonb_array_length(stmt.excluded.tags) == 0,
                    ),
                    Job.tags,
                ),
                else_=stmt.excluded.tags,
            )
            set_["tags"] = effective_tags
        # location/canton: nunca pisar un valor REAL con uno vacío — un listado
        # degradado (thehub sin detalle trae location {}) llegaba con "" y
        # machacaba la ubicación buena. Una location entrante no vacía siempre
        # pasa (una oferta puede reubicarse legítimamente) y entonces el canton
        # entrante manda AUNQUE sea None: canton se deriva de location y
        # alimenta filtros — mejor sin cantón que un cantón obsoleto de la
        # ubicación anterior (V2-1/C10, VD.9).
        if "location" in set_:
            set_["location"] = func.coalesce(
                func.nullif(stmt.excluded.location, ""), Job.location
            )
        if "canton" in set_:
            # canton sigue la MISMA señal que location: solo se conserva
            # cuando la location entrante viene vacía (fetch degradado).
            set_["canton"] = case(
                (func.coalesce(stmt.excluded.location, "") == "", Job.canton),
                else_=stmt.excluded.canton,
            )
        elif "location" in set_:
            # canton OMITIDO del payload con location presente (r2/H5): si la
            # location entrante es real, dejar la columna intacta conservaba
            # el cantón de la ubicación ANTERIOR alimentando los filtros
            # geográficos — la semántica ya decidida (V2-1/C10) es "mejor sin
            # cantón que un cantón obsoleto", así que canton = NULL. Con
            # location entrante vacía (fetch degradado) se conservan ambos.
            set_["canton"] = case(
                (func.coalesce(stmt.excluded.location, "") == "", Job.canton),
                else_=null(),
            )
        # Reactivar SOLO si NO es duplicado: una oferta archivada que reaparece se
        # reactiva; un duplicado re-visto sigue inactivo (no vuelve a los feeds).
        set_["is_active"] = case((Job.duplicate_of.isnot(None), False), else_=True)
        # OJO (V2-2, VD.9): content_hash versiona el payload ENTRANTE, no la
        # fila efectiva — tras una re-vista degradada (campos conservados por
        # los COALESCE/CASE de arriba) el hash guardado NO coincide con el
        # contenido real de la fila y oscila un run hasta el siguiente fetch
        # sano. Hoy es cosmético: el único lector (tasks/embedding_tasks.py)
        # lo usa como CAS auto-consistente (snapshot vs. relectura de la misma
        # columna) y ni dedup, ni last_seen_at, ni caducidad, ni el core lo
        # consumen. Un consumidor futuro NO debe diffear content_hash entre
        # runs sin tener esta oscilación en cuenta.
        set_["content_hash"] = stmt.excluded.content_hash
        # El embedding se construye de title+company+description+tags
        # (JobMatcher.build_job_text). En un conflicto, title/company están fijados
        # por el hash, así que solo pueden cambiar description/tags: invalidamos el
        # embedding (NULL → re-embed + re-match) SOLO si esos cambian. Es
        # independiente de content_hash (que versiona MÁS campos): así ni un cambio
        # de logo/salario re-embebe texto idéntico (eficiencia), ni una fila anterior
        # a la columna (content_hash NULL) conserva un embedding obsoleto (correctitud).
        # Se compara contra la description y las tags EFECTIVAS (las que de
        # verdad quedan en la fila tras los COALESCE/CASE de arriba): un
        # entrante degradado que se conserva NO debe invalidar el embedding
        # (texto final idéntico) — comparar contra excluded.tags re-embebía en
        # cada transición degradada↔sana (V2-1/C9, VD.9).
        set_["embedding"] = case(
            (
                or_(
                    Job.description.is_distinct_from(effective_description),
                    Job.tags.is_distinct_from(effective_tags),
                ),
                null(),
            ),
            else_=Job.embedding,
        )

        await self.db.execute(
            stmt.on_conflict_do_update(index_elements=["hash"], set_=set_)
        )
        return is_new

    async def known_hashes(self, hashes: set[str]) -> set[str]:
        """De un conjunto de hashes, devuelve los que YA existen en `jobs`.

        Sustituye a `exists` por-oferta (K5): la ventana de cosecha (ADR-10
        rev. J1) solo puede rechazar ALTAS, nunca re-vistas — una oferta ya
        en `jobs` debe seguir pasando por el upsert para que se refresque
        `last_seen_at` (si se saltara, `cleanup_stale_jobs` la archivaría por
        "desaparecida del feed"). `precheck_batch` la llama UNA vez por
        fuente con los hashes fuera de ventana (`hash` es clave primaria),
        en vez de una consulta por oferta.
        """
        if not hashes:
            return set()
        result = await self.db.execute(select(Job.hash).where(Job.hash.in_(hashes)))
        return set(result.scalars())

    async def count_jobs(self, source_key: str) -> int:
        """Nº de filas de la fuente en `jobs`.

        Para la detección de deriva de identidad (K1, sustituye a
        `has_any_job` en la ronda 2): la deriva exige un corpus COMPARABLE al
        lote (`count >= len(lote)`) — con 2 filas en corpus y un lote de 10,
        no reconocer nada no es anómalo. Solo se consulta en el camino raro
        (lote con tamaño mínimo, descartes por fecha y nada reconocido),
        nunca por oferta ni por run normal.
        """
        result = await self.db.execute(
            select(func.count()).select_from(Job).where(Job.source == source_key)
        )
        return result.scalar_one()

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
