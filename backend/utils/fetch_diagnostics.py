"""Diagnóstico de fetch: distingue "falló la descarga" de "no hay ofertas".

Problema que resuelve (V.0): los helpers de `utils.http` devuelven `None` cuando
agotan reintentos, y los providers traducen ese `None` a `return []`. El pipeline
recibía una lista vacía y la contaba como ÉXITO, así que un 404, un 403 y un feed
legítimamente vacío eran indistinguibles. Nueve fuentes estuvieron 66 días mudas
sin que saltara nada (ver ESTADO_Y_HOJA_DE_RUTA.md §3.3).

Diseño: los helpers HTTP REGISTRAN el fallo en un contexto de ejecución; el
pipeline lo lee al terminar cada fuente. Se usa `contextvars` a propósito, para
NO cambiar la firma de `fetch_jobs()` en los 25 providers y sus scrapers: el
acoplamiento se mantiene en una sola dirección (http → contexto → pipeline).

Cada tarea asyncio hereda una COPIA del contexto en su creación, así que los
fetches concurrentes del pipeline (`asyncio.gather`) no se pisan entre sí
siempre que `begin()` se llame DENTRO de la tarea de cada fuente.
"""

import logging
from contextvars import ContextVar
from dataclasses import dataclass

from utils.redact import redact_credentials

logger = logging.getLogger(__name__)

# Clases de fallo. `http_error` cubre 4xx/5xx tras agotar reintentos;
# `network_error` cubre timeouts y errores de conexión/parseo.
KIND_HTTP = "http_error"
KIND_NETWORK = "network_error"


@dataclass(frozen=True)
class FetchIssue:
    """Un fallo de descarga concreto, ya definitivo (reintentos agotados).

    `root_cause` (r4/H5): marca el issue que EXPLICA el run — hoy solo lo usa
    el detector de soft-blocks de BaseScraper: el fallo estructural que el
    parser registró ANTES sobre ese mismo HTML de challenge es su síntoma.
    `source_health._summarize` prefiere el primer issue marcado como cabecera
    del resumen; el resto de issues no cambia (default False). Es un flag
    tipado y no un prefijo de texto en `detail`: un detail ajeno que empezara
    igual no puede secuestrar la cabecera.
    """

    kind: str
    url: str
    status: int | None = None
    detail: str = ""
    root_cause: bool = False

    def describe(self) -> str:
        """Texto corto para logs y para la columna de salud."""
        if self.status is not None:
            return f"HTTP {self.status} en {self.url}"
        return f"{self.detail or self.kind} en {self.url}"


# None = no hay recolección activa (p.ej. una llamada suelta desde un test o un
# script). En ese caso `record()` no hace nada: no se cambia el comportamiento
# de quien no participa del pipeline.
_issues: ContextVar[list[FetchIssue] | None] = ContextVar("fetch_issues", default=None)


def begin() -> None:
    """Abre una recolección para la fuente que se va a descargar.

    Llamar DENTRO de la tarea asyncio de cada fuente, justo antes de
    `fetch_jobs()`. Descarta lo que hubiera: cada run empieza limpio.
    """
    _issues.set([])


def record(
    kind: str,
    url: str,
    status: int | None = None,
    detail: str = "",
    root_cause: bool = False,
) -> None:
    """Registra un fallo DEFINITIVO de descarga (no un reintento intermedio).

    G6/P3-6 — la URL y el detalle se REDACTAN aquí, en la raíz de verdad. Lo que
    `record` guarda acaba en `SourceHealth.last_error_detail` (que el panel
    muestra) y en el cuerpo de la alerta de fuente caída, y hasta ahora solo se
    salvaba de publicar la credencial quien se acordara de pasar `diag_url` —
    «alguien tiene que acordarse» es la fragilidad que ese parámetro venía a
    evitar, desplazada del sitio de la fuga al sitio de la llamada.
    `diag_url` sigue siendo necesario para la credencial en el PATH (jooble),
    que ningún patrón de query puede reconocer.
    """
    current = _issues.get()
    if current is None:
        return
    current.append(
        FetchIssue(
            kind=kind,
            url=redact_credentials(url),
            status=status,
            detail=redact_credentials(detail),
            root_cause=root_cause,
        )
    )


