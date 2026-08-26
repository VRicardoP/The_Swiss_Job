"""Regresiones de la auditoría G2 — parser de salarios (familia de corrupción).

Ambas las introdujo el fix G1/P2-7 al mover la detección de la «k» del texto
entero al regex adyacente al número:

- P2-3: en un rango con UNA sola «k» al final («CHF 80-100k», el shorthand
  habitual) la cota baja se quedaba sin el ×1000 → `salary_min_chf=80`
  (80 CHF anuales) persistido y media distorsionada en `compute_salary_match`.
- P3-3: con el número pegado a letras, el lookahead negativo dejaba RETROCEDER
  al motor dentro del número hasta que siguiera un dígito: «80'000CHF» → 8000
  y «100kEUR» → 10. Números plausibles-pero-falsos, peores que un descarte.

«Kanton» (el caso que motivó G1/P2-7) sigue sin multiplicar: su K va seguida
de letra y no es un código de divisa.
"""

import pytest

from services.data_normalizer import DataNormalizer


class TestP23ShorthandDeRango:
    @pytest.mark.parametrize(
        "text",
        [
            "CHF 80-100k",
            "80-100k CHF",
            "80 - 100k",
            "EUR 80–100K",
        ],
    )
    def test_la_k_final_cubre_tambien_la_cota_baja(self, text):
        """«80-100k» significa 80k-100k: la baja no puede quedarse en 80."""
        lo, hi, _ = DataNormalizer._parse_salary_string(text)
        assert (lo, hi) == (80000.0, 100000.0)

    def test_la_k_en_ambas_cotas_sigue_funcionando(self):
        assert DataNormalizer._parse_salary_string("80k-100k")[:2] == (
            80000.0,
            100000.0,
        )

    def test_rango_sin_k_no_se_multiplica(self):
        """No-regresión: un rango horario/diario no debe inflarse."""
        assert DataNormalizer._parse_salary_string("25-30 CHF/hour")[:2] == (
            25.0,
            30.0,
        )

    def test_rango_decimal_intacto(self):
        assert DataNormalizer._parse_salary_string("25.5-30.75 USD/hour")[:2] == (
            25.5,
            30.75,
        )

    def test_rango_suizo_completo_intacto(self):
        assert DataNormalizer._parse_salary_string("CHF 80'000 - 100'000")[:2] == (
            80000.0,
            100000.0,
        )

    def test_cota_baja_ya_grande_no_se_toca(self):
        """«80000-100k» no debe convertir 80000 en 80 millones."""
        lo, hi, _ = DataNormalizer._parse_salary_string("80000-100k")
        assert lo == 80000.0
        assert hi == 100000.0

    def test_el_shorthand_llega_correcto_a_la_fila_persistida(self):
        job = {"salary_original": "CHF 80-100k", "salary_period": "yearly"}
        out = DataNormalizer.normalize_salary(job)
        assert out["salary_min_chf"] == 80000
        assert out["salary_max_chf"] == 100000


class TestP33NumeroPegadoALetras:
    def test_divisa_pegada_no_trunca_el_numero(self):
        lo, hi, cur = DataNormalizer._parse_salary_string("80'000CHF")
        assert (lo, hi) == (80000.0, 80000.0)
        assert cur == "CHF"

    def test_k_con_divisa_pegada(self):
        lo, hi, cur = DataNormalizer._parse_salary_string("100kEUR")
        assert (lo, hi) == (100000.0, 100000.0)
        assert cur == "EUR"

    def test_numero_pegado_a_palabra_no_se_trunca(self):
        """Sin borde válido no hay salario que extraer — nunca un número falso."""
        lo, hi, _ = DataNormalizer._parse_salary_string("Referenz 80'000ABC intern")
        assert lo != 8000.0
        assert hi != 8000.0

    def test_kanton_sigue_sin_multiplicar(self):
        """No-regresión de G1/P2-7: la K de «Kanton» no es un ×1000."""
        assert DataNormalizer._parse_salary_string("80 CHF pro Stunde, Kanton Zürich")[
            :2
        ] == (80.0, 80.0)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("CHF 120'000.-", (120000.0, 120000.0)),
            ("45,50 EUR", (45.5, 45.5)),
            ("60-80% Pensum, CHF 90'000", (90000.0, 90000.0)),
            ("90k", (90000.0, 90000.0)),
        ],
    )
    def test_formatos_ya_soportados_intactos(self, text, expected):
        assert DataNormalizer._parse_salary_string(text)[:2] == expected
