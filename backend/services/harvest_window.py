"""Ventana de cosecha (ADR-10 rev. J1, fase 2B).

Responsabilidad principal: decidir si cada oferta de un lote ENTRA en el
corpus, dados su fuente y su `published_at`. Además — y desde la ronda 2 de
revisión, dicho explícitamente — el módulo aloja la MONITORIZACIÓN de
integridad del corpus que esa decisión hace necesaria: la vigilancia de
deriva de identidad (`watch_drift` / `report_identity_drift`) y los
guardarraíles de logging sobre las fechas (`log_window_summary`). Descartar
datos obliga a vigilar que el descarte siga siendo el correcto, y esa
vigilancia vive junto a la regla que la motiva.

La entrada de los pipelines es
`precheck_batch` (K5): una pre-pasada pura sobre el lote + UNA sola consulta
por fuente (`JobRepository.known_hashes`) para distinguir ALTA de RE-VISTA en
las ofertas que caen fuera de la ventana. Devuelve un veredicto EXPLÍCITO por
oferta (`ACCEPT` / `SKIP_STALE` / `SKIP_NO_DATE`, K6): los contadores se
atribuyen aquí, en un único sitio, no re-derivando `published_at is None` en
cada pipeline. La aplicación del veredicto sigue siendo de los pipelines
(`fetch_tasks` / `scraping_tasks`).

Semántica de los contadores (K3, léelo antes de revisar N):

- En los PROVIDERS (sin cursor: re-bajan el listado entero en cada run),
  `window_skipped` es FLUJO POR RUN, no ofertas únicas — la misma oferta
  fuera de ventana se re-cuenta en cada run hasta que el portal la retire.
  Leído en bruto sobreestima la pérdida en un factor de ~7 a 23.
- En los SCRAPERS, las identidades descartadas por fecha SÍ entran en el
  cursor (descarte por política: la fecha que PUBLICA EL PORTAL no suele
  cambiar y el corte solo avanza), así que no se re-descargan ni se
  re-cuentan y el contador se aproxima a ofertas únicas. Dos residuales
  conocidos de esa premisa (registrados a propósito, sin arreglo):
  (a) la fecha la pone el portal — un anuncio RENOVADO con la misma URL y
  fecha nueva (patrón real de `tes`) ya está aprendido como stale y, si es
  la única novedad de su página, el early-stop impide re-evaluarlo aunque
  ahora caiga dentro de ventana; (b) en listados grandes (`irishjobs`) el
  caudal de stale puede superar `CURSOR_RECENT_IDENTITIES_MAX` — las
  identidades expulsadas se re-descargan y se re-cuentan, `window_skipped`
  se desliza de nuevo hacia "flujo por run" y el early-stop deja de cortar
  en esas páginas (sin pérdida de datos: solo coste y contador inflado).
- ⚠ Las identidades stale ya aprendidas por el cursor hacen que el
  early-stop no vuelva a paginar sobre ellas. Por eso, SUBIR
  `HARVEST_WINDOW_DAYS`, APAGAR `HARVEST_WINDOW_ENABLED` (el caso extremo de
  subir N: sin vaciado, las ofertas viejas aún listadas NO se re-descargan y
  el interruptor no restaura el comportamiento previo), RECLASIFICAR una
  fuente a FULL o CORREGIR una deriva de identidad real exigen vaciar
  `recent_identities` de las fuentes WINDOW afectadas:

      UPDATE source_cursors SET recent_identities = '[]'::jsonb
       WHERE source_key IN ('<fuentes WINDOW afectadas>');

Dos políticas, y solo dos:

- ``WINDOW`` — solo entran las ALTAS publicadas dentro de la ventana móvil
  (`HARVEST_WINDOW_DAYS`, default 7). Sin `published_at` ⇒ NO entra: para una
  fuente clasificada así la ausencia de fecha es una anomalía, y dejarla
  pasar vaciaría la ventana de sentido.
- ``FULL`` — se cosecha entera, sin ventana. Es la política de los colegios
  suizos (la excepción de ADR-10) y la de toda fuente que no expone fecha:
  descartarlo todo la dejaría muda en silencio, que es exactamente el fallo
  que este proyecto persigue.

La ventana aplica en TODOS los runs, y SOLO a ALTAS (rev. J1 de ADR-10,
decisión por delegación): la versión literal "solo en el bootstrap" era
decorativa — el run siguiente, ya con filas en `jobs`, re-descargaba y
guardaba todo lo descartado, porque 20 de las 23 fuentes WINDOW son
providers sin cursor ni early-stop y re-descargan el listado entero en cada
run. La lectura enforceable es: *el corpus contiene ofertas publicadas en los
últimos N días; las que ya están dentro se siguen refrescando con
normalidad*. Que NUNCA se rechacen re-vistas lo garantiza `precheck_batch`
consultando `JobRepository.known_hashes` solo con las ofertas fuera de
ventana: saltarse el upsert de una re-vista dejaría de refrescar su
`last_seen_at` y `cleanup_stale_jobs` la archivaría por "desaparecida".

El compromiso, dicho en voz alta: una oferta que un portal nos muestre por
primera vez más de N días después de publicarse no se ingesta. Por eso N es
configurable y por eso existen los contadores `window_skipped`/`window_no_date`.

El registro `_POLICIES` es EXPLÍCITO fuente por fuente, con el motivo en el
propio dato: ADR-10 exige "decidir fuente por fuente y dejarlo registrado,
porque un default silencioso reintroduce el problema que esta ADR corrige".
La asignación sale del inventario de `published_at` (sondas en vivo del
2026-08-14): WINDOW = fuentes que SÍ exponen fecha utilizable (excepto
`swiss_schools_isp`, cuyo `postedOn` es texto relativo en buckets);
FULL = colegios, fuentes sin fecha, inciertas y restringidas sin conector.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, NamedTuple

from config import settings

if TYPE_CHECKING:  # solo para tipos: evita acoplar el import en runtime
    from services.job_repository import JobRepository

logger = logging.getLogger(__name__)

# Los dos modos posibles. Strings (no Enum) a propósito: se comparan por
# identidad de módulo y se imprimen tal cual en logs y en el informe.
WINDOW = "window"
FULL = "full"

# Veredicto EXPLÍCITO por oferta (K6). Strings por el mismo motivo que los
# modos. Si mañana aparece un tercer motivo de descarte, se añade AQUÍ y los
# contadores de ambos pipelines se atribuyen solos — antes se re-derivaba
# `published_at is None` en dos sitios y un motivo nuevo se desatribuiría en
# silencio.
ACCEPT = "accept"
SKIP_STALE = "skip_stale"  # alta con fecha fuera de la ventana
SKIP_NO_DATE = "skip_no_date"  # alta sin fecha utilizable en política WINDOW

# L4 — mínimo de descartes por falta de fecha para el WARNING de fallo
# parcial (K2): con "más de cero", UNA oferta perennemente sin fecha en el
# listado (nunca entra en corpus, se reprocesa cada run) más un run sin
# altas — lo normal en régimen estacionario — producía el WARNING cada run,
# indefinidamente.
_MIN_NO_DATE_FOR_WARNING = 3


class WindowPrecheck(NamedTuple):
    """Resultado de la pre-pasada de un lote (K5): veredictos + contadores.

    `verdicts` es paralelo al lote de entrada (un veredicto por oferta).
    `recognized` = re-vistas fuera de ventana halladas en `jobs` — alimenta la
    detección de deriva de identidad (K1). `policy_mode` viaja aquí para que
    la política se resuelva UNA vez por fuente (K8), no por oferta.
    """

    verdicts: list[str]
    skipped_by_date: int
    skipped_no_date: int
    saw_published_at: bool
    recognized: int
    policy_mode: str


class HarvestPolicy(NamedTuple):
    """Política de cosecha de una fuente + el MOTIVO de la decisión."""

    mode: str  # WINDOW | FULL
    reason: str


# ---------------------------------------------------------------------------
# Registro de políticas — UNA entrada por fuente, con motivo. Este dict ES la
# documentación de la decisión (ADR-10). Una fuente nueva no puede entrar sin
# añadirse aquí: lo fija el test de completitud de test_harvest_window.py.
# Incluye también las fuentes hoy deshabilitadas en los registros (reliefweb,
# himalayas...) para que reactivarlas mañana no reintroduzca el hueco.
# ---------------------------------------------------------------------------
_POLICIES: dict[str, HarvestPolicy] = {
    # --- WINDOW — el inventario confirmó fecha de publicación utilizable ---
    "ostjob": HarvestPolicy(WINDOW, "API chmedia: dateFirstPublished (ISO8601)"),
    "zentraljob": HarvestPolicy(WINDOW, "API chmedia: dateFirstPublished (ISO8601)"),
    "nav_arbeidsplassen": HarvestPolicy(WINDOW, "API: _source.published (ISO8601)"),
    "publicjobs": HarvestPolicy(WINDOW, "API: publicFrom (ISO8601 Z)"),
    "jobgether": HarvestPolicy(WINDOW, "API: createdAt (ISO8601 Z)"),
    "arbeitnow": HarvestPolicy(WINDOW, "API: created_at (epoch en segundos)"),
    "remotive": HarvestPolicy(WINDOW, "API: publication_date (ISO8601 sin TZ)"),
    "workingnomads": HarvestPolicy(WINDOW, "API: pub_date (ISO8601 con offset)"),
    "weworkremotely": HarvestPolicy(WINDOW, "RSS: pubDate (RFC822)"),
    "euremotejobs": HarvestPolicy(WINDOW, "RSS WP: pubDate (RFC822)"),
    "jobspresso": HarvestPolicy(WINDOW, "RSS WP: pubDate (RFC822)"),
    "globaljobs": HarvestPolicy(WINDOW, "RSS: pubDate (RFC822, a medianoche)"),
    "zebis": HarvestPolicy(WINDOW, "RSS: pubDate (RFC822)"),
    "financejobs": HarvestPolicy(WINDOW, "__NEXT_DATA__: datePosted (ISO8601)"),
    "tes": HarvestPolicy(WINDOW, "__NEXT_DATA__: advert.startDate (ISO8601)"),
    "irishjobs": HarvestPolicy(WINDOW, "__PRELOADED_STATE__: datePosted (ISO8601 Z)"),
    "schuljobs": HarvestPolicy(
        WINDOW, "JSON-LD del detalle (ya descargado): datePosted (YYYY-MM-DD)"
    ),
    # Deshabilitadas hoy en el registro pero con fecha confirmada: se dejan
    # decididas para que reactivarlas no cree un hueco de política.
    "reliefweb": HarvestPolicy(
        WINDOW, "API: fields.date.created (ISO8601) — deshabilitada hoy"
    ),
    "himalayas": HarvestPolicy(
        WINDOW, "API: pubDate (epoch en segundos) — deshabilitada hoy"
    ),
    "jobicy": HarvestPolicy(
        WINDOW, "API: pubDate (ISO8601 con offset) — deshabilitada hoy"
    ),
    "remoteok": HarvestPolicy(
        WINDOW, "API: date + epoch (ISO8601/epoch) — deshabilitada hoy"
    ),
    "ictjobs": HarvestPolicy(
        WINDOW, "API WP REST: date_gmt (ISO8601 sin TZ) — deshabilitada hoy"
    ),
    "swisstechjobs": HarvestPolicy(
        WINDOW, "API WP REST: date_gmt (ISO8601 sin TZ) — deshabilitada hoy"
    ),
    # --- FULL — colegios suizos: la excepción explícita de ADR-10 ---
    # Pocos, de rotación lenta y de alto valor para el perfil objetivo: se
    # cosechan ENTEROS, sin ventana en ningún run (una ventana semanal
    # perdería el histórico vigente).
    "swiss_schools_nae": HarvestPolicy(
        FULL, "excepción colegios ADR-10; además no expone fecha de publicación"
    ),
    "swiss_schools_isp": HarvestPolicy(
        FULL,
        "excepción colegios ADR-10; su postedOn (Workday) es texto relativo"
        " en buckets, no una fecha",
    ),
    "swiss_schools_inspired": HarvestPolicy(
        FULL, "excepción colegios ADR-10; además no expone fecha de publicación"
    ),
    "swiss_schools_ecolint": HarvestPolicy(
        FULL,
        "excepción colegios ADR-10; expone incorporación y deadline,"
        " no fecha de publicación",
    ),
    "swiss_schools_zis": HarvestPolicy(
        FULL, "excepción colegios ADR-10; además no expone fecha de publicación"
    ),
    "swiss_schools_isb": HarvestPolicy(
        FULL, "excepción colegios ADR-10; inventario INCIERTO (fuente muda hoy)"
    ),
    "swiss_schools_hautlac": HarvestPolicy(
        FULL, "excepción colegios ADR-10; además no expone fecha de publicación"
    ),
    "swiss_schools_iscs": HarvestPolicy(
        FULL, "excepción colegios ADR-10; además no expone fecha de publicación"
    ),
    # --- FULL — restringidas sin conector (arrancan deshabilitadas) ---
    "jobcloud_partner": HarvestPolicy(
        FULL, "restringida sin conector: sin credencial no hay payload que sondear"
    ),
    "linkedin_authorized": HarvestPolicy(
        FULL, "restringida sin conector: sin credencial no hay payload que sondear"
    ),
    "indeed_partner": HarvestPolicy(
        FULL, "restringida sin conector: sin credencial no hay payload que sondear"
    ),
    "glassdoor_partner": HarvestPolicy(
        FULL, "restringida sin conector: sin credencial no hay payload que sondear"
    ),
    "xing_partner": HarvestPolicy(
        FULL, "restringida sin conector: sin credencial no hay payload que sondear"
    ),
    # --- FULL — inventario INCIERTO: sin fecha confirmada, no perder nada ---
    "adzuna": HarvestPolicy(
        FULL, "INCIERTO: sin API key no sondeable (doc sugiere `created`)"
    ),
    "careerjet": HarvestPolicy(
        FULL, "INCIERTO: sin API key no sondeable (doc sugiere sort por fecha)"
    ),
    "jooble": HarvestPolicy(
        FULL, "INCIERTO: sin API key no sondeable (doc sugiere `updated`)"
    ),
    "jsearch": HarvestPolicy(
        FULL,
        "INCIERTO: sin API key no sondeable (doc sugiere job_posted_at_datetime_utc)",
    ),
    "proz": HarvestPolicy(FULL, "INCIERTO: feed muerto/bloqueado en la sonda"),
    "translatorscafe": HarvestPolicy(
        FULL, "INCIERTO: feed muerto/bloqueado en la sonda"
    ),
    "dailyremote": HarvestPolicy(FULL, "INCIERTO: feed muerto/bloqueado en la sonda"),
    "authenticjobs": HarvestPolicy(FULL, "INCIERTO: feed muerto/bloqueado en la sonda"),
    "remoteco": HarvestPolicy(FULL, "INCIERTO: feed muerto/bloqueado en la sonda"),
    "gastrojob": HarvestPolicy(FULL, "INCIERTO: fuente muda hoy, sin sonda posible"),
    "stelle_admin": HarvestPolicy(FULL, "INCIERTO: fuente muda hoy, sin sonda posible"),
    "thehub": HarvestPolicy(
        FULL, "INCIERTO: /api/jobs devolvió página de error en la sonda"
    ),
    "myscience": HarvestPolicy(
        FULL, "INCIERTO: viva pero la sonda quedó bloqueada sin la capa stealth"
    ),
    # Deshabilitadas hoy y sin fecha confirmada: decididas por si reactivan.
    "undpjobs": HarvestPolicy(
        FULL, "INCIERTO: deshabilitada (¿dc:date?), sin sonda posible"
    ),
    "ilojobs": HarvestPolicy(FULL, "INCIERTO: deshabilitada, sin sonda posible"),
    "impactpool": HarvestPolicy(FULL, "INCIERTO: deshabilitada, sin sonda posible"),
    "untalent": HarvestPolicy(FULL, "INCIERTO: deshabilitada, sin sonda posible"),
    "medjobs": HarvestPolicy(
        FULL, "INCIERTO: deshabilitada (challenge Cloudflare), sin sonda posible"
    ),
}

# ---------------------------------------------------------------------------
# K10 — fuentes DECIDIDAS POR ADELANTADO: registradas en _POLICIES a propósito
# aunque hoy NO exista clase viva en providers/scrapers (están deshabilitadas
# en sus registros). El test de completitud INVERSA exige que toda clave de
# _POLICIES esté en el código vivo o en esta lista: sin ella, renombrar una
# fuente dejaría una entrada huérfana en el registro sin ningún aviso. Al
# reactivar una de estas, sácala de aquí (el test también vigila el solape).
# ---------------------------------------------------------------------------
SOURCES_DECIDED_IN_ADVANCE: frozenset[str] = frozenset(
    {
        # WINDOW con fecha confirmada, deshabilitadas hoy
        "reliefweb",
        "himalayas",
        "jobicy",
        "remoteok",
        "ictjobs",
        "swisstechjobs",
        # FULL inciertas, deshabilitadas hoy
        "undpjobs",
        "ilojobs",
        "impactpool",
        "untalent",
        "medjobs",
    }
)


def policy_for(source_key: str) -> HarvestPolicy:
    """Política de cosecha de una fuente.

    Fuente NO registrada ⇒ ``FULL`` (el comportamiento de hoy: no perder
    nada) pero NUNCA en silencio: se loguea un ERROR nombrándola, porque una
    fuente sin decisión es exactamente el default silencioso que ADR-10
    prohíbe.
    """
    policy = _POLICIES.get(source_key)
    if policy is None:
        logger.error(
            "Fuente '%s' SIN política de cosecha registrada — se aplica FULL "
            "(no perder nada). Añádela a _POLICIES en "
            "services/harvest_window.py con su motivo (ADR-10 prohíbe el "
            "default silencioso).",
            source_key,
        )
        return HarvestPolicy(FULL, "fuente sin registrar — default seguro FULL")
    return policy


def _is_aware_datetime(value: object) -> bool:
    """K9 — True solo para `datetime` timezone-aware. Cualquier otra cosa
    (None, naive, tipos ajenos) se trata como "sin fecha": comparar a ciegas
    una naive lanzaría `TypeError` FUERA del savepoint por-oferta y perdería
    el lote entero de la fuente."""
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.tzinfo.utcoffset(value) is not None
    )


def _cutoff(now: datetime | None) -> datetime:
    # Ventana MÓVIL de N días, no la semana natural (precisión por delegación
    # sobre ADR-10): el borde exacto de la ventana queda DENTRO (>=).
    # Granularidad solo-fecha (registrado a propósito, sin arreglo): fuentes
    # como globaljobs (pubDate a medianoche) o schuljobs (YYYY-MM-DD) dan una
    # ventana efectiva de 6-7 días según la hora del run — inherente al dato
    # del portal.
    reference = now if now is not None else datetime.now(timezone.utc)
    return reference - timedelta(days=settings.HARVEST_WINDOW_DAYS)


def _job_verdict(mode: str, published_at: object, cutoff: datetime) -> str:
    """Veredicto puro de UNA oferta con la política ya resuelta (K6/K8)."""
    if mode == FULL:
        return ACCEPT
    if not _is_aware_datetime(published_at):
        # Política WINDOW sin fecha utilizable: anomalía — no entra (ver
        # docstring del módulo). Se cuenta aparte (`window_no_date`) y los
        # guardarraíles de `log_window_summary` vigilan la fuente.
        return SKIP_NO_DATE
    return ACCEPT if published_at >= cutoff else SKIP_STALE


def accepts(
    source_key: str,
    published_at: datetime | None,
    *,
    now: datetime | None = None,
) -> bool:
    """True si la oferta entra en el corpus; False si la ventana la descarta.

    Es el PREDICADO PURO que documenta la regla de la ventana: ya no lo llama
    ningún pipeline (solo los tests y esta documentación) — la ruta de
    producción es `precheck_batch`, que comparte `_job_verdict` con esta
    función, así que el riesgo de divergencia entre ambas es bajo y los tests
    de la matriz cubren las dos rutas.

    Es PURO respecto a la BD: no distingue alta de re-vista — eso lo hace
    `precheck_batch` con `JobRepository.known_hashes`. `now` es inyectable
    para poder testear el borde exacto de la ventana sin congelar el reloj.
    `published_at` debería ser timezone-aware (contrato de
    `utils.dates.parse_published_at`, fase 2A); si no lo es, se trata como
    "sin fecha" (K9), nunca se compara a ciegas.
    """
    if not settings.HARVEST_WINDOW_ENABLED:
        # Interruptor apagado ⇒ comportamiento idéntico al pipeline sin
        # ventana: la ventana descarta datos y debe poder apagarse en caliente.
        return True
    verdict = _job_verdict(policy_for(source_key).mode, published_at, _cutoff(now))
    return verdict == ACCEPT


async def precheck_batch(
    source_key: str,
    jobs: Sequence[Mapping],
    repo: "JobRepository",
    *,
    now: datetime | None = None,
) -> WindowPrecheck:
    """Pre-pasada de la ventana sobre el lote ENTERO de una fuente (K5).

    1. Resuelve la política UNA vez por fuente (K8: una fuente sin registrar
       emite un solo ERROR por run, no uno por oferta).
    2. Veredicto puro por oferta (sin BD).
    3. UNA única consulta por fuente (`known_hashes`, PK) con los hashes de
       las ofertas que caerían descartadas: las que YA están en `jobs` son
       re-vistas y se convierten en ACCEPT — la propiedad de J1 (rechazar
       solo altas, nunca re-vistas) se conserva exactamente, sin pagar una
       consulta por oferta ni duplicarla luego en `upsert_job`.
    """
    saw_published_at = any(_is_aware_datetime(j.get("published_at")) for j in jobs)
    if not settings.HARVEST_WINDOW_ENABLED:
        # Interruptor apagado ⇒ entra todo, como el pipeline sin ventana. La
        # política se resuelve EN SILENCIO (sin el ERROR de fuente sin
        # registrar): aquí es solo informativa y los guardarraíles que la
        # usan también están apagados.
        mode = _POLICIES.get(source_key, HarvestPolicy(FULL, "")).mode
        return WindowPrecheck([ACCEPT] * len(jobs), 0, 0, saw_published_at, 0, mode)

    policy = policy_for(source_key)
    cutoff = _cutoff(now)
    verdicts = [_job_verdict(policy.mode, j.get("published_at"), cutoff) for j in jobs]
    pending = {j["hash"] for j, v in zip(jobs, verdicts) if v != ACCEPT}
    known = await repo.known_hashes(pending) if pending else set()

    final: list[str] = []
    skipped_by_date = 0
    skipped_no_date = 0
    recognized = 0
    for job, verdict in zip(jobs, verdicts):
        if verdict != ACCEPT and job["hash"] in known:
            # Re-vista fuera de ventana: pasa al upsert (refresca last_seen_at).
            recognized += 1
            final.append(ACCEPT)
        else:
            final.append(verdict)
            if verdict == SKIP_STALE:
                skipped_by_date += 1
            elif verdict == SKIP_NO_DATE:
                skipped_no_date += 1
    return WindowPrecheck(
        final,
        skipped_by_date,
        skipped_no_date,
        saw_published_at,
        recognized,
        policy.mode,
    )


async def watch_drift(
    repo: "JobRepository", source_key: str, precheck: WindowPrecheck
) -> bool:
    """K1 (1/2) — ANTES de los upserts: ¿se dan las condiciones para VIGILAR
    la deriva de identidad en este run? (Nombre honesto — antes
    `corpus_at_risk`, que prometía un veredicto que esta mitad no da: el
    veredicto lo emite `report_identity_drift` tras los upserts.)

    El síntoma de la deriva (hash = md5(title|company|url) que cambia en
    bloque) NO es "descarta mucho" — una fuente WINDOW sana descarta ~80 % de
    su listado cada run y debe seguir en silencio. El síntoma es: la fuente
    tiene un corpus comparable al lote, hay descartes por antigüedad en juego
    y este run no ha RECONOCIDO nada POR SU MISMO HASH. Tres cláusulas, y
    cada una mata un modo de fallo concreto (ronda 2 de revisión):

    1. `recognized` cuenta SOLO reconocimiento por el mismo hash (re-vistas
       fuera de ventana de la pre-pasada; la otra mitad, los `updated`, la
       aporta `report_identity_drift`). Los duplicados fuzzy quedan FUERA:
       `Deduplicator.find_fuzzy_duplicate` solo casa con OTRA fuente y no es
       evidencia de estabilidad de identidad de ESTA — contarlos silenciaba
       la deriva insignia (falso negativo) en cuanto una alta casaba con un
       board que sindica el mismo contenido.
    2. `skipped_by_date > 0`: sin descartes por antigüedad no hay pérdida
       posible — la deriva con todo el lote dentro de ventana solo produce
       duplicados, que son recuperables. Es la condición semánticamente
       correcta (la deriva solo hace daño donde hay rechazos) y mata el
       falso positivo del lote pequeño y reciente todo-nuevo.
    3. El corpus tiene que ser COMPARABLE al lote: `count_jobs(source) >=
       len(lote)`. Con 2 filas en corpus y un lote de 10, no reconocer nada
       no es anómalo (portal que retiró del listado el poco corpus que
       tenemos, p. ej. `zebis`); con 3 000 filas y un lote de 375, sí lo es.
       El conteo se ejecuta solo en este camino, que es raro.

    Se evalúa a propósito ANTES del bucle de upserts: el corpus que la
    deriva puede perder es el PREVIO al run — contar las altas que este
    mismo run inserta dispararía un falso positivo en el bootstrap de una
    fuente sana.
    """
    if not settings.HARVEST_WINDOW_ENABLED or precheck.policy_mode != WINDOW:
        return False
    if len(precheck.verdicts) < settings.HARVEST_ALERT_MIN_BATCH:
        return False
    # Cláusula 2 — sin rechazos por antigüedad la deriva no puede perder nada.
    if precheck.skipped_by_date == 0:
        return False
    # Cláusula 1 (mitad de la pre-pasada) — reconocimiento por mismo hash.
    if precheck.recognized > 0:
        return False
    # Cláusula 3 — corpus comparable al lote, no un "hay alguna fila".
    return await repo.count_jobs(source_key) >= len(precheck.verdicts)


def report_identity_drift(
    source_key: str, precheck: WindowPrecheck, *, updated_in_upserts: int
) -> str | None:
    """K1 (2/2) — DESPUÉS de los upserts: veredicto final de la deriva.

    `updated_in_upserts` = upserts del run que resultaron no-nuevos
    (delta de `updated`): la otra mitad del reconocimiento POR MISMO HASH.
    Los duplicados fuzzy NO entran (cláusula 1 de `watch_drift`: son
    cross-source y no dicen nada de la identidad de esta fuente).
    reconocidas = precheck.recognized + updated_in_upserts; como
    `watch_drift` ya exigió precheck.recognized == 0, basta con mirar los
    upserts. Si sigue a cero: ERROR nombrando la fuente y la sospecha, y
    motivo para `summary["unhealthy"]` — antes de este guard la deriva
    convertía todas las re-vistas en altas rechazables y a los 60 días
    `cleanup_stale_jobs` BORRABA el corpus de la fuente, con un contador
    como única señal.

    Residual conocido (registrado a propósito, sin arreglo): una deriva
    PARCIAL — cambio de esquema solo en una sección del portal — mantiene
    `recognized > 0` por las secciones intactas y no grita mientras ese
    subconjunto se pudre.
    """
    if updated_in_upserts > 0:
        return None
    motivo = (
        "posible deriva de identidad: "
        f"{len(precheck.verdicts)} ofertas descargadas, ninguna reconocida, "
        "con corpus comparable"
    )
    logger.error(
        "Fuente '%s' — %s. Si el portal cambió su esquema de URLs (o un "
        "deploy tocó los campos del hash), TODAS sus re-vistas parecen altas "
        "rechazables: dejan de refrescar last_seen_at y cleanup_stale_jobs "
        "acabará borrándolas. Revisar normalize_job / compute_hash de la "
        "fuente YA. Tras corregir la deriva, vaciar recent_identities de la "
        "fuente en source_cursors (SQL en el docstring de "
        "services/harvest_window.py): las identidades stale aprendidas "
        "impiden que el early-stop re-pagine sobre ellas.",
        source_key,
        motivo,
    )
    return motivo


def log_window_summary(
    source_key: str,
    precheck: WindowPrecheck,
    *,
    new_count: int,
) -> None:
    """Rastro obligatorio de la ventana: descarta datos y no puede hacerlo en
    silencio. Lo llaman ambos pipelines al acabar el bucle de CADA fuente en
    CADA run (rev. J1: la ventana ya no es del bootstrap). `new_count` son las
    ALTAS que el run SÍ consiguió ingerir de esta fuente (upserts nuevos +
    duplicados fuzzy).

    Dos guardarraíles escalonados sobre las fechas, AMBOS con tamaño mínimo
    de lote (`HARVEST_ALERT_MIN_BATCH`, compartido con el detector de deriva
    — ronda 2 de revisión: un lote de 1-2 ofertas con un hipo transitorio de
    parseo no puede disparar un diagnóstico grave):

    - ERROR — NINGUNA oferta del lote traía `published_at`: la política está
      mal asignada (inventario obsoleto); sus altas caen a cero y sin esto se
      manifestaría como "la fuente dejó de traer nada", otro fallo disfrazado
      de éxito. `saw_published_at` mira el lote ENTERO (también las aceptadas
      por re-vista), no solo lo descartado.
    - WARNING (K2) — fallo PARCIAL de fechas, el caso probable: al menos
      `_MIN_NO_DATE_FOR_WARNING` descartes por falta de fecha Y el run no
      consiguió NI UN alta. Ej.: un rediseño rompe el JSON-LD solo en las
      páginas nuevas de schuljobs — las re-vistas traen fecha (el ERROR
      calla) pero todas las altas caen a `window_no_date` indefinidamente.
      Este WARNING grita desde el primer run.

    Residuales conocidos (registrados a propósito, sin arreglo): un fallo
    parcial que aún deje pasar ALGUNA alta no dispara nada — convertirlo en
    racha exigiría estado nuevo por fuente y queda para cuando haya datos;
    un fallo parcial con MENOS de `_MIN_NO_DATE_FOR_WARNING` altas sin fecha
    tampoco grita (el umbral que evita el falso positivo de la oferta
    perenne sin fecha también acalla al fallo pequeño de verdad); y una
    fuente con >= `_MIN_NO_DATE_FOR_WARNING` ofertas CRÓNICAMENTE sin fecha
    en su listado dará el WARNING en cada run tranquilo (sin altas).
    """
    if not settings.HARVEST_WINDOW_ENABLED:
        return
    if precheck.policy_mode != WINDOW:
        # A las FULL la ventana no les aplica: nada que rastrear ni vigilar.
        return
    total = len(precheck.verdicts)
    discarded = precheck.skipped_by_date + precheck.skipped_no_date
    if discarded > 0:
        logger.info(
            "%s: ventana de cosecha de %d días — descartadas %d altas de %d "
            "ofertas (%d sin fecha)",
            source_key,
            settings.HARVEST_WINDOW_DAYS,
            discarded,
            total,
            precheck.skipped_no_date,
        )
    if total < settings.HARVEST_ALERT_MIN_BATCH:
        # Lote minúsculo (early-stop que solo trae 1-2 novedades): un fallo
        # transitorio de parseo no es evidencia de política mal asignada ni
        # de fallo estructural del parser de fechas.
        return
    if not precheck.saw_published_at:
        logger.error(
            "Fuente '%s' con política WINDOW terminó un run en el que NINGUNA "
            "de sus %d ofertas traía published_at: su política está mal "
            "asignada (inventario obsoleto). Revisar su normalize_job o "
            "reclasificarla como FULL en services/harvest_window.py — y al "
            "reclasificarla, vaciar recent_identities de la fuente en "
            "source_cursors (SQL en el docstring del módulo): las "
            "identidades stale aprendidas impiden que el early-stop "
            "re-pagine sobre ellas.",
            source_key,
            total,
        )
    elif precheck.skipped_no_date >= _MIN_NO_DATE_FOR_WARNING and new_count == 0:
        logger.warning(
            "Fuente '%s': TODAS las altas de este run se han caído por falta "
            "de fecha (%d en window_no_date, 0 altas ingeridas). Si el "
            "detalle dejó de emitir published_at solo en las páginas nuevas, "
            "sus altas están a cero desde ya. Revisar el parser de fechas de "
            "la fuente.",
            source_key,
            precheck.skipped_no_date,
        )
