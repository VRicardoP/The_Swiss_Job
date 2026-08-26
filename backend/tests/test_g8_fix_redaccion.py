"""G8/P3-1, P3-2 y P3-3 — las tres cotas mecánicas de la redacción de secretos.

El canal que motivó la redacción está cerrado (verificado EN VIVO por G8: el
JWT de sesión sale `<redacted>` en `uvicorn.access` y el censo de loggers no
deja un handler descubierto). Lo que queda son tres cotas sin productor
alcanzable HOY, que este fichero cierra y fija:

P3-1: una credencial percent-encoded cuyo primer carácter es reservado
      (`+`, `/`, `=`) escapaba ENTERA — la guarda del `%s` no distingue un
      hueco de plantilla de un `%2F`. Fuga TOTAL, no parcial.
P3-2: el filtro llamaba a `record.getMessage()` y las excepciones de un
      `Filter` no las captura `logging`: un `logger.info()` con args de más o
      de menos reventaba el código de negocio. Invertía el contrato «logging
      nunca revienta a su llamante».
P3-3: el separador `:` aceptado en cualquier posición de cualquier prosa —y un
      `Bearer|Basic` con mínimo de 4 caracteres— mutilaban logs legítimos.
"""

import logging

import httpx
import pytest

from logging_setup import CredentialRedactingFilter
from utils.redact import redact_credentials


class TestPercentEncodingNoEscapa:
    """P3-1 — `httpx` percent-codifica los valores de query."""

    @pytest.mark.parametrize(
        "secreto", ["ab/cd+ef", "+leading", "/leading", "=leading", "AIzaSyD-normal_1"]
    )
    def test_el_secreto_sale_tapado_venga_como_venga(self, secreto):
        url = str(httpx.URL("https://a.io/x", params={"key": secreto}))
        redactada = redact_credentials(url)
        assert "<redacted>" in redactada
        assert secreto.replace("/", "%2F") not in redactada

    def test_la_firma_base64_tambien(self):
        """`signature`/`sig`/`hmac` están en la lista precisamente porque son
        base64: ~3 % empiezan por `+` o `/`."""
        url = str(httpx.URL("https://a.io/x", params={"signature": "/abc+def="}))
        assert "<redacted>" in redact_credentials(url)

    def test_un_hueco_de_plantilla_SIGUE_intacto(self):
        """La guarda no desaparece: se traslada al único sitio donde tiene
        sentido, el `record.msg` con argumentos pendientes."""
        assert redact_credentials("... key=%s ...", plantilla=True) == "... key=%s ..."
        assert redact_credentials("... token={t} ...", plantilla=True).endswith(
            "{t} ..."
        )

    def test_y_sin_plantilla_ese_mismo_texto_SI_se_tapa(self):
        assert redact_credentials("... key=%2Fsecreto ...") == "... key=<redacted> ..."


class TestElFiltroNoRevientaASuLlamante:
    """P3-2."""

    @pytest.mark.parametrize(
        "msg,args",
        [
            ("faltan %s %s", ("uno",)),
            ("sobran %s", ("uno", "dos")),
            ("token=%s y %(falta)s", ("x",)),
        ],
    )
    def test_una_plantilla_rota_no_sube_al_codigo_de_negocio(self, msg, args):
        registro = logging.LogRecord("x", logging.INFO, "f", 1, msg, args, None)
        assert CredentialRedactingFilter().filter(registro) is True

    def test_una_plantilla_buena_se_sigue_formateando_y_tapando(self):
        registro = logging.LogRecord(
            "x", logging.INFO, "f", 1, "url key=%s fin", ("AIzaSyD-secreto",), None
        )
        CredentialRedactingFilter().filter(registro)
        assert registro.getMessage() == "url key=<redacted> fin"


class TestLaProsaLegitimaSobrevive:
    """P3-3 — el `:` solo en las dos formas que tienen sink."""

    @pytest.mark.parametrize(
        "texto",
        [
            "auth: failed for user pepe",
            "Basic auth failed, retrying",
            "primary key: id no puede ser NULL",
            "secret: no compartir",
            "Bearer token independiente en la frase",
        ],
    )
    def test_no_se_mutila(self, texto):
        assert redact_credentials(texto) == texto

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("{'title':'x','key':'arbeitnow-123'}", "{'title':'x','key':'<redacted>'}"),
            ('{"api_key": "abc123"}', '{"api_key": "<redacted>"}'),
            ("x-rapidapi-key: 9f8a7b6c5d4e3f2a", "x-rapidapi-key: <redacted>"),
            (
                "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
                "Authorization: Bearer <redacted>",
            ),
        ],
    )
    def test_las_formas_con_sink_siguen_tapadas(self, texto, esperado):
        assert redact_credentials(texto) == esperado

    def test_el_query_string_sigue_siendo_el_caso_principal(self):
        assert (
            redact_credentials("https://a.io/x?key=AIzaSyD-secreto_1234567890")
            == "https://a.io/x?key=<redacted>"
        )

    def test_la_redaccion_sigue_siendo_punto_fijo(self):
        """Pasar por el filtro del root y por el de un handler no degrada."""
        una = redact_credentials("key=secreto123")
        assert redact_credentials(una) == una == "key=<redacted>"
