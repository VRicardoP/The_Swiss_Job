"""G7/P2-1 y G7/P3-7 — la redacción de credenciales tenía la instalación
acotada al root y tres huecos mecánicos en el propio filtro.

P2-1: `install_credential_redaction` colgaba el filtro de `root.filters` y de
`root.handlers`, y de nada más. Un `logging.Filter` puesto en un *logger* solo
corre para lo que se loguea directamente en él; a lo que sube desde un hijo solo
lo ve el filtro de los HANDLERS. `uvicorn.access` no sube: `uvicorn` aplica su
`dictConfig` antes de importar `main.py` y le deja `propagate=False` con
`StreamHandler` propio. Y por ahí sale el **JWT de sesión** de cada conexión
SSE, que viaja en la query string porque `EventSource` no puede mandar cabeceras
(`routers/notifications.py`). Reproducido contra el servicio en marcha.

P3-7a: el traceback lo formatea el `Formatter` desde `record.exc_info`, que el
filtro no tocaba; y un objeto pasado como argumento (el patrón de
`utils/http.py:246`, que pasa `exc` y no `str(exc)`) se convertía con `str()`
DESPUÉS del filtro.

P3-7b: la señal `after_setup_task_logger` entregaba `celery.task` —el logger
canónico del worker, también con handler propio— en un kwarg `logger=` que la
función descartaba.

P3-7c: el patrón solo entendía `nombre=valor` sin guion interno, así que
`api-key=` y `x-api-key=` (el transporte real de `providers/jsearch.py`) se le
escapaban, y el docstring afirmaba cubrir formas que no cubría.

Y un defecto que ninguna de las dos cosas anteriores predecía, medido aquí: el
patrón tapaba también el `%s` de una plantilla sin formatear, con lo que el
`msg % args` posterior reventaba, `logging` DESCARTABA la línea entera y volcaba
los argumentos —el secreto incluido— por stderr.
"""

import io
import logging

import pytest

from logging_setup import CredentialRedactingFilter, install_credential_redaction
from utils.redact import redact_credentials

_JWT = "eyJhbGciOiJIUzI1NiJ9.G7-JWT-DE-SESION-REAL.firma"
_CLAVE = "S3CR3T0-D3-PR0V1D3R"


def _logger_aislado(nombre: str) -> tuple[logging.Logger, io.StringIO]:
    """Logger con handler propio y `propagate=False` — la forma de uvicorn/celery."""
    logger = logging.getLogger(nombre)
    logger.handlers.clear()
    logger.filters.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger, buffer


@pytest.fixture(autouse=True)
def root_limpio():
    """Devuelve el root a su estado previo: `install_*` lo muta de verdad."""
    root = logging.getLogger()
    filtros, handlers = list(root.filters), list(root.handlers)
    yield root
    root.filters[:] = filtros
    root.handlers[:] = handlers


class TestLosLoggersQueNoPropaganTambienSeRedactan:
    """P2-1 y P3-7b: instalar solo en el root deja fuera a quien tiene handler propio."""

    @pytest.mark.parametrize("nombre", ["uvicorn.access", "celery.task"])
    def test_el_filtro_aterriza_en_el_handler_del_logger_aislado(self, nombre):
        logger, _ = _logger_aislado(nombre)
        install_credential_redaction()
        assert any(
            isinstance(f, CredentialRedactingFilter) for f in logger.handlers[0].filters
        ), f"{nombre} tiene handler propio y el filtro no llegó a él"

    def test_el_jwt_de_la_conexion_sse_no_sale_en_el_log_de_acceso(self):
        logger, buffer = _logger_aislado("uvicorn.access")
        install_credential_redaction()
        logger.info(
            '%s - "%s %s HTTP/%s" %d',
            "172.18.0.1",
            "GET",
            f"/api/v1/notifications/stream?token={_JWT}",
            "1.1",
            401,
        )
        salida = buffer.getvalue()
        assert _JWT not in salida
        assert "token=<redacted>" in salida

    def test_el_logger_canonico_de_celery_tampoco_publica_la_clave(self):
        logger, buffer = _logger_aislado("celery.task")
        install_credential_redaction(logger=logger, loglevel=logging.INFO, format="x")
        logger.warning("fallo pidiendo https://a.io/x?key=%s", _CLAVE)
        salida = buffer.getvalue()
        assert _CLAVE not in salida
        assert "key=<redacted>" in salida

    def test_es_idempotente_y_no_apila_filtros(self):
        logger, _ = _logger_aislado("uvicorn.access")
        install_credential_redaction()
        install_credential_redaction()
        cuantos = sum(
            isinstance(f, CredentialRedactingFilter) for f in logger.handlers[0].filters
        )
        assert cuantos == 1


