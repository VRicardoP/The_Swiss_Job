"""G7/P2-4 — la medición que validó `log_run_summary` no podía refutarlo.

Las cifras del commit `1fda4e5` (34 WARNING / 0 INFO con `5bfece0`, 26/8 con
HEAD) se reproducen EXACTAS sobre los 34 summaries del journal del worker. Lo
que no puede ese corpus es refutar el fix: tres de las cuatro claves que el
conjunto declara como su nueva señal aparecen en **0 de 34**, porque los
summaries son ANTERIORES al código que las emite. Con la clave que el código sí
emite hoy —`identity_clones`, medida contra producción en 14 de 14 días, entre
el 2,0 % y el 19,2 % de las altas— el reparto vuelve a **34 WARNING / 0 INFO**.

Y los dos disparadores que quedaban son PEGAJOSOS por construcción:
`source_health` avisa por «N runs seguidos», un contador monótono que no se
limpia hasta que la fuente se arregla (`proz` y `remoteco` llevan 8;
`swiss_schools_inspired/zis/isb` llevan 8 sin traer ofertas). Un aviso crónico no
es «algo que mirar hoy».

La regla es ahora que el run GRITA cuando hay algo NUEVO. Medido sobre los 34
summaries reales, con `identity_clones` inyectado a la tasa medida y las
entradas de `unhealthy` marcadas como las marcaría el código de hoy:
HEAD 34 WARNING / 0 INFO → 18 WARNING / 16 INFO, y los 18 son runs con
`fetched=0`, con tasa de error material, o el día exacto en que una fuente cruzó
su umbral.
"""

import logging
import types

import pytest

from config import settings
from services.source_health import _fetch_alert, _storage_alert
from utils import fetch_diagnostics as diag


def _limpio() -> dict:
    return {
        "providers": 3,
        "fetched": 120,
        "new": 40,
        "errors": 0,
        "fetch_failed": 0,
        "identity_conflicts": 0,
        "identity_clones": 0,
        "soft_time_limit": False,
        "window_skipped": 12,
        "window_no_date": 0,
        "unhealthy": [],
    }


def _nivel(caplog, summary: dict) -> int:
    logger = logging.getLogger("g7.runsummary")
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="g7.runsummary"):
        diag.log_run_summary(logger, "Fetch complete", summary)
    return caplog.records[-1].levelno


def _fila(**kwargs) -> types.SimpleNamespace:
    base = {
        "consecutive_errors": 0,
        "consecutive_empty": 0,
        "consecutive_unstored": 0,
        "last_error_detail": None,
    }
    base.update(kwargs)
    return types.SimpleNamespace(**base)


class TestElRuidoCronicoYaNoSubeElNivel:
    """Las dos claves que traen valor en TODAS las corridas reales."""

    @pytest.mark.parametrize(
        "clave,valor", [("identity_clones", 87), ("fetch_failed", 5)]
    )
    def test_por_si_solas_no_hacen_WARNING(self, caplog, clave, valor):
        s = _limpio()
        s[clave] = valor
        assert _nivel(caplog, s) == logging.INFO

    @pytest.mark.parametrize(
        "clave,valor", [("identity_clones", 87), ("fetch_failed", 5)]
    )
    def test_pero_el_dato_se_sigue_publicando(self, caplog, clave, valor):
        s = _limpio()
        s[clave] = valor
        _nivel(caplog, s)
        assert f"'{clave}': {valor}" in caplog.text

    def test_la_corrida_tipica_de_hoy_sale_INFO(self, caplog):
        """87 clones y 5 fuentes crónicas: exactamente el 2026-08-25 real."""
        s = _limpio()
        s.update(fetched=812, new=812, identity_clones=87, fetch_failed=5, errors=4)
        s["unhealthy"] = [
            diag.mark_chronic("proz: 8 runs seguidos con error — último: HTTP 403"),
            diag.mark_chronic("remoteco: 8 runs seguidos con error"),
            diag.mark_chronic("swiss_schools_isb: 8 runs seguidos sin traer ofertas"),
            diag.mark_chronic("arbeitnow: DERIVA DE IDENTIDAD — 87 ofertas"),
        ]
        assert _nivel(caplog, s) == logging.INFO


