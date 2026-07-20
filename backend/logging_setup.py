"""Configuración central de logging.

La app no fijaba nivel de logging, así que los loggers propios (`services.*`,
`providers`, `tasks.*`, `apscheduler`) heredaban WARNING y sus mensajes INFO —el
scheduler, la cosecha diaria, etc.— no aparecían en los logs. `configure_logging`
fija el nivel del root desde `settings.LOG_LEVEL` de forma idempotente y robusta
(funciona aunque gunicorn/uvicorn ya hayan añadido handlers al root).
"""

import logging

from config import settings

# Loggers de la app que deben emitir al nivel configurado aunque otro proceso
# (gunicorn/uvicorn) haya tocado el root con un nivel distinto.
_APP_LOGGERS = ("services", "providers", "tasks", "apscheduler")


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
