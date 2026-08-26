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
    """Registra un fallo DEFINITIVO de descarga (no un reintento intermedio)."""
    current = _issues.get()
    if current is None:
        return
    current.append(
        FetchIssue(
            kind=kind, url=url, status=status, detail=detail, root_cause=root_cause
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


# Claves de `summary` que son INCIDENCIAS: si traen valor, el run no fue
# limpio. Se declaran aquí, junto al resto del vocabulario de diagnóstico de la
# cosecha, para que añadir un contador nuevo sea una línea y no un olvido.
_SUMMARY_INCIDENT_KEYS = (
    "identity_conflicts",
    "identity_clones",
    "errors",
    "fetch_failed",
    "soft_time_limit",
    "window_no_date",
)


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
    """
    incidencias = [
        f"{k}={summary[k]}"
        for k in _SUMMARY_INCIDENT_KEYS
        if summary.get(k)  # 0, False y ausente son «sin incidencia»
    ]
    if summary.get("unhealthy"):
        incidencias.append(f"unhealthy={len(summary['unhealthy'])}")

    if incidencias:
        run_logger.warning(
            "%s con INCIDENCIAS (%s): %s", label, ", ".join(incidencias), summary
        )
    else:
        run_logger.info("%s: %s", label, summary)
