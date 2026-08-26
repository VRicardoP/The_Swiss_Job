"""Redacción de credenciales en cualquier texto que vaya a un log o a la BD.

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

G7/P3-7c — el patrón entendía SOLO `nombre=valor` sin guion, y el docstring
afirmaba cubrir formas que no cubría. Corregido y ampliado a las tres que sí
tienen (o pueden tener) un sink en este backend:

* `nombre=valor` en query string, y ahora también con guion interno
  (`api-key=`, `x-api-key=`, `x-rapidapi-key=` — el lookbehind `(?<![\\w-])`
  anterior los descartaba justo por el guion);
* `"nombre": "valor"` / `{'nombre': 'valor'}` / `Nombre: valor` — el cuerpo JSON
  de una respuesta de error, que `utils/http.py:212` vuelca hasta 500 caracteres
  para CUALQUIER no-200, y la forma de cabecera (`x-rapidapi-key`, el transporte
  real de `providers/jsearch.py`);
* `Authorization: Bearer <token>` (`CORE_CONSUMER_KEY` está configurada) y el
  `usuario:secreto@host` de una URL con userinfo (`SCRAPER_PROXY_URL`).

Lo que NO se cubre, y se dice para no repetir la afirmación falsa del docstring
anterior: la credencial que viaja en el PATH sin nombre (jooble:
`/api/<clave>`). Es indistinguible de un segmento de ruta cualquiera y se sigue
tapando en origen, con el `diag_url` que el provider ya pasa.

FALSO POSITIVO CONOCIDO Y ACEPTADO (G8/P3-3, medido). Barrido sobre el journal
vivo —18.025 líneas de `worker`, `backend` y `worker-ai`—: **36 líneas
cambian**, y el desglose es
    31  banner de arranque de Celery: `.> ai   exchange=ai(direct) key=ai`
     3  JWT de sesión en `uvicorn.access` (`?token=…`)   <- redacción CORRECTA
     2  `GEMINI_API_KEY` en la URL de httpx (`?key=…`)   <- redacción CORRECTA
La *routing key* de una cola no es una credencial, pero es la forma
`nombre=valor` y no hay manera de distinguirla de una `app_id` de adzuna, que
son 8 caracteres igual que `scraping`. Se acepta porque no pierde información:
la misma línea del banner sigue mostrando el nombre de la cola DOS veces
(`.> ai` y `exchange=ai(direct)`). Un umbral de longitud sí taparía la
diferencia, y dejaría fuera las credenciales cortas: no se pone.
"""

import functools
import re

# Nombres de parámetro cuyo VALOR es una credencial. Se comparan sin distinguir
# mayúsculas y cubren los que el proyecto usa hoy en query (gemini/google `key`,
# adzuna `app_id`+`app_key`, careerjet `affid`) y en cabecera (`x-rapidapi-key`
# de jsearch, que casa por el sufijo `key`), más los habituales, para que añadir
# un provider no reabra el canal.
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
    "refresh_token",
    "session_id",
    "auth",
    "secret",
    "client_secret",
    "consumer_key",
    "password",
    "passwd",
    "signature",
    "sig",
    "hmac",
)
_NOMBRES = "|".join(_CREDENTIAL_PARAMS)

REDACTED = "<redacted>"

# `(?<!\w)` evita que `monkey=` case por su sufijo, pero —a diferencia del
# `(?<![\w-])` anterior— SÍ deja pasar `x-api-key=`: el guion no es parte de
# otra palabra, es el separador de un nombre de cabecera que también es
# credencial.
_BORDE = r"(?<!\w)"
# El nombre puede venir entrecomillado (JSON/`dict` repr) y el separador puede
# ser `=` (query) o `:` (JSON y cabeceras). La comilla del valor se cierra
# simétricamente con `(?P=q)` para no tragarse el resto de la línea.
_VALOR = r"(?P<q>[\"']?)(?P<sec>[^&\s\"'<>,;}\\]+)(?P=q)"
# Forma QUERY: `key=…`, `x-api-key=…`. El separador `=` no aparece en prosa
# entre un nombre de credencial y una palabra, así que aquí no hace falta más.
_KV_EQ_RE = re.compile(
    _BORDE + r"(?P<q1>[\"']?)(?:" + _NOMBRES + r")(?P=q1)\s*=\s*" + _VALOR,
    re.IGNORECASE,
)
# G8/P3-3 — el separador `:` ya NO se acepta en cualquier posición de cualquier
# prosa. Medido sobre el journal vivo (18.025 líneas de `worker`, `backend` y
# `worker-ai`), el patrón de G7 mutilaba mensajes legítimos:
#   'auth: failed for user pepe'        -> 'auth: <redacted> for user pepe'
#   'primary key: id no puede ser NULL' -> 'primary key: <redacted> no puede…'
#   'secret: no compartir'              -> 'secret: <redacted> compartir'
# y `fetch_diagnostics.record()` —que redacta lo que va a
# `SourceHealth.last_error_detail`, VISIBLE en el panel— es justo el canal por
# el que un error legítimo con `auth:` o `key:` llegaría mutilado.
#
# Se conservan las DOS formas que sí tienen (o pueden tener) un sink:
#   1) JSON/`dict`, con el nombre ENTRECOMILLADO — el cuerpo de error que
#      `utils/http.py` vuelca hasta 500 caracteres para cualquier no-200;
#   2) CABECERA con guion (`x-api-key:`, `x-rapidapi-key:`), que exige al menos
#      un guion precisamente porque la prosa no escribe eso nunca. Hoy no hay
#      sink de cabeceras (barrido: 0 resultados), así que es puramente
#      preventiva y por eso se le pone la forma más estrecha que la cubre.
_KV_JSON_RE = re.compile(
    r"(?P<q1>[\"'])(?:" + _NOMBRES + r")(?P=q1)\s*:\s*" + _VALOR,
    re.IGNORECASE,
)
_KV_HEADER_RE = re.compile(
    r"^[ \t]*[A-Za-z]\w*(?:-\w+)*-(?:" + _NOMBRES + r")[ \t]*:[ \t]*" + _VALOR,
    re.IGNORECASE | re.MULTILINE,
)
# `Authorization: Bearer …` y `Basic …`. El esquema se conserva: el operador
# sigue viendo QUÉ mecanismo intervenía. NO se incluye el esquema `Token`: es
# además una palabra corriente en prosa castellana e inglesa («token
# independiente») y taparía la palabra siguiente de cualquier mensaje.
# G8/P3-3: el mínimo sube de 4 a `_MIN_TOKEN_BEARER`. Con 4, «Basic auth
# failed, retrying» salía «Basic <redacted> failed, retrying»: la palabra
# siguiente a un esquema de autenticación no es el token. Un token real
# (JWT, base64 de `usuario:secreto`, clave de consumer) no baja de esa
# longitud; una palabra de prosa no la alcanza.
_MIN_TOKEN_BEARER = 16
_BEARER_RE = re.compile(
    r"\b(?:Bearer|Basic)\s+(?P<sec>[A-Za-z0-9._~+/=-]{"
    + str(_MIN_TOKEN_BEARER)
    + r",})",
)
# userinfo de una URL: `scheme://usuario:secreto@host`. Solo se tapa la parte
# que sigue a los dos puntos — el usuario no es el secreto.
_USERINFO_RE = re.compile(r"//[^/\s:@]+:(?P<sec>[^@\s/]+)@")

