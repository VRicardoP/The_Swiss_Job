"""G5/P3-5 — dos cotas del parser de salarios que G4/P2-1 dejó abiertas.

`92b9204` probó PRIMERO el patrón con divisa en ambos extremos, lo que cerró
ocho casos de escalas salariales británicas ("Grade 6 - £30,000" ya no persiste
`salary_min_chf = 6`). Pero abrió dos flancos:

(a) **La divisa podía no ser la del par de números que casa.** `_CUR_RE` toma la
    PRIMERA divisa de TODO el texto mientras `_SALARY_RANGE_CUR_RE` toma el
    primer rango DELIMITADO por divisa. Los dos regex pueden apuntar a sitios
    distintos y el resultado mezclaba el importe de uno con la divisa del otro.

(b) **«Divisa solo en el extremo derecho» colapsaba al camino `single`.** El
    `£` corta el segundo `_NUM` del patrón clásico, así que "30,000 - £40,000"
    no casaba ningún rango y se persistía 30000-30000.

Alcance real sobre los datos de hoy: **CERO**. Barrido diferencial de los 637
valores distintos de `salary_original` del corpus de producción (SELECT de solo
lectura): 0 diferencias PRE→POST. Es una cota que conviene dejar escrita antes
de que `tes` o `irishjobs` la produzcan — los dos vuelcan texto libre.
"""

import pytest

from services.data_normalizer import DataNormalizer


def _parse(text: str):
    lo, hi, cur = DataNormalizer._parse_salary_string(text)
    return (None if lo is None else int(lo), None if hi is None else int(hi), cur)


class TestLaDivisaSaleDelRangoQueCaso:
    def test_dos_rangos_con_divisas_distintas_no_se_mezclan(self):
        """El caso estructural: `_CUR_RE` elegía CHF y el rango era el de £.

        G7/P3-1 corrigió la RESPUESTA sin reabrir el defecto. Este test fijaba
        `(75000, 90000, "GBP")`: el importe del PARÉNTESIS, porque el patrón con
        divisa en ambos extremos ganaba por prioridad global aunque estuviera a
        la derecha. Pero en «90'000 - 110'000 CHF (env. £75,000 - £90,000)» el
        sueldo es el de la izquierda y el paréntesis es una conversión
        aproximada — el propio «env.» lo dice. Gana el más a la izquierda.

        Lo que G5 vino a cerrar sigue cerrado, y es lo que este test comprueba
        ahora: el importe y la divisa salen del MISMO sitio. Antes se mezclaba
        el importe del paréntesis (£) con la divisa del texto entero (CHF).
        """
        assert _parse("90'000 - 110'000 CHF (env. £75,000 - £90,000)") == (
            90000,
            110000,
            "CHF",
        )

    def test_la_divisa_es_la_del_rango_que_gano_no_la_primera_del_texto(self):
        """El invariante de G5, con el paréntesis PRIMERO para aislarlo.

        Aquí gana el rango en libras (es el más a la izquierda) y el texto trae
        `CHF` después: si la divisa se buscara en todo el texto saldría la
        mezcla que G5 cerró.
        """
        assert _parse("(env. £75,000 - £90,000) 90'000 - 110'000 CHF") == (
            75000,
            90000,
            "GBP",
        )

    def test_un_rango_sin_divisa_dentro_conserva_la_del_texto(self):
        """No-regresión: "80000-100000 CHF" sigue saliendo en CHF."""
        assert _parse("80'000 - 100'000 CHF") == (80000, 100000, "CHF")
        assert _parse("80000-100000") == (80000, 100000, None)


class TestDivisaSoloEnElExtremoDerecho:
    def test_el_rango_ya_no_colapsa_a_single(self):
        assert _parse("30,000 - £40,000") == (30000, 40000, "GBP")

    def test_shorthand_con_k_a_la_izquierda(self):
        assert _parse("30k - £40,000") == (30000, 40000, "GBP")

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            # Las escalas salariales tienen LA MISMA forma: `<num> - <cur><num>`.
            # Lo único que las separa es la magnitud de la cota baja.
            ("Grade 6 - £30,000", (30000, 30000, "GBP")),
            ("Band 5 - £28,407", (28407, 28407, "GBP")),
            ("NJC Scale 4 - £24,000", (24000, 24000, "GBP")),
            ("UPS 3 - £45,000", (45000, 45000, "GBP")),
            ("MPS 1 - £30,000 per annum", (30000, 30000, "GBP")),
            ("Level 5 - £22,000", (22000, 22000, "GBP")),
            ("Main Pay Scale 1 - 6 (£31,650 - £43,607)", (31650, 43607, "GBP")),
        ],
    )
    def test_las_ocho_mejoras_de_G4_no_se_reabren(self, texto, esperado):
        """Admitir la divisa a la derecha NO puede reabrir la regresión que
        cerró G4/P2-1: el ruido pequeño no secuestra el mínimo."""
        assert _parse(texto) == esperado


class TestNoRegresionDelResto:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("£30,000 - £40,000", (30000, 40000, "GBP")),
            ("CHF 80'000 - CHF 100'000", (80000, 100000, "CHF")),
            ("80k - 100k CHF", (80000, 100000, "CHF")),
            ("80-100k CHF", (80000, 100000, "CHF")),
            ("100 000 - 120 000 EUR", (100000, 120000, "EUR")),
            ("CHF 95'000", (95000, 95000, "CHF")),
            ("Kanton Zurich, 80 CHF pro Stunde", (80, 80, "CHF")),
            ("60-80%", (None, None, None)),
        ],
    )
    def test_las_formas_ya_cubiertas_no_cambian(self, texto, esperado):
        assert _parse(texto) == esperado

    def test_cota_preexistente_declarada_escala_de_DOS_digitos(self):
        """No la cierra este fix y no es una regresión suya: viene de que
        `re.search` es leftmost y `single` solo exige 2 dígitos. Sin ejemplares
        en los 637 valores reales del corpus."""
        assert _parse("Point 12 - €35,000") == (12, 12, "EUR")
