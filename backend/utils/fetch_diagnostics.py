"""Diagnóstico de fetch: distingue "falló la descarga" de "no hay ofertas".

Problema que resuelve (V.0): los helpers de `utils.http` devuelven `None` cuando
agotan reintentos, y los providers traducen ese `None` a `return []`. El pipeline
recibía una lista vacía y la contaba como ÉXITO, así que un 404, un 403 y un feed
legítimamente vacío eran indistinguibles. Nueve fuentes estuvieron 66 días mudas
sin que saltara nada (ver ESTADO_Y_HOJA_DE_RUTA.md §3.3).

Diseño: los helpers HTTP REGISTRAN el fallo en un contexto de ejecución; el
pipeline lo lee al terminar cada fuente. Se usa `contextvars` a propósito, para
NO cambiar la firma de `fetch_jobs()` en los 28 providers y sus scrapers: el
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
    """Un fallo de descarga concreto, ya definitivo (reintentos agotados)."""

    kind: str
    url: str
    status: int | None = None
    detail: str = ""

    def describe(self) -> str:
        """Texto corto para logs y para la columna de salud."""
        if self.status is not None:
            return f"HTTP {self.status} en {self.url}"
        return f"{self.detail or self.kind} en {self.url}"


# None = no hay recolección activa (p.ej. una llamada suelta desde un test o un
# script). En ese caso `record()` no hace nada: no se cambia el comportamiento
# de quien no participa del pipeline.
_issues: ContextVar[list[FetchIssue] | None] = ContextVar(
    "fetch_issues", default=None
)


def begin() -> None:
    """Abre una recolección para la fuente que se va a descargar.

    Llamar DENTRO de la tarea asyncio de cada fuente, justo antes de
    `fetch_jobs()`. Descarta lo que hubiera: cada run empieza limpio.
    """
    _issues.set([])


def record(kind: str, url: str, status: int | None = None, detail: str = "") -> None:
    """Registra un fallo DEFINITIVO de descarga (no un reintento intermedio)."""
    current = _issues.get()
    if current is None:
        return
    current.append(FetchIssue(kind=kind, url=url, status=status, detail=detail))


def issues() -> list[FetchIssue]:
    """Fallos registrados desde el último `begin()`. Lista vacía si no hubo."""
    return list(_issues.get() or [])


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
