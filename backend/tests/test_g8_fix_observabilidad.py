"""G8/P2-2 — `identity_conflicts` volvía a hacer WARNING el ~85 % de las
corridas, y la medición que validó el fix de G7 no podía verlo.

Es la TERCERA vez consecutiva que la observabilidad se re-mide con un corpus
que no contiene la clave que el fix declara. `identity_conflicts` aparece en
0 de los 18 summaries del journal —igual que `identity_clones`, que G7 sí
retiró— pero su RASTRO sí está: la colisión de `ix_jobs_url` deja línea
literal y dispara en 11 de los 13 días con cosecha, entre 2 y 9 por día. Con
la clave dentro del conjunto de incidencias, el reparto volvía a
17 WARNING / 1 INFO.

Es la otra mitad del mismo fenómeno que `identity_clones` (el portal re-lista
con identidad nueva sobre corpus sin migrar) y su remediación es la misma
acción pendiente. La diferencia es que aquí SÍ hay pérdida —la oferta se
descarta—, así que no se retira sin más: entra por TASA, como `errors`.
"""

import logging

import pytest

from utils.fetch_diagnostics import (
    _SUMMARY_INCIDENT_KEYS,
    is_chronic,
    log_run_summary,
    mark_chronic,
)


class _Captura(logging.Handler):
    def __init__(self):
        super().__init__()
        self.nivel = None

    def emit(self, record):
        self.nivel = record.levelname


@pytest.fixture
def nivel_de():
    log = logging.getLogger("g8.obs.test")
    log.propagate = False
    log.setLevel(logging.INFO)
    cap = _Captura()
    log.handlers = [cap]

    def _nivel(summary: dict) -> str:
        cap.nivel = None
        log_run_summary(log, "run", summary)
        return cap.nivel

    yield _nivel
    log.handlers = []


def _run(**extra) -> dict:
    base = {
        "providers": 19,
        "fetched": 1227,
        "new": 40,
        "updated": 1100,
        "dupes": 5,
        "errors": 1,
        "unhealthy": [],
    }
    base.update(extra)
    return base


# Las colisiones de `ix_jobs_url` medidas en el journal del worker, por día.
# 11 de los 13 días con cosecha. Ninguna puede subir el nivel del run.
COLISIONES_MEDIDAS = [3, 9, 8, 5, 3, 3, 9, 6, 8, 9, 2]


class TestLaDerivaConocidaNoSubeElNivel:
    @pytest.mark.parametrize("conflictos", COLISIONES_MEDIDAS)
    def test_el_goteo_medido_deja_el_run_en_INFO(self, nivel_de, conflictos):
        """MORDIDA: con `identity_conflicts` dentro de
        `_SUMMARY_INCIDENT_KEYS`, los once salían WARNING."""
        assert nivel_de(_run(identity_conflicts=conflictos)) == "INFO"

    def test_la_clave_ya_no_esta_en_el_conjunto_de_incidencias(self):
        assert "identity_conflicts" not in _SUMMARY_INCIDENT_KEYS

    def test_la_linea_de_unhealthy_va_marcada_como_cronica(self, nivel_de):
        """El doble disparador: sacarla del conjunto no basta si su línea de
        `unhealthy` sigue contando como incidencia NUEVA."""
        entrada = mark_chronic("thehub: DERIVA DE IDENTIDAD — 9 ofertas descartadas")
        assert is_chronic(entrada)
        assert nivel_de(_run(identity_conflicts=9, unhealthy=[entrada])) == "INFO"


class TestUnEpisodioRealSIGrita:
    def test_la_deriva_masiva_sube_a_WARNING(self, nivel_de):
        """Los dos peores episodios del histórico fueron 143/199 (72 %) y
        128/304 (42 %). El 5 % los separa del goteo con holgura."""
        assert nivel_de(_run(fetched=199, identity_conflicts=143)) == "WARNING"
        assert nivel_de(_run(fetched=304, identity_conflicts=128)) == "WARNING"

    def test_con_cosecha_cero_cualquier_colision_es_material(self, nivel_de):
        assert nivel_de(_run(fetched=0, errors=0, identity_conflicts=1)) == "WARNING"

    def test_justo_por_encima_de_la_tasa(self, nivel_de):
        assert nivel_de(_run(fetched=1000, identity_conflicts=50)) == "INFO"
        assert nivel_de(_run(fetched=1000, identity_conflicts=51)) == "WARNING"


class TestLoQueYaFuncionabaSigueFuncionando:
    def test_un_run_limpio_es_INFO(self, nivel_de):
        assert nivel_de(_run()) == "INFO"

    def test_soft_time_limit_sigue_siendo_incidencia_estructural(self, nivel_de):
        """No se puede medir sobre el corpus (0 de 18) y se deja dentro con la
        medición escrita: los 8 `SoftTimeLimitExceeded` del journal son TODOS
        de `tasks.check_job_urls`, que no publica este summary."""
        assert "soft_time_limit" in _SUMMARY_INCIDENT_KEYS
        assert nivel_de(_run(soft_time_limit=1)) == "WARNING"

    def test_errors_por_encima_de_su_tasa_sigue_gritando(self, nivel_de):
        assert nivel_de(_run(fetched=0, errors=1143)) == "WARNING"

    def test_una_fuente_que_ACABA_de_degradarse_sigue_gritando(self, nivel_de):
        assert (
            nivel_de(_run(unhealthy=["proz: 3 runs seguidos con error"])) == "WARNING"
        )
