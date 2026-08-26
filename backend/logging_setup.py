"""Configuración central de logging.

La app no fijaba nivel de logging, así que los loggers propios (`services.*`,
`providers`, `tasks.*`, `apscheduler`) heredaban WARNING y sus mensajes INFO —el
scheduler, la cosecha diaria, etc.— no aparecían en los logs. `configure_logging`
fija el nivel del root desde `settings.LOG_LEVEL` de forma idempotente y robusta
(funciona aunque gunicorn/uvicorn ya hayan añadido handlers al root).

G6/P2-2 — además CIERRA el canal por el que los logs publicaban credenciales.
`0308807` enumeró tres destinos («`SourceHealth.last_error_detail`, el cuerpo de
la alerta y los logs del backend») y cerró los dos primeros; el tercero no lo
escribía `utils/http.py` sino el logger PROPIO de httpx, que a nivel INFO emite
la URL COMPLETA de cada petición. Medido en el journal del worker: 32 líneas con
una `GEMINI_API_KEY` real de 39 caracteres, sobre 13.446 líneas `HTTP Request:`.
Se cierra en la raíz —no parcheando cada llamada— por dos vías complementarias:
bajar httpx/httpcore a WARNING (su INFO es puro ruido: una línea por petición) y
un filtro de redacción en los handlers del root, que tapa el valor de cualquier
parámetro de credencial venga del logger que venga.

`install_credential_redaction` está SEPARADA de `configure_logging` porque las
líneas con la credencial no salían de la API sino del WORKER, y el worker nunca
llama a `configure_logging`: quien monta su logging es Celery. `celery_app` la
engancha a `after_setup_logger`/`after_setup_task_logger`, que se disparan
DESPUÉS de que Celery haya puesto sus handlers, y así el filtro aterriza sobre
los handlers reales sin tocar el nivel que el worker trae por `-l`.

G7/P2-1 — ese razonamiento era correcto y su ALCANCE no: el filtro se instalaba
en el root y en nada más, y los loggers con `propagate=False` y handler propio
—`uvicorn.access`, `celery.task`— quedaban fuera. Por el primero salía en claro
el JWT de sesión de cada conexión SSE. `install_credential_redaction` recorre
ahora todos los loggers vivos con handler propio.
"""

import logging
import traceback

from config import settings
from utils.redact import has_placeholder_credential, redact_credentials

# Loggers de la app que deben emitir al nivel configurado aunque otro proceso
# (gunicorn/uvicorn) haya tocado el root con un nivel distinto.
_APP_LOGGERS = ("services", "providers", "tasks", "apscheduler")

# Librerías cuyo INFO es una línea por petición con la URL entera (credenciales
# en la query incluidas). No aportan nada que no diga ya `utils.http`.
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore")


def _redact_arg(value: object) -> object:
    """Redacta un argumento de log mirando su REPRESENTACIÓN, no su tipo.

    G7/P3-7a: filtrar por `isinstance(a, str)` dejaba pasar el caso que el
    código auditado usa de verdad — `utils/http.py:246` y `:266` pasan el objeto
    excepción, no `str(exc)`, y `logging` lo convierte con `str()` DESPUÉS del
    filtro. Los escalares se devuelven intactos: no pueden llevar credencial y
    convertirlos rompería un `%d`/`%f`. Y si la redacción no cambia nada se
    devuelve el objeto ORIGINAL, para no alterar cómo lo formatea un `%r`.
    """
    if isinstance(value, str):
        return redact_credentials(value)
    if value is None or isinstance(value, (int, float, complex)):
        return value
    texto = str(value)
    redactado = redact_credentials(texto)
    return redactado if redactado != texto else value


