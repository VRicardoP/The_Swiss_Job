"""G7/P3-5 y G7/P3-6 — dos afirmaciones de `deduplicator.py` que los datos no
sostenían, y un invariante que no hacía cumplir nada.

P3-5 — el invariante de `fuzzy_hash`.

`jobs.fuzzy_hash` es un valor derivado que se PERSISTE y solo se refresca cuando
el portal re-lista la oferta. Cambiar `compute_fuzzy_hash` —o cualquiera de sus
dos normalizaciones— parte el corpus en dos algoritmos: las DOS consultas que
comparan un hash recién calculado contra los almacenados
(`find_fuzzy_duplicate`, `find_same_source_clone`) dejan de ver las filas
viejas, y nada deja rastro de la partición.

Ese «DEBE ejecutar el backfill» estaba escrito en dos docstrings y en la
cabecera del script, y no lo hacía cumplir ni un test, ni un hook, ni una
migración, ni el CI. Este fichero lo convierte en un fallo de la suite.

Los cuatro pares fijan las dos normalizaciones que ya cambiaron una vez —y que
explican, medidas contra producción, las 40 filas desfasadas no degeneradas:
26 por `_DIVERSITY_RE` y 14 por el filtrado de seniority por token— más la
guarda de identidad degenerada de G3/P2-12.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.deduplicator import Deduplicator

_AVISO = (
    "La fórmula de `fuzzy_hash` ha cambiado. Es un valor PERSISTIDO: ejecuta "
    "`scripts/g6_backfill_fuzzy_hash.py --apply` (o mete el UPDATE en la misma "
    "migración) y actualiza los hashes de este test. Sin eso, las filas ya "
    "almacenadas quedan invisibles para `find_fuzzy_duplicate` y para "
    "`find_same_source_clone`, sin ningún rastro de la partición."
)

# Hashes emitidos por el código vivo el 2026-08-26.
_FIJOS = [
    ("Senior Python Engineer", "Acme GmbH", "87c62ee664f978cc04fb5d3bc006715b"),
    # `_DIVERSITY_RE`: el marcador se quita ENTERO, antes que la puntuación.
    (
        "Werkstudent Product Management (gn)",
        "LichtBlick eMobility GmbH",
        "1e9d2b26ab38a9303f56e8acc283540d",
    ),
    # Seniority por TOKEN: "International" NO pierde su "intern".
    (
        "Senior International Marketing Manager, Nordics (m/w/d)",
        "Natalie AG",
        "f9564d45aeee7fe965d79367ba4d0d69",
    ),
    # G3/P2-12: identidad degenerada → cadena vacía, que nunca casa.
    ("Senior", "GmbH", ""),
]


@pytest.mark.parametrize("title,company,esperado", _FIJOS)
def test_la_formula_de_fuzzy_hash_no_ha_cambiado(title, company, esperado):
    assert Deduplicator.compute_fuzzy_hash(title, company) == esperado, _AVISO


class TestLaGemelaEsHistoricaSiEntroAntesDeESTACorrida:
    """P3-6 — `now() - 1 h` era una cota sin apoyo en los datos.

    De los 571 pares de gemelas del corpus no hay NI UNO con gap entre 0 y
    60 min: los intra-corrida tienen gap exactamente 0 (misma marca de
    transacción, porque `b3c7d1a95e42` sigue sin aplicar) y el resto pasa de la
    hora. Cualquier valor entre 0 s y 60 min clasificaba idéntico. El riesgo
    real era el INVERSO: las corridas duran 100-321 s, así que una re-ejecución
    manual dentro de la hora anterior —el journal las tiene: 10:21:59, 10:22:01
    y 10:23:44 del 2026-07-28— degradaba un clon REAL a «gemela de la misma
    corrida» y no lo contaba. Falso negativo silencioso.
    """

    ARRANQUE = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)

    def test_la_que_entro_en_esta_corrida_no_es_historica(self):
        entro = self.ARRANQUE + timedelta(seconds=30)
        assert Deduplicator._is_historical_twin(entro, self.ARRANQUE) is False

    def test_la_que_entro_justo_antes_del_arranque_SI_lo_es(self):
        """El caso que la cota de 1 h perdía: re-ejecución manual reciente."""
        entro = self.ARRANQUE - timedelta(seconds=90)
        assert Deduplicator._is_historical_twin(entro, self.ARRANQUE) is True

    @pytest.mark.parametrize("minutos", [2, 30, 59, 61, 120, 2880])
    def test_todo_lo_anterior_al_arranque_es_historico(self, minutos):
        entro = self.ARRANQUE - timedelta(minutes=minutos)
        assert Deduplicator._is_historical_twin(entro, self.ARRANQUE) is True

    def test_sin_fecha_no_se_afirma_nada(self):
        assert Deduplicator._is_historical_twin(None, self.ARRANQUE) is False

    def test_una_fecha_naive_se_lee_como_UTC(self):
        """La columna es `timestamptz` y asyncpg devuelve tz, pero la rama existe."""
        naive = (self.ARRANQUE - timedelta(hours=2)).replace(tzinfo=None)
        assert Deduplicator._is_historical_twin(naive, self.ARRANQUE) is True
