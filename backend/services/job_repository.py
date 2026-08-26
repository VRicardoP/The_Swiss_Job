"""JobRepository — database operations for job upsert and dedup management."""

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import and_, case, func, null, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job
from services.job_matcher import EMBEDDING_TAGS_VISIBLE_MAX_CHARS

logger = logging.getLogger(__name__)


def _column_max_len(column) -> int:
    """Cota de una columna String, verificada en el ARRANQUE (r4/R3-4).

    Si la columna migrara a Text, `type.length` sería None y `len(...) > None`
    lanzaría TypeError en CADA upsert — todas las ofertas degradadas hasta
    detectarlo. Mejor un fallo de import explícito que pérdida silenciosa
    por oferta.
    """
    length = getattr(column.type, "length", None)
    if length is None:
        # `raise` y no `assert` (r5/H3): con `-O`/PYTHONOPTIMIZE los asserts
        # se eliminan y el guard devolvía None en silencio — reproduciendo el
        # TypeError por-upsert que existe para evitar. Ningún arranque usa -O
        # hoy, pero eso es convención de despliegue, no garantía del código.
        raise TypeError(
            f"jobs.{column.key} sin cota de longitud (¿migrada a Text?): "
            "la frontera de upsert_job necesita el límite del modelo"
        )
    return length


# Cotas de columna (r3/R11): derivadas del modelo para no duplicar el número.
# `url` es la única columna que la identidad exige respetar sin truncar
# (hash + ix_jobs_url comparan la URL literal); `logo` comparte el String(2048)
# pero es decorativa — políticas distintas en upsert_job.
# Extensión opcional (r4/R3-7): title(500)/company(300)/location(300)/
# salary_original(200)/employment_type(100) siguen sin cota central — mismo
# modo de fallo pre-cota (savepoint abortado con error del driver) y mismo
# radio (solo esa oferta); solo se pierde claridad de mensaje. Añadirlas aquí
# si algún portal empieza a desbordarlas.
_URL_MAX_LEN: int = _column_max_len(Job.__table__.c.url)
_LOGO_MAX_LEN: int = _column_max_len(Job.__table__.c.logo)
# apply_url: contrato ALINEADO a 2048 (auditoría C5-P2-2, core0028) — el
# tope es el de la columna, idéntico en legacy y core.
_APPLY_URL_MAX_LEN: int = _column_max_len(Job.__table__.c.apply_url)

