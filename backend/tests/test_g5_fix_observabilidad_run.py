"""G5/P3-6 — los contadores del `summary` no los lee nadie aguas arriba.

Cinco ciclos de auditoría han ido añadiendo contadores (`identity_conflicts`,
`identity_clones`, `unhealthy`, `soft_time_limit`, `window_skipped`,
`window_no_date`) y NINGUNO tiene lector: `tasks/pipeline_tasks.py` encadena
con `.si()`, que no propaga el resultado, y no hay consumidor en `tasks/`,
`services/`, `routers/` ni en el frontend. Todo terminaba en un `logger.info`
— el mismo nivel con el que se anuncia un run perfecto.

Esto no fabrica el canal que falta (eso exige un consumidor real del payload de
resultado Celery). Lo que fija este test es que la línea de cierre del run
GRITE cuando hay algo que mirar, que es la única visibilidad efectiva que el
sistema tiene hoy.
"""

import logging

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
        "window_skipped": 12,  # el filtro FUNCIONANDO: no es incidencia
        "window_no_date": 0,
        "unhealthy": [],
    }


class TestElCierreDelRunGritaCuandoHayQueMirar:
    def test_un_run_limpio_sigue_en_INFO(self, caplog):
        logger = logging.getLogger("g5.runsummary")
        with caplog.at_level(logging.INFO, logger="g5.runsummary"):
            diag.log_run_summary(logger, "Fetch complete", _limpio())

        assert [r.levelno for r in caplog.records] == [logging.INFO]
        assert "INCIDENCIAS" not in caplog.text

    def test_window_skipped_solo_NO_es_incidencia(self, caplog):
        """Es el filtro de ventana funcionando, no una anomalía."""
        logger = logging.getLogger("g5.runsummary")
        s = _limpio()
        s["window_skipped"] = 900
        with caplog.at_level(logging.INFO, logger="g5.runsummary"):
            diag.log_run_summary(logger, "Fetch complete", s)

        assert [r.levelno for r in caplog.records] == [logging.INFO]

    def test_los_clones_de_identidad_elevan_el_nivel(self, caplog):
        """La rama MUDA de G5/P1-1: sin esto se anunciaba como un run normal."""
        logger = logging.getLogger("g5.runsummary")
        s = _limpio()
        s["identity_clones"] = 55
        with caplog.at_level(logging.INFO, logger="g5.runsummary"):
            diag.log_run_summary(logger, "Fetch complete", s)

        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert "identity_clones=55" in caplog.text

    def test_cada_contador_de_incidencia_eleva_el_nivel(self, caplog):
        """G6/P3-2 — `window_no_date` salió del conjunto: ver el test de G6.

        `errors` sigue aquí, pero con el valor MATERIAL (7 sobre 120
        cosechadas = 5,8 %); marginal ya no eleva el nivel.
        """
        logger = logging.getLogger("g5.runsummary")
        for clave, valor in (
            ("identity_conflicts", 3),
            ("identity_clones", 1),
            ("errors", 7),
            ("fetch_failed", 2),
            ("soft_time_limit", True),
        ):
            caplog.clear()
            s = _limpio()
            s[clave] = valor
            with caplog.at_level(logging.INFO, logger="g5.runsummary"):
                diag.log_run_summary(logger, "Fetch complete", s)

            assert [r.levelno for r in caplog.records] == [logging.WARNING], (
                f"{clave}={valor} se anuncia con el mismo nivel que un run limpio"
            )
            assert f"{clave}=" in caplog.text

    def test_unhealthy_no_vacio_eleva_el_nivel(self, caplog):
        logger = logging.getLogger("g5.runsummary")
        s = _limpio()
        s["unhealthy"] = ["arbeitnow: DERIVA DE IDENTIDAD — 55 ofertas"]
        with caplog.at_level(logging.INFO, logger="g5.runsummary"):
            diag.log_run_summary(logger, "Fetch complete", s)

        assert [r.levelno for r in caplog.records] == [logging.WARNING]
        assert "unhealthy=1" in caplog.text

    def test_el_summary_completo_sigue_en_la_linea(self, caplog):
        """No se pierde información: el dict entero viaja igual que antes."""
        logger = logging.getLogger("g5.runsummary")
        s = _limpio()
        s["errors"] = 1
        with caplog.at_level(logging.INFO, logger="g5.runsummary"):
            diag.log_run_summary(logger, "Fetch complete", s)

        assert "'fetched': 120" in caplog.text