def issues() -> list[FetchIssue]:
    """Fallos registrados desde el último `begin()`. Lista vacía si no hubo."""
    return list(_issues.get() or [])


def json_items(data, url: str, source: str, *, key: str | None = None) -> list | None:
    """Lista de ofertas de un cuerpo JSON, o `None` si el run debe CORTAR.

    G4/P2-8 — un HTTP 200 cuyo cuerpo no sabemos leer (`{}`, `[]` donde se
    esperaba un envoltorio, la clave renombrada) NO es «no hay ofertas»: hasta
    ahora los providers lo cortaban con `if not data: break` y la fuente salía
    `empty`, indistinguible de un feed legítimamente vacío. El día que
    ostjob/zentraljob cambien su 308 por un 200 con cuerpo vacío volverían a
    morir en silencio — exactamente la clase V.0 que cerró el fix de los seis
    RSS de G3/P2-6.

    Contrato:
    - `data is None` ⇒ `None` SIN registrar: el fetch falló y el issue ya lo
      puso `utils.http` (no duplicar).
    - estructura reconocible ⇒ la lista, aunque esté vacía (fin de paginación
      o feed sin resultados: eso sí es legítimo y no se registra).
    - la clave PRESENTE con valor `null` ⇒ lista vacía, sin registrar (ver
      abajo).
    - cualquier otra cosa ⇒ se REGISTRA el fallo de estructura y `None`.

    G5/P3-4 — el caso `{clave: null}` tenía DOS contratos opuestos dentro del
    mismo commit: `careerjet` lo trataba como vacío legítimo (su `or []`, con
    el comentario de G3/P2-6 que lo declara así) y los otros nueve providers
    pasaron a declararlo fallo de estructura al adoptar este helper — 9 de 11
    cambiaron de `empty` a `error`. Se elige UN contrato y se escribe: la clave
    presente con `null` es «esta página no trae ofertas», que es lo que los
    `or []` originales de los providers sugieren que alguien vio. La clave
    AUSENTE o de tipo equivocado sigue siendo fallo de estructura — que es el
    caso que G4/P2-8 quería cazar (la clave renombrada).

    `key=None` para los endpoints cuyo cuerpo es la lista directamente.
    """
    if data is None:
        return None
    if key is None:
        if isinstance(data, list):
            return data
        forma = f"se esperaba una lista y llegó {type(data).__name__}"
    elif isinstance(data, dict):
        items = data.get(key)
        if isinstance(items, list):
            return items
        if items is None and key in data:
            # Clave PRESENTE con `null`: fin de paginación, no fallo.
            return []
        forma = (
            f"la clave {key!r} falta"
            if items is None
            else f"la clave {key!r} llegó como {type(items).__name__}"
        )
    else:
        forma = f"se esperaba un objeto con {key!r} y llegó {type(data).__name__}"

    detail = f"{source}: 200 con estructura desconocida — {forma}"
    logger.error(detail)
    record(KIND_NETWORK, url, detail=detail)
    return None


def classify(job_count: int, collected: list[FetchIssue]) -> str:
    """Veredicto del run de una fuente: `ok` | `empty` | `error`.

    - `error`: hubo fallos de descarga Y no se obtuvo NADA. Es el caso que antes
      se disfrazaba de éxito.
    - `ok`: trajo ofertas. Si además hubo fallos (p.ej. una página de N cayó),
      sigue siendo `ok` pero el detalle queda registrado — es degradación parcial,
      no una fuente muerta.
    - `empty`: cero ofertas SIN ningún fallo. La fuente respondió y no tenía nada,
      que es un estado legítimo y distinto de `error`.
    """
    if job_count > 0:
        return "ok"
    return "error" if collected else "empty"