class CredentialRedactingFilter(logging.Filter):
    """Tapa el valor de los parámetros de credencial de CUALQUIER registro.

    Se aplica sobre `record.msg` y `record.args` por separado —antes de que el
    formateador los una— porque los loggers de terceros pasan la URL como
    argumento (`httpx` emite `"HTTP Request: %s %s ..."` con la URL en `args`).
    Muta el registro, así que la redacción alcanza a todos los handlers.

    G7/P3-7a: el traceback NO pasa por `msg` ni por `args` — lo formatea el
    `Formatter` desde `record.exc_info`, así que un secreto en el `str()` de la
    excepción salía entero en la línea siguiente al mensaje ya redactado. Se
    precalcula `record.exc_text` (que es justo lo que el `Formatter` reutiliza
    si ya está puesto) con el traceback redactado.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            if record.args and has_placeholder_credential(record.msg):
                # El NOMBRE del parámetro está en la plantilla y el VALOR llega
                # en los argumentos (`logger.info("... key=%s", clave)`):
                # redactar las dos piezas por separado no tapa nada, porque
                # ninguna contiene por sí sola la forma `nombre=valor`. Se
                # formatea aquí y se tapa el resultado. Es el ÚNICO caso en que
                # se pierde la estructura `msg`/`args` del registro, y no
                # alcanza a `uvicorn.access` —cuya plantilla no nombra ninguna
                # credencial— cuyo formateador sí desempaqueta `record.args`.
                try:
                    formateado = record.getMessage()
                except Exception:
                    # G8/P3-2: `getMessage()` es `msg % args` y una plantilla
                    # con args de más o de menos lanza `TypeError`. Las
                    # excepciones de un `Filter` NO las captura `logging`
                    # (`handleError` solo envuelve `emit`), así que subían hasta
                    # el `logger.info(...)` del código de negocio e invertían el
                    # contrato «logging nunca revienta a su llamante»: antes del
                    # fix ese mismo error lo absorbía `logging` (registro
                    # descartado + traza por stderr). Se cae al camino normal,
                    # que redacta plantilla y argumentos por separado y deja que
                    # `logging` haga lo de siempre con el formateo roto.
                    record.msg = redact_credentials(record.msg, plantilla=True)
                else:
                    record.msg = redact_credentials(formateado)
                    record.args = ()
            else:
                # `plantilla` solo cuando queda un `%`/`{` por sustituir: si no
                # hay args, un valor que empieza por `%` es percent-encoding y
                # hay que taparlo (G8/P3-1).
                record.msg = redact_credentials(record.msg, plantilla=bool(record.args))
        if isinstance(record.args, tuple):
            record.args = tuple(_redact_arg(a) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: _redact_arg(v) for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_credentials(
                "".join(traceback.format_exception(*record.exc_info))
            ).rstrip("\n")
        if isinstance(record.stack_info, str):
            record.stack_info = redact_credentials(record.stack_info)
        return True


def _enganchar_en_handlers(target: logging.Logger | None) -> None:
    """Cuelga el filtro de cada handler de `target` que aún no lo tenga."""
    for handler in getattr(target, "handlers", ()):
        if not any(isinstance(f, CredentialRedactingFilter) for f in handler.filters):
            handler.addFilter(CredentialRedactingFilter())


def install_credential_redaction(*_args, **_kwargs) -> None:
    """Redacta credenciales en todo lo que se loguee y calla el INFO de httpx.

    Idempotente y sin efectos sobre los niveles de la app: la llaman tanto
    `configure_logging` (API) como las señales de Celery (worker). Acepta y
    descarta argumentos porque las señales de Celery pasan `logger`, `loglevel`,
    `format`… como kwargs.

    G7/P2-1 — instalarlo solo en el root NO basta. Un `logging.Filter` colgado
    de un *logger* corre únicamente para lo que se loguea directamente en él; a
    los registros que suben de un hijo solo los ve el filtro de los HANDLERS. Y
    dos loggers que llevan credenciales no suben al root:

    * `uvicorn.access`, con `propagate=False` y `StreamHandler` propio porque
      `uvicorn` aplica su `dictConfig` ANTES de importar `main.py`. Por él sale
      el **JWT de sesión** de cada conexión SSE, que viaja en la query string
      porque `EventSource` no puede mandar cabeceras
      (`routers/notifications.py`);
    * `celery.task` (G7/P3-7b), el logger canónico del worker, que
      `after_setup_task_logger` entregaba en el kwarg `logger=` que esta función
      descartaba.

    Se recorren, por eso, TODOS los loggers vivos con handler propio. Los que se
    creen DESPUÉS de esta llamada quedan fuera; los dos citados existen ya
    cuando se ejecuta (uvicorn por su `dictConfig`, `celery.task` porque la
    señal se dispara tras montar los handlers).
    """
    root = logging.getLogger()
    if not any(isinstance(f, CredentialRedactingFilter) for f in root.filters):
        root.addFilter(CredentialRedactingFilter())
    _enganchar_en_handlers(root)
    for obj in list(logging.root.manager.loggerDict.values()):
        if isinstance(obj, logging.Logger):
            _enganchar_en_handlers(obj)

    for name in _NOISY_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def configure_logging() -> None:
    """Fija el nivel de logging del root y de los loggers de la app."""
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    # basicConfig no haría nada si el root ya tiene handlers (caso gunicorn),
    # así que garantizamos al menos un handler nosotros.
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)

    for name in _APP_LOGGERS:
        logging.getLogger(name).setLevel(level)

    install_credential_redaction()