# Strings de contenido con protección NULLIF(valor, '') en el ON CONFLICT:
# se normalizan en la frontera para que "solo espacios" cuente como vacío
# (r3/H1, ver upsert_job). title/company/url NO entran: son identidad.
_BLANKABLE_TEXT_FIELDS: tuple[str, ...] = (
    "description",
    "description_snippet",
    "location",
)

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
        # Solo-espacios en la frontera (r3/H1): para NULLIF(valor, '') un
        # "   " o un "\t" son datos REALES — pisaban la description/snippet/
        # location buenas — y además hacían falsa la señal de degradación que
        # protege tags (coalesce(excluded.description,'') == ''), con lo que
        # una re-vista degradada destruía los cinco valores útiles e
        # invalidaba el embedding. Normalizar a "" ANTES de construir el
        # INSERT hace que la cascada de protecciones existente los vea como
        # vacíos. Los strings NO vacíos no se recortan: cambiar el contenido
        # real cambiaría content_hash y el texto del embedding (G4/G5).
        for field in _BLANKABLE_TEXT_FIELDS:
            value = values.get(field)
            if isinstance(value, str) and not value.strip():
                values[field] = ""
        # url en la frontera (r3/R11): varios scrapers construyen la URL con
        # datos del portal sin acotar y un desborde de String(2048) abortaba
        # el savepoint con un error del driver. Mismo patrón y justificación
        # que el rechazo del tags no-lista: degradar ESTA oferta con un
        # mensaje claro, aquí, donde vive la columna. Truncar no es opción:
        # la URL es identidad (hash + ix_jobs_url). La cota local de
        # financejobs se conserva (allí evita además perseguir una URL
        # absurda); esta es la red central para el resto de fuentes.
        # Residual conocido (r4/R3-6): una oferta con URL desbordada nunca se
        # persiste, así que no entra en el cursor (correcto por VD.2) y se
        # re-intenta y rechaza en CADA run — ruido de log permanente hasta
        # que la fuente deje de emitirla. Asumido: preferible a truncar la
        # identidad o a meter en el cursor URLs no persistidas.
        url = values.get("url")
        if isinstance(url, str) and len(url) > _URL_MAX_LEN:
            raise ValueError(
                f"url excede String({_URL_MAX_LEN}): {len(url)} caracteres"
            )
        # logo comparte el String(2048) pero es decorativo: un logo
        # kilométrico no debe costar la oferta entera — se degrada SOLO el
        # campo. `pop` y no `= None` (r6/H4, G5): el None asignado ENTRABA en
        # el ON CONFLICT y pisaba el logo bueno ya almacenado — degradar el
        # dato inválido no puede destruir el válido. Omitido del INSERT, en
        # un alta la columna queda en su default (NULL) y en una re-vista el
        # SET no la toca. Con rastro (r4/R3-5): misma disciplina que el resto
        # de degradaciones del fichero.
        # r7/H5 (G5): el None/""/tipo inválido EXPLÍCITO del productor tampoco
        # pisa el almacenado. Los productores construyen el valor con
        # `.get("logo")`, así que NO pueden distinguir "el portal retiró el
        # logo" de "este fetch no lo trajo": tratar None como borrado
        # autoritativo es interpretar como intención lo que es ausencia de
        # dato. Si algún día hace falta un borrado autoritativo, el DTO tendrá
        # que distinguir "campo omitido" de "borrado explícito" (p. ej. un
        # sentinel dedicado) — deliberadamente NO implementado. SOLO logo:
        # False, 0 y algunos None sí son datos legítimos en otras columnas
        # (canton entrante None con location real, p. ej.) — nada de coalesce
        # genérico.
        # apply_url (R.6): misma disciplina que logo — decorativa para el
        # legacy (la consume el CORE como señal de dedup); NUL o desborde
        # degradan SOLO el campo, jamás la oferta. pop y no None: un None
        # entraría al ON CONFLICT y pisaría el valor bueno almacenado.
        if "apply_url" in values:
            aurl = values["apply_url"]
            if isinstance(aurl, str):
                aurl = aurl.strip()  # C4: sin padding almacenado ni medido
                values["apply_url"] = aurl
            if not isinstance(aurl, str) or not aurl:
                values.pop("apply_url")
            elif "\x00" in aurl or len(aurl) > _APPLY_URL_MAX_LEN:
                logger.info(
                    "apply_url invalido (NUL o >%d): campo descartado (url=%s)",
                    _APPLY_URL_MAX_LEN,
                    values.get("url"),
                )
                values.pop("apply_url")
        if "logo" in values:
            logo = values["logo"]
            if isinstance(logo, str) and "\x00" in logo:
                # Un byte NUL revienta el INSERT entero en Postgres
                # (CharacterNotInRepertoireError) y costaba la OFERTA: en un
                # alta se pierde y en una re-vista no refresca last_seen_at
                # (a 60 días, cleanup_stale_jobs la borra). El logo es
                # decorativo: se degrada SOLO el campo, con rastro — misma
                # disciplina que el logo desbordado de abajo.
                logger.info(
                    "logo con byte NUL: campo descartado (url=%s)",
                    values.get("url"),
                )
                values.pop("logo")
            elif isinstance(logo, str) and len(logo) > _LOGO_MAX_LEN:
                logger.info(
                    "logo excede String(%d) (%d caracteres): campo descartado (url=%s)",
                    _LOGO_MAX_LEN,
                    len(logo),
                    values.get("url"),
                )
                values.pop("logo")
            elif not (isinstance(logo, str) and logo.strip()):
                # Ausencia de dato (None, "" o solo espacios): se omite del
                # INSERT sin log — es el estado normal de la mayoría de fetches.
                # Un logo no-string (dict, int…) NO es ausencia sino un bug del
                # productor (antes abortaba el savepoint con DBAPIError): se
                # descarta igual pero con rastro (r4/R3-5), misma disciplina
                # que el logo desbordado de arriba.
                if logo is not None and not isinstance(logo, str):
                    logger.info(
                        "logo no-string (%s): campo descartado (url=%s)",
                        type(logo).__name__,
                        values.get("url"),
                    )
                values.pop("logo")
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
        reactivating = and_(Job.is_active.is_(False), Job.duplicate_of.is_(None))
        set_["is_active"] = case((Job.duplicate_of.isnot(None), False), else_=True)
        # G3/P3-10: la reactivación de arriba no distingue "archivada por
        # caducidad" de "desactivada por URL muerta (404/410)" — el portal sigue
        # listando la URL muerta, el upsert la revive y, como `url_last_check`
        # quedaba intacto (fechado en la sonda que la mató), el
        # `order_by(nulls_first(...))` de check_job_urls no la volvía a sondear
        # hasta completar TODA la rotación: oscilaba indefinidamente sirviendo un
        # 404 a los usuarios. Al reactivar se borra la marca de sondeo, de modo
        # que la oferta resucitada entra a la CABECERA de la rotación y se
        # re-verifica en el siguiente barrido. Solo en la transición
        # inactiva→activa: una oferta ya activa conserva su rotación normal.
        set_["url_last_check"] = case((reactivating, null()), else_=Job.url_last_check)
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
        # G3/P3-8: las tags van al FINAL de build_job_text y el encoder trunca a
        # 128 tokens, así que en una oferta con descripción larga no llegan al
        # vector (medido: con 1700 chars de descripción, tags=[...] y tags=[]
        # producen el MISMO vector). Invalidar por ellas dejaba la oferta con
        # embedding NULL —fuera de _stage1_vector_search y de
        # find_semantic_duplicates— hasta el siguiente embed_all_pending, a
        # cambio de re-calcular un vector idéntico. Un cambio de tags solo
        # invalida si la descripción efectiva es lo bastante corta como para
        # dejarles sitio en la ventana (EMBEDDING_TAGS_VISIBLE_MAX_CHARS).
        tags_reach_encoder = (
            func.char_length(func.coalesce(effective_description, ""))
            <= EMBEDDING_TAGS_VISIBLE_MAX_CHARS
        )
        set_["embedding"] = case(
            (
                or_(
                    Job.description.is_distinct_from(effective_description),
                    and_(
                        Job.tags.is_distinct_from(effective_tags),
                        tags_reach_encoder,
                    ),
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