# Marca de una incidencia CRÓNICA: la que YA venía avisando en el run anterior.
# Se conserva en el texto de `unhealthy` —el operador la sigue viendo— pero no
# sube el nivel del run: un aviso que lleva ocho corridas sonando no es «algo
# que mirar hoy».
CHRONIC_MARK = "[crónico]"


def mark_chronic(motivo: str) -> str:
    """Marca un motivo de `unhealthy` como ya avisado en el run anterior."""
    return f"{CHRONIC_MARK} {motivo}"


def is_chronic(entrada: str) -> bool:
    """¿Esta entrada de `unhealthy` es un aviso que ya venía sonando?"""
    return CHRONIC_MARK in entrada


# Claves de `summary` que son INCIDENCIA ESTRUCTURAL: si traen valor, algo dejó
# de funcionar. Se declaran aquí, junto al resto del vocabulario de diagnóstico
# de la cosecha, para que añadir un contador nuevo sea una línea y no un olvido.
#
# G6/P3-2 — de aquí salieron `errors` y `window_no_date`. NO son incidencias
# estructurales sino RUIDO DE CAUDAL: `errors` (fallos por-oferta) trae valor en
# 33 de 34 runs reales y `window_no_date` en todos los que cosechan, con valores
# de 10 a 23. Con ellos dentro, los 34 runs reales salían WARNING y NINGUNO
# INFO: el nivel dejó de discriminar y la observabilidad seguía terminando en
# una línea indistinguible de un run perfecto, solo que un peldaño más arriba.
#
# G7/P2-4 — y de aquí salieron `identity_clones` y `fetch_failed`. La medición
# que validó el fix de G6 no PODÍA verlos: los 34 summaries del journal son
# anteriores al código que emite esas claves (`identity_clones` aparece en 0 de
# 34) y con ellas dentro el reparto vuelve a 34 WARNING / 0 INFO. Medido contra
# producción, SOLO LECTURA:
#
# * `identity_clones` es una TASA, no una avería: 14 de 14 días con valor, entre
#   el 2,0 % y el 19,2 % de las altas del día (10-87 clones sobre 125-1100).
#   Es una condición estructural conocida, con remediación pendiente (los dos
#   scripts de canonización + la migración `b3c7d1a95e42`). Sigue publicándose
#   en el `summary` y en su propia línea de `unhealthy`, marcada como crónica.
#   Cuando la canonización corra, el contador debe colapsar y volver aquí.
# * `fetch_failed` tiene su calibración —más estricta y POR FUENTE— en
#   `services/source_health` (`SOURCE_HEALTH_ERROR_STREAK`), exactamente el
#   mismo argumento con el que G6 sacó `window_no_date`. Con él dentro, las
#   fuentes crónicas (`proz` y `remoteco` llevan 8 runs seguidos con error) lo
#   mantienen por encima de 0 para siempre. Se pierden como mucho dos runs de
#   antelación sobre un fallo NUEVO, que a la tercera entra por `unhealthy`.
_SUMMARY_INCIDENT_KEYS = (
    "identity_conflicts",
    "soft_time_limit",
)

# `errors` sí sube a WARNING cuando deja de ser marginal: son fallos POR OFERTA
# y su valor normal es un puñado sobre más de mil. Por encima de esta fracción
# de lo cosechado —o con cosecha CERO, donde no hay caudal contra el que
# relativizar— el run se rompió de verdad: son los seis runs de
# `errors=1143..1223` con `fetched=0` que hay en el journal.
_ERROR_RATE_WARNING = 0.05

# `window_no_date` NO entra ni con umbral: la calibración ya existe en el módulo
# que lo produce (`services/harvest_window._MIN_NO_DATE_FOR_WARNING = 3`, que
# además exige `new_count == 0`), es POR FUENTE y es más estricta. Duplicarla
# aquí sobre el contador agregado —que trae de 10 a 23 en cada run que
# cosecha— solo reproduce el WARNING permanente que este fix quita.


