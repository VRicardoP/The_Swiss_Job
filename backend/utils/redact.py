"""Redacción de credenciales que viajan en la *query string* de una URL.

Por qué existe (G6/P2-2 y G6/P3-6). El proyecto ya redactaba la credencial que
va en el PATH (`providers/jooble.py` pasa su `diag_url` a `utils.http`), pero la
que va en la QUERY —`?key=`, `?app_key=`, `?affid=`…— seguía saliendo entera por
dos canales:

* el logger propio de **httpx**, que a nivel INFO emite la URL COMPLETA de cada
  petición: 32 líneas del journal del worker llevaban una `GEMINI_API_KEY` real
  de 39 caracteres, y `adzuna`/`careerjet` saldrían por la misma línea en cuanto
  se configuren;
* `utils.fetch_diagnostics.record()`, que publica la URL tal cual en
  `SourceHealth.last_error_detail` (visible en el panel) salvo que el llamante
  se acuerde de pasar `diag_url` — y `fetch_rss` no tiene ningún llamante que lo
  pase.

La redacción vive AQUÍ, en un único sitio, y se aplica en las dos raíces: el
filtro de logging (todos los loggers, propios y de terceros) y `record()`. No se
parchea cada llamada: «alguien tiene que acordarse» es exactamente la fragilidad
que se está cerrando.
"""

import re

# Nombres de parámetro cuyo VALOR es una credencial. Se comparan sin distinguir
# mayúsculas y cubren los que el proyecto usa hoy (gemini/google `key`, adzuna
# `app_id`+`app_key`, careerjet `affid`, jooble/jsearch/rapidapi) más los
# habituales, para que añadir un provider no reabra el canal.
_CREDENTIAL_PARAMS = (
    "key",
    "api_key",
    "apikey",
    "app_key",
    "app_id",
    "appid",
    "affid",
    "token",
    "access_token",
    "auth",
    "secret",
    "password",
    "signature",
)

# `(?<![\w-])` evita que `monkey=` o `x-key=` casen por su sufijo. El valor se
# corta en el primer separador de query (`&`), de comillas o de espacio, así que
# el resto de la línea de log sobrevive intacto.
_QUERY_CREDENTIAL_RE = re.compile(
    r"(?<![\w-])(" + "|".join(_CREDENTIAL_PARAMS) + r")=([^&\s\"'<>]+)",
    re.IGNORECASE,
)

REDACTED = "<redacted>"


def redact_credentials(text: str) -> str:
    """Sustituye el valor de todo parámetro de credencial por `<redacted>`.

    Conserva el nombre del parámetro: el operador sigue viendo QUÉ credencial
    intervenía sin que el secreto quede escrito.
    """
    return _QUERY_CREDENTIAL_RE.sub(rf"\1={REDACTED}", text)