class TestElTracebackYLosArgumentosNoStr:
    """P3-7a: `exc_info` y un objeto pasado como argumento esquivaban el filtro."""

    def test_el_traceback_sale_redactado(self):
        logger, buffer = _logger_aislado("probe.g7.exc")
        install_credential_redaction()
        try:
            raise RuntimeError(f"fallo pidiendo https://a.io/x?key={_CLAVE}")
        except RuntimeError as exc:
            logger.error("peticion fallida: %s", exc, exc_info=True)
        salida = buffer.getvalue()
        assert "Traceback" in salida, "el test debe estar mirando un traceback real"
        assert _CLAVE not in salida
        assert salida.count("<redacted>") >= 2

    def test_una_excepcion_como_argumento_se_redacta(self):
        logger, buffer = _logger_aislado("probe.g7.arg")
        install_credential_redaction()
        # El patrón real de utils/http.py:246 — pasa `exc`, no `str(exc)`.
        logger.warning("reintento: %s", ValueError(f"https://a.io/x?key={_CLAVE}"))
        assert _CLAVE not in buffer.getvalue()

    def test_los_escalares_siguen_formateandose_como_numeros(self):
        logger, buffer = _logger_aislado("probe.g7.num")
        install_credential_redaction()
        logger.info("reintentos=%d ratio=%.2f", 3, 0.5)
        assert buffer.getvalue().strip() == "reintentos=3 ratio=0.50"


class TestLaPlantillaSinFormatearNoEsUnaCredencial:
    """Tapar el `%s` de `key=%s` rompía el `msg % args` y perdía la línea entera."""

    def test_el_registro_sobrevive_y_el_valor_se_redacta(self):
        logger, buffer = _logger_aislado("probe.g7.plantilla")
        install_credential_redaction()
        logger.info("llamando con key=%s", _CLAVE)
        salida = buffer.getvalue()
        assert salida.strip(), "logging descartó el registro: el %s se había tapado"
        assert _CLAVE not in salida
        assert "key=<redacted>" in salida

    @pytest.mark.parametrize("hueco", ["%s", "%(clave)s", "{token}"])
    def test_los_huecos_de_plantilla_se_dejan_intactos(self, hueco):
        # G8/P3-1: la guarda sigue existiendo, pero solo se aplica al texto que
        # ES una plantilla pendiente de formatear. Aplicada a TODO texto dejaba
        # escapar ENTERA una credencial percent-encoded cuyo primer carácter es
        # reservado (`?key=%2Fsecreto`), y `%2F` no se puede distinguir de un
        # especificador de formato por su forma: es uno válido en Python.
        assert redact_credentials(f"key={hueco}", plantilla=True) == f"key={hueco}"


class TestLasFormasQueElPatronNoEntendia:
    """P3-7c: guion interno, cuerpo JSON, cabecera, `Bearer` y userinfo."""

    @pytest.mark.parametrize(
        "texto",
        [
            "https://a.io/x?api-key=" + _CLAVE,
            "https://a.io/x?x-api-key=" + _CLAVE,
            "x-rapidapi-key: " + _CLAVE,
            '{"api_key": "' + _CLAVE + '", "page": 1}',
            "{'app_key': '" + _CLAVE + "'}",
            "Authorization: Bearer " + _CLAVE,
            f"http://usuario:{_CLAVE}@proxy.io:8080",
            "https://a.io/token?client_secret=" + _CLAVE,
        ],
    )
    def test_la_credencial_no_sobrevive(self, texto):
        redactado = redact_credentials(texto)
        assert _CLAVE not in redactado
        assert "<redacted>" in redactado

    @pytest.mark.parametrize(
        "texto",
        [
            "https://a.io/x?monkey=noesnada&turkey=tampoco",
            "Como token independiente, la seniority se elimina",
            "CHF 80 000 - CHF 100 000 par an",
            "https://a.io/x?page=1&limit=50",
        ],
    )
    def test_no_destroza_texto_legitimo(self, texto):
        assert redact_credentials(texto) == texto