_KV_PATRONES = (_KV_EQ_RE, _KV_JSON_RE, _KV_HEADER_RE)
_PATRONES = (*_KV_PATRONES, _BEARER_RE, _USERINFO_RE)


def _tapar(match: re.Match, *, respetar_huecos: bool) -> str:
    """Sustituye SOLO el tramo del grupo `sec`, dejando el resto literal.

    Trabajar por posiciones en vez de por plantilla de reemplazo evita tener que
    reconstruir comillas, separadores y espacios de cada forma, que es donde se
    cuelan los errores.
    """
    # Un `%s`/`{token}` NO es una credencial: es el hueco de una plantilla que
    # todavía no se ha formateado. Taparlo rompía el `msg % args` posterior y
    # `logging` descartaba la línea entera volcando los args —el secreto— por
    # stderr. Medido: `logger.info("... key=%s", clave)` perdía el registro.
    #
    # G8/P3-1 — pero esa guarda solo tiene sentido sobre una PLANTILLA que
    # todavía se va a formatear, y se aplicaba a TODO texto. `httpx`
    # percent-codifica los valores de query, así que un secreto cuyo primer
    # carácter es reservado (`+`, `/`, `=`) sale como `%2Bleading` / `%2Fleading`
    # y escapaba ENTERO — fuga total, no parcial, y alcanza a
    # `signature`/`sig`/`hmac`, que están en la lista precisamente porque son
    # base64 (~3 % de los secretos base64 empiezan por `+` o `/`). Distinguir
    # `%s` de `%2F` por su forma es imposible —`%2F` es un especificador de
    # formato válido en Python—, así que la decisión se toma donde SÍ se sabe:
    # el llamante dice si el texto es una plantilla pendiente de formatear.
    if respetar_huecos and match.group("sec").startswith(("%", "{")):
        return match.group(0)
    ini, fin = match.span("sec")
    inicio = match.start()
    return match.group(0)[: ini - inicio] + REDACTED + match.group(0)[fin - inicio :]


def has_placeholder_credential(text: str) -> bool:
    """¿Hay un nombre de credencial cuyo VALOR es un hueco sin formatear?

    Es la señal de que la credencial NO está en la plantilla sino en los
    argumentos —`logger.info("... key=%s", clave)`—, el único caso en que
    redactar la plantilla y los argumentos por separado no puede tapar nada.
    """
    return any(
        m.group("sec").startswith(("%", "{"))
        for patron in _KV_PATRONES
        for m in patron.finditer(text)
    )


def redact_credentials(text: str, *, plantilla: bool = False) -> str:
    """Sustituye el valor de toda credencial reconocible por `<redacted>`.

    Conserva el nombre del parámetro: el operador sigue viendo QUÉ credencial
    intervenía sin que el secreto quede escrito.

    `plantilla=True` SOLO cuando el texto es un `record.msg` con argumentos
    pendientes de sustituir: ahí un valor que empieza por `%` o `{` es un hueco
    de formato y taparlo rompería el `msg % args`. En cualquier otro texto —un
    mensaje ya formateado, un argumento suelto, una URL— un valor que empieza
    por `%` es percent-encoding y SÍ hay que taparlo (G8/P3-1).
    """
    tapar = functools.partial(_tapar, respetar_huecos=plantilla)
    for patron in _PATRONES:
        text = patron.sub(tapar, text)
    return text
