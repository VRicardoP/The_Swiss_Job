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
"""

import logging

from config import settings
from utils.redact import redact_credentials

# Loggers de la app que deben emitir al nivel configurado aunque otro proceso
# (gunicorn/uvicorn) haya tocado el root con un nivel distinto.
_APP_LOGGERS = ("services", "providers", "tasks", "apscheduler")

# Librerías cuyo INFO es una línea por petición con la URL entera (credenciales
# en la query incluidas). No aportan nada que no diga ya `utils.http`.
_NOISY_HTTP_LOGGERS = ("httpx", "httpcore")


class CredentialRedactingFilter(logging.Filter):
    """Tapa el valor de los parámetros de credencial de CUALQUIER registro.

    Se aplica sobre `record.msg` y `record.args` por separado —antes de que el
    formateador los una— porque los loggers de terceros pasan la URL como
    argumento (`httpx` emite `"HTTP Request: %s %s ..."` con la URL en `args`).
    Muta el registro, así que la redacción alcanza a todos los handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_credentials(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_credentials(a) if isinstance(a, str) else a for a in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                k: redact_credentials(v) if isinstance(v, str) else v
                for k, v in record.args.items()
            }
        return True


def install_credential_redaction(*_args, **_kwargs) -> None:
    """Redacta credenciales en todo lo que se loguee y calla el INFO de httpx.

    Idempotente y sin efectos sobre los niveles de la app: la llaman tanto
    `configure_logging` (API) como las señales de Celery (worker). Acepta y
    descarta argumentos porque las señales de Celery pasan `logger`, `loglevel`,
    `format`… como kwargs.
    """
    root = logging.getLogger()
    if not any(isinstance(f, CredentialRedactingFilter) for f in root.filters):
        root.addFilter(CredentialRedactingFilter())
    for handler in root.handlers:
        if not any(isinstance(f, CredentialRedactingFilter) for f in handler.filters):
            handler.addFilter(CredentialRedactingFilter())

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
