"""G6/P3-2 — `log_run_summary` cambió el nivel pero no la señal.

G5 elevó a WARNING la línea de cierre del run «cuando hay algo que mirar».
Ejecutada sobre los **34 summaries REALES** extraídos del journal del worker, la
función daba **34 WARNING y 0 INFO**: `errors` (fallos por-oferta) trae valor en
33 de 34 runs porque es el estado normal del pipeline, y `window_no_date` en
todos los que cosechan, con valores de 10 a 23. Un WARNING que sale en el 100 %
de los runs discrimina exactamente igual que el INFO al que sustituyó.

Con el fix, los mismos 34 runs se reparten **26 WARNING / 8 INFO**, y los 8 INFO
son runs con 1-5 errores sobre 176-1257 ofertas, sin `fetch_failed` y sin
`unhealthy`: runs sanos de verdad.
"""

import logging

import pytest

from utils import fetch_diagnostics as diag

# Cuatro summaries REALES del journal del worker, verbatim.
_RUN_ROTO = {  # los seis primeros del journal: no cosechó NADA
    "providers": 19,
    "fetched": 0,
    "new": 0,
    "updated": 0,
    "dupes": 0,
    "errors": 1143,
}
_RUN_SANO = {  # cosecha normal: 1 error sobre 1230
    "providers": 19,
    "fetched": 1230,
    "new": 993,
    "updated": 237,
    "dupes": 0,
    "errors": 1,
}
_RUN_SANO_SCRAPERS = {
    "scrapers": 15,
    "skipped": 0,
    "fetched": 219,
    "new": 180,
    "updated": 39,
    "dupes": 0,
    "errors": 3,
    "fetch_failed": 0,
    "window_skipped": 2,
    "window_no_date": 10,  # 10 descartes por falta de fecha: caudal, no avería
    "unhealthy": [],
}
_RUN_CON_FUENTES_CAIDAS = {
    "providers": 16,
    "fetched": 1208,
    "new": 446,
    "updated": 761,
    "dupes": 1,
    "errors": 1,
    "fetch_failed": 3,
    "window_skipped": 36,
    "window_no_date": 0,
    "unhealthy": ["remoteco: 3 runs seguidos con error"],
}


def _nivel(caplog, summary: dict) -> int:
    logger = logging.getLogger("g6.runsummary")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="g6.runsummary"):
        diag.log_run_summary(logger, "Fetch complete", summary)
    return caplog.records[0].levelno


class TestElNivelVuelveADiscriminar:
    def test_un_run_sano_con_errores_marginales_es_INFO(self, caplog):
        """1 error sobre 1.230 ofertas no es una incidencia del run."""
        assert _nivel(caplog, _RUN_SANO) == logging.INFO

    def test_window_no_date_ya_no_eleva_el_nivel(self, caplog):
        """Tiene su propio aviso, por fuente y más estricto, en harvest_window."""
        assert _nivel(caplog, _RUN_SANO_SCRAPERS) == logging.INFO

    def test_un_run_que_no_cosecho_nada_sigue_siendo_WARNING(self, caplog):
        """errors=1143 con fetched=0: no hay caudal contra el que relativizar."""
        assert _nivel(caplog, _RUN_ROTO) == logging.WARNING

    def test_las_fuentes_caidas_siguen_elevando_el_nivel(self, caplog):
        assert _nivel(caplog, _RUN_CON_FUENTES_CAIDAS) == logging.WARNING

    @pytest.mark.parametrize("clave,valor", [("soft_time_limit", True)])
    def test_las_incidencias_ESTRUCTURALES_siguen_gritando(self, caplog, clave, valor):
        assert _nivel(caplog, {**_RUN_SANO, clave: valor}) == logging.WARNING

    def test_la_deriva_de_identidad_pasa_a_entrar_por_TASA(self, caplog):
        """G8/P2-2 — `identity_conflicts=3` sobre 1.230 cosechadas es el goteo
        conocido (dispara en 11 de los 13 días con cosecha del journal), y como
        incidencia estructural devolvía el reparto a 17 WARNING / 1 INFO. El
        episodio real —42 % y 72 % en los dos peores del histórico— sí grita."""
        assert _nivel(caplog, {**_RUN_SANO, "identity_conflicts": 3}) == logging.INFO
        assert (
            _nivel(caplog, {**_RUN_SANO, "identity_conflicts": 99}) == logging.WARNING
        )


class TestElUmbralDeErrores:
    def test_justo_por_debajo_del_umbral_no_eleva(self, caplog):
        """5 % de 1.000 = 50; 50 no supera 50."""
        assert _nivel(caplog, {"fetched": 1000, "errors": 50}) == logging.INFO

    def test_justo_por_encima_si(self, caplog):
        assert _nivel(caplog, {"fetched": 1000, "errors": 51}) == logging.WARNING

    def test_cero_errores_nunca_eleva(self, caplog):
        assert _nivel(caplog, {"fetched": 0, "errors": 0}) == logging.INFO
