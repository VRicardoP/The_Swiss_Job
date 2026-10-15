"""G6/P2-2 y G6/P3-6 — la credencial seguía saliendo por dos canales abiertos.

P2-2: `0308807` enumeró TRES destinos («`SourceHealth.last_error_detail`, el
cuerpo de la alerta y los logs del backend») y cerró los dos primeros. El
tercero no lo escribía `utils/http.py` sino el logger PROPIO de httpx, que a
nivel INFO emite la URL COMPLETA de cada petición: 32 líneas del journal del
worker llevaban una `GEMINI_API_KEY` real de 39 caracteres. Y el worker ni
siquiera llama a `configure_logging` — quien monta su logging es Celery.

P3-6: `fetch_rss(diag_url=…)` no tenía ningún llamante y, por defecto, publicaba
el token de la query en la columna de salud. La redacción de la query se hace
ahora en `fetch_diagnostics.record()`, que sí es la raíz.
"""

import logging

import pytest

from logging_setup import CredentialRedactingFilter, install_credential_redaction
from utils.redact import redact_credentials

_KEY_GEMINI = "AIzaSyF4K3-CL4V3-D3-G00GL3-D3-39-CH4R5"
_TOKEN_RSS = "T0K3N-S3CR3T0-D3L-F33D"


class TestRedaccionDeLaQuery:
    """El valor de un parámetro de credencial nunca se escribe entero."""

    @pytest.mark.parametrize(
        "url,secreto",
        [
            (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={_KEY_GEMINI}",
                _KEY_GEMINI,
            ),
            (
                "https://api.adzuna.com/v1/api/jobs/ch/search/1"
                "?app_id=APP1D-R34L&app_key=4PP-K3Y-R34L&results_per_page=50",
                "4PP-K3Y-R34L",
            ),
            (
                "https://public.api.careerjet.net/search?affid=4FF1D-R34L&keywords=x",
                "4FF1D-R34L",
            ),
            (f"https://feed.example/rss?token={_TOKEN_RSS}", _TOKEN_RSS),
        ],
    )
    def test_el_secreto_desaparece_y_el_nombre_del_parametro_queda(self, url, secreto):
        redactada = redact_credentials(url)
        assert secreto not in redactada
        assert "<redacted>" in redactada

    def test_no_se_come_parametros_inocentes(self):
        """`monkey=`/`donkey=` no terminan en `key=`/`id=` a ojos del patrón."""
        url = "https://x.example/a?monkey=banana&donkey=1&results_per_page=50"
        assert redact_credentials(url) == url


class TestElLoggerDeHttpxYaNoPublicaLaClave:
    """El canal que `0308807` decía cerrar y no tocaba."""

    def test_el_filtro_redacta_la_url_que_httpx_pasa_como_ARGUMENTO(self, caplog):
        """httpx emite `"HTTP Request: %s %s ..."` con la URL en `args`."""
        filtro = CredentialRedactingFilter()
        record = logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg='HTTP Request: %s %s "%s"',
            args=(
                "POST",
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={_KEY_GEMINI}",
                "HTTP/1.1 200 OK",
            ),
            exc_info=None,
        )
        assert filtro.filter(record) is True
        assert _KEY_GEMINI not in record.getMessage()
        assert "<redacted>" in record.getMessage()

    def test_httpx_queda_en_WARNING_asi_que_su_INFO_ni_se_emite(self):
        logging.getLogger("httpx").setLevel(logging.INFO)
        install_credential_redaction()
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

    def test_install_es_idempotente(self):
        root = logging.getLogger()
        install_credential_redaction()
        install_credential_redaction()
        filtros = [f for f in root.filters if isinstance(f, CredentialRedactingFilter)]
        assert len(filtros) == 1

    def test_el_worker_celery_engancha_la_redaccion(self):
        """El worker NO llama a `configure_logging`: lo hace por señal."""
        import celery_app  # noqa: F401  (importarlo es lo que conecta las señales)
        from celery.signals import after_setup_logger, after_setup_task_logger

        for señal in (after_setup_logger, after_setup_task_logger):
            receptores = [receptor for _clave, receptor in señal.receivers]
            assert install_credential_redaction in receptores