class TestLoQueSiSigueGritando:
    """Sin esto el fix no discrimina: silencia el ruido, no la señal."""

    @pytest.mark.parametrize(
        "clave,valor", [("identity_conflicts", 3), ("soft_time_limit", True)]
    )
    def test_las_incidencias_estructurales(self, caplog, clave, valor):
        s = _limpio()
        s[clave] = valor
        assert _nivel(caplog, s) == logging.WARNING

    def test_una_tasa_de_error_material(self, caplog):
        s = _limpio()
        s["errors"] = 7  # 7 sobre 120 = 5,8 %
        assert _nivel(caplog, s) == logging.WARNING

    def test_el_run_que_no_cosecho_nada(self, caplog):
        s = _limpio()
        s.update(fetched=0, errors=1223)
        assert _nivel(caplog, s) == logging.WARNING

    def test_una_fuente_que_ACABA_de_degradarse(self, caplog):
        s = _limpio()
        s["unhealthy"] = [
            diag.mark_chronic("proz: 8 runs seguidos con error"),
            "ostjob: 3 runs seguidos con error — último: HTTP 308",
        ]
        assert _nivel(caplog, s) == logging.WARNING
        assert "unhealthy=1" in caplog.text, "solo la NUEVA cuenta para el nivel"


class TestLaMarcaDeCronicaLaPoneQuienSabeLaRacha:
    """`source_health` conoce la racha y el umbral: la marca se decide ahí."""

    def test_al_cruzar_el_umbral_es_noticia(self):
        motivo = _fetch_alert(
            _fila(consecutive_errors=settings.SOURCE_HEALTH_ERROR_STREAK)
        )
        assert motivo is not None
        assert not diag.is_chronic(motivo)

    def test_a_partir_de_ahi_es_cronica(self):
        motivo = _fetch_alert(
            _fila(consecutive_errors=settings.SOURCE_HEALTH_ERROR_STREAK + 1)
        )
        assert diag.is_chronic(motivo)

    def test_por_debajo_del_umbral_no_hay_aviso(self):
        assert _fetch_alert(_fila(consecutive_errors=1)) is None

    def test_la_racha_de_vacios_sigue_la_misma_regla(self):
        umbral = settings.SOURCE_HEALTH_EMPTY_STREAK
        assert not diag.is_chronic(_fetch_alert(_fila(consecutive_empty=umbral)))
        assert diag.is_chronic(_fetch_alert(_fila(consecutive_empty=umbral + 8)))

    def test_la_racha_de_no_guardados_tambien(self):
        umbral = settings.SOURCE_HEALTH_UNSTORED_STREAK
        assert not diag.is_chronic(_storage_alert(_fila(consecutive_unstored=umbral)))
        assert diag.is_chronic(_storage_alert(_fila(consecutive_unstored=umbral + 1)))

    def test_el_texto_del_motivo_sobrevive_a_la_marca(self):
        motivo = _fetch_alert(_fila(consecutive_errors=8, last_error_detail="HTTP 403"))
        assert "8 runs seguidos con error" in motivo
        assert "HTTP 403" in motivo


class TestElRepartoSobreLosSummariesQueElCodigoDeHoyEmite:
    """La mordida del P2-4: medir con un corpus que SÍ puede refutar el fix."""

    def _reparto(self, summaries: list[dict]) -> dict[str, int]:
        logger = logging.getLogger("g7.reparto")
        logger.propagate = False
        logger.handlers.clear()
        niveles: list[str] = []

        class _Captura(logging.Handler):
            def emit(self, record):
                niveles.append(record.levelname)

        logger.addHandler(_Captura())
        logger.setLevel(logging.DEBUG)
        for s in summaries:
            diag.log_run_summary(logger, "Fetch complete", s)
        return {n: niveles.count(n) for n in set(niveles)}

    def test_catorce_corridas_cronicas_no_producen_catorce_WARNING(self):
        """Las 14 tasas medidas contra producción, una por día."""
        clones = [87, 78, 52, 43, 35, 53, 20, 58, 44, 24, 16, 20, 24, 10]
        altas = [812, 782, 784, 788, 734, 530, 403, 1100, 641, 955, 630, 993, 125, 181]
        summaries = []
        for n, a in zip(clones, altas):
            s = _limpio()
            s.update(fetched=a, new=a, identity_clones=n, fetch_failed=5)
            s["unhealthy"] = [diag.mark_chronic("proz: 8 runs seguidos con error")]
            summaries.append(s)
        assert self._reparto(summaries) == {"INFO": 14}

    def test_el_dia_que_algo_se_rompe_de_verdad_sigue_gritando(self):
        s = _limpio()
        s.update(fetched=812, new=812, identity_clones=87, fetch_failed=6)
        s["unhealthy"] = [
            diag.mark_chronic("proz: 8 runs seguidos con error"),
            f"ostjob: {settings.SOURCE_HEALTH_ERROR_STREAK} runs seguidos con error",
        ]
        assert self._reparto([s]) == {"WARNING": 1}
