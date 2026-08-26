"""G7/P2-2, P3-1, P3-2 y P3-9 — el parser de salarios, comparado contra su
PREDECESOR y no solo contra el bug que decía arreglar.

P2-2: `e468ade` sustituyó la cadena de prioridades por una elección posicional…
pero solo entre `right` y `plain`, y `_low_looks_like_salary` se le exigía a
`right` y nunca a `plain`. Con eso, cualquier rango de RUIDO a la izquierda —una
escala salarial UK/IE, un número de referencia, un pensum, unos años de
experiencia— secuestraba el parseo por la puerta del `else` y reintroducía la
corrupción que cerró G4/P2-1. Ocho formas de anuncio quedaban ESTRICTAMENTE PEOR
que en `5bfece0`, el commit anterior al fix.

P3-1: `_SALARY_RANGE_CUR_RE` seguía ganando por prioridad global, así que la
divisa escrita en los DOS extremos de un paréntesis —la forma canónica en
anuncios CH-FR/CH-DE— se llevaba el parseo pese a estar a la derecha del rango
real. El comentario del commit afirmaba justo lo contrario («el más a la
izquierda gana»).

P3-2: `_SPACED` admitía solo U+0020 y U+00A0. Los tres espacios estrechos
(U+202F —el que emite `Intl.NumberFormat('de-CH')`—, U+2009 y U+2007) y el prime
tipográfico U+2032 caían al camino `single`: ×1000 en silencio.

P3-9: `_SALARY_RANGE_*` escala en O(n²) sobre texto patológico y `float()`
devuelve `inf` con ≥309 dígitos, que reventaba el `int()` con `OverflowError`.
"""

import time

import pytest

from services.data_normalizer import DataNormalizer


def _rango(texto: str) -> tuple[float | None, float | None]:
    return DataNormalizer._parse_salary_string(texto)[:2]


class TestElRuidoDeLaIzquierdaYaNoSecuestraElRango:
    """P2-2 — las ocho formas en que HEAD era peor que `5bfece0`."""

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("Grade 6 - 8, salary 30,000 - £40,000 per annum", (30000.0, 40000.0)),
            ("MPS 1 - 6: 31,650 - £43,607", (31650.0, 43607.0)),
            ("NJC Scale 4 - 6 / 25,000 - £30,000", (25000.0, 30000.0)),
            ("Band 2 to 4, 45,000 to £55,000 per annum", (45000.0, 55000.0)),
            ("Teilzeit 60 - 80 Prozent, 90000 - CHF 110000", (90000.0, 110000.0)),
            ("Ref 123 - 456. Gehalt 80000 - CHF 100000", (80000.0, 100000.0)),
            ("2 - 5 Jahre Erfahrung, 90000 - CHF 120000", (90000.0, 120000.0)),
            ("Stufe 3 - 5, 85,000 - CHF 95,000", (85000.0, 95000.0)),
        ],
    )
    def test_gana_el_importe_y_no_la_escala(self, texto, esperado):
        assert _rango(texto) == esperado

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            # G4/P2-1 y G5/P3-5 en su forma exacta: siguen cerrados.
            ("Grade 6 - £30,000", (30000.0, 30000.0)),
            ("30,000 - £40,000", (30000.0, 40000.0)),
            # Las tres filas REALES del corpus que rompe la variante «obvia»
            # (exigirle la guarda a `plain` siempre): cota baja pequeña pero
            # verdadera. Aquí `plain` es el ÚNICO candidato y se respeta.
            ("12-42508 EUR", (12.0, 42508.0)),
            ("21-42508 EUR", (21.0, 42508.0)),
            ("720-2400 EUR", (720.0, 2400.0)),
        ],
    )
    def test_los_controles_no_se_mueven(self, texto, esperado):
        assert _rango(texto) == esperado


class TestElParentesisConDivisaEnLosDosExtremos:
    """P3-1 — el patrón más específico ya no gana por prioridad global."""

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            (
                "90000 - 110000 par an (env. CHF 7,500 - CHF 9,200 par mois)",
                (90000.0, 110000.0),
            ),
            ("80'000 - 100'000 (Bonus: CHF 5,000 - CHF 20,000)", (80000.0, 100000.0)),
        ],
    )
    def test_gana_el_rango_que_el_anuncio_enuncia_como_suyo(self, texto, esperado):
        assert _rango(texto) == esperado

    def test_el_parentesis_gana_si_va_PRIMERO(self):
        """La regla es posicional de verdad, no «el paréntesis nunca»."""
        assert _rango("(CHF 7,500 - CHF 9,200 par mois) 90000 - 110000 par an") == (
            7500.0,
            9200.0,
        )


class TestLosSeparadoresDeMilesEstrechos:
    """P3-2 — U+202F, U+2009, U+2007 y el prime U+2032 valían ×1000 menos."""

    @pytest.mark.parametrize(
        "separador",
        [" ", " ", " ", " ", "\xa0"],
        ids=["U+202F", "U+2009", "U+2007", "U+0020", "U+00A0"],
    )
    def test_el_rango_se_lee_entero(self, separador):
        texto = f"CHF 80{separador}000 - CHF 100{separador}000"
        assert _rango(texto) == (80000.0, 100000.0)

    @pytest.mark.parametrize("apostrofo", ["'", "’", "′"], ids=["'", "’", "′"])
    def test_el_apostrofo_suizo_en_todas_sus_formas(self, apostrofo):
        assert _rango(f"CHF 80{apostrofo}000 - CHF 100{apostrofo}000") == (
            80000.0,
            100000.0,
        )

    def test_el_valor_unico_tampoco_se_queda_en_80(self):
        assert _rango("CHF 80′000") == (80000.0, 80000.0)


class TestElTextoPatologico:
    """P3-9 — coste O(n²) y desbordamiento del INTEGER de la columna."""

    def test_el_parseo_esta_acotado_en_tiempo(self):
        texto = "9" * 16000 + " - " + "9" * 16000
        inicio = time.monotonic()
        _rango(texto)
        assert time.monotonic() - inicio < 1.0

    def test_un_importe_infinito_no_revienta_la_normalizacion(self):
        job = {"salary_original": "9" * 400, "salary_currency": "CHF"}
        salida = DataNormalizer.normalize_salary(dict(job))
        assert salida.get("salary_min_chf") is None
        assert salida.get("salary_max_chf") is None

    def test_un_importe_que_no_cabe_en_INT4_se_descarta(self):
        job = {"salary_original": "1" * 20, "salary_currency": "CHF"}
        salida = DataNormalizer.normalize_salary(dict(job))
        assert salida.get("salary_min_chf") is None

    def test_el_multiplicador_de_periodo_tampoco_desborda(self):
        job = {
            "salary_original": "2000000000",
            "salary_currency": "CHF",
            "salary_period": "monthly",
        }
        salida = DataNormalizer.normalize_salary(dict(job))
        assert salida.get("salary_min_chf") is None

    def test_un_importe_normal_sigue_persistiendose(self):
        job = {
            "salary_original": "CHF 80'000 - CHF 100'000",
            "salary_currency": "CHF",
            "salary_period": "yearly",
        }
        salida = DataNormalizer.normalize_salary(dict(job))
        assert (salida["salary_min_chf"], salida["salary_max_chf"]) == (80000, 100000)