def _errors_are_material(summary: dict) -> bool:
    """¿Los fallos por-oferta dejaron de ser marginales en este run?

    Marginal = un puñado sobre lo que se llegó a cosechar. Con `fetched` en 0 y
    errores, no hay caudal contra el que relativizar: el run se rompió.
    """
    errors = summary.get("errors") or 0
    if not errors:
        return False
    fetched = summary.get("fetched") or 0
    return errors > fetched * _ERROR_RATE_WARNING


def log_run_summary(run_logger, label: str, summary: dict) -> None:
    """Cierra el run con su `summary`, en el NIVEL que le corresponde.

    G5/P3-6 — los cinco ciclos de auditoría han ido añadiendo contadores al
    `summary` (`identity_conflicts`, `identity_clones`, `unhealthy`,
    `soft_time_limit`, `window_skipped`, `window_no_date`) y **ninguno tiene
    lector aguas arriba**: `tasks/pipeline_tasks.py` encadena con `.si()`, que
    NO propaga el resultado, y no hay ningún consumidor en `tasks/`,
    `services/`, `routers/` ni en el frontend. Toda esa observabilidad
    terminaba en un `logger.info` — el mismo nivel con el que se anuncia un run
    perfecto, y por tanto invisible entre el ruido.

    Esto no fabrica el canal que falta (eso exige un consumidor de verdad del
    payload de resultado Celery); lo que hace es que la línea de cierre GRITE
    cuando hay algo que mirar: WARNING si algún contador de incidencia trae
    valor o si `unhealthy` no está vacío, INFO cuando el run fue limpio. Es la
    misma cota que ya tenía el `logger.error` por-oferta, elevada al run.

    G6/P3-2 — el nivel cambió pero la SEÑAL no: sobre los 34 summaries reales
    del journal del worker, la primera versión daba **34 WARNING y 0 INFO**,
    porque `errors` y `window_no_date` estaban en el conjunto y traen valor casi
    siempre. Un WARNING que sale en el 100 % de los runs discrimina exactamente
    igual que el INFO al que sustituyó. Ahora el conjunto son las incidencias
    ESTRUCTURALES, `errors` entra solo por encima de `_ERROR_RATE_WARNING` de lo
    cosechado, y `window_no_date` no entra: tiene su propio aviso calibrado y
    por fuente en `harvest_window`.

    G7/P2-4 — y ese 26/8 se midió sobre un corpus que no podía refutarlo: tres
    de las cuatro claves del conjunto aparecen en 0 de los 34 summaries del
    journal, porque son ANTERIORES al código que las emite. Con la clave que el
    código sí emite hoy (`identity_clones`, presente en 14 de 14 días) el
    reparto volvía a 34 WARNING / 0 INFO. Además, los dos disparadores que
    quedaban —`fetch_failed` y `unhealthy`— son PEGAJOSOS por construcción:
    `source_health` avisa por «N runs seguidos», un contador monótono que no se
    limpia hasta que la fuente se arregla. La regla es ahora que el run GRITA
    cuando hay algo NUEVO: incidencia estructural, `errors` por encima de su
    tasa, o una fuente que ACABA de cruzar su umbral de racha. Lo crónico se
    publica igual, marcado, sin subir el nivel.
    """
    incidencias = [
        f"{k}={summary[k]}"
        for k in _SUMMARY_INCIDENT_KEYS
        if summary.get(k)  # 0, False y ausente son «sin incidencia»
    ]
    if _errors_are_material(summary):
        incidencias.append(f"errors={summary['errors']}")
    # G7/P2-4: solo las fuentes que ACABAN de degradarse. Las crónicas siguen
    # en el `summary` y en el texto de la línea, pero no suben el nivel.
    nuevas = [e for e in summary.get("unhealthy") or () if not is_chronic(e)]
    if nuevas:
        incidencias.append(f"unhealthy={len(nuevas)}")

    if incidencias:
        run_logger.warning(
            "%s con INCIDENCIAS (%s): %s", label, ", ".join(incidencias), summary
        )
    else:
        run_logger.info("%s: %s", label, summary)
