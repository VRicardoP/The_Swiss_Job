"""Regresiones de la auditoría G3 — familia de corrupción de SALARIOS (lote C).

Las tres corrompen datos ya persistidos:

- P2-3: el separador de miles ESPACIO (francés, uso suizo francófono y norma
  ISO 31-0, incluido el espacio duro U+00A0) no lo admitía `_NUM`, así que
  «100 000 CHF» se persistía como 100 CHF — error ×1000 que además hunde el
  factor salario del matching hasta 0.
- P2-4: la divisa repetida en el segundo extremo del rango («£30,000 -
  £40,000», la forma canónica en Reino Unido e Irlanda) no casaba
  `_SALARY_RANGE_RE`; el parser caía en silencio al camino `single` y
  persistía 30000-30000.
- P2-5: `CURRENCY_TO_CHF.get(currency, 1.0)` guardaba 1:1 como CHF cualquier
  divisa desconocida — 107 filas de producción con el importe nominal (INR
  ≈ ×106, ZAR ≈ ×21) puntuando al máximo en el factor salario.

Los formatos que G1/G2 dejaron buenos (apóstrofo suizo, `.-`, `k`, `p.a.`,
`/h`, pensums, «Kanton», decimales, 13x) se re-verifican aquí como red de
no-regresión: el cambio es de regex.
"""

import pytest

from services.data_normalizer import CURRENCY_TO_CHF, DataNormalizer


class TestP23SeparadorEspacio:
    """El espacio como separador de miles (fr / CH-FR / ISO 31-0)."""

    @pytest.mark.parametrize(
        ("text", "esperado"),
        [
            ("100 000 CHF", (100000.0, 100000.0)),
            ("80 000 CHF", (80000.0, 80000.0)),
            ("3 500 CHF par mois", (3500.0, 3500.0)),
            ("1 200 000 INR", (1200000.0, 1200000.0)),
            # Espacio duro U+00A0: `tes.py` lo normaliza porque LLEGA de verdad.
            ("100\xa0000 CHF", (100000.0, 100000.0)),
            ("100\xa0000\xa0EUR", (100000.0, 100000.0)),
        ],
    )
    def test_valor_unico_con_espacio(self, text, esperado):
        lo, hi, _ = DataNormalizer._parse_salary_string(text)
        assert (lo, hi) == esperado

    @pytest.mark.parametrize(
        ("text", "esperado"),
        [
            ("60 000 - 80 000 EUR par an", (60000.0, 80000.0)),
            ("CHF 5 000 - CHF 6 500 pro Monat", (5000.0, 6500.0)),
            ("$ 50 000 - $ 70 000", (50000.0, 70000.0)),
            ("£ 30 000 - £ 40 000 per annum", (30000.0, 40000.0)),
            ("60 000 to 80 000 CHF", (60000.0, 80000.0)),
        ],
    )
    def test_rango_con_espacio(self, text, esperado):
        lo, hi, _ = DataNormalizer._parse_salary_string(text)
        assert (lo, hi) == esperado

    def test_persistido_no_se_divide_por_mil(self):
        """El síntoma de producción: 100 000 CHF guardado como 100 CHF."""
        job = DataNormalizer.normalize_salary({"salary_original": "100 000 CHF"})
        assert job["salary_min_chf"] == 100000
        assert job["salary_max_chf"] == 100000

    @pytest.mark.parametrize(
        ("text", "esperado"),
        [
            # Grupos que NO son de 3 dígitos: el espacio no une nada.
            ("80000 100000", (80000.0, 80000.0)),
            ("1 2 3", (None, None)),
            ("Level 5", (None, None)),
            # La «k» pegada y el shorthand de G2/P2-3 siguen intactos.
            ("CHF 80-100k", (80000.0, 100000.0)),
            ("80'000CHF", (80000.0, 80000.0)),
            # «Kanton» (G1/P2-7) sigue sin multiplicar pese al espacio previo.
            ("80 CHF pro Stunde im Kanton Zürich", (80.0, 80.0)),
        ],
    )
    def test_el_espacio_no_se_traga_numeros_vecinos(self, text, esperado):
        lo, hi, _ = DataNormalizer._parse_salary_string(text)
        assert (lo, hi) == esperado


class TestP24DivisaRepetidaEnElRango:
    """«£30,000 - £40,000»: la forma canónica UK/IE no puede caer a `single`."""

    @pytest.mark.parametrize(
        ("text", "esperado"),
        [
            ("£30,000 - £40,000", (30000.0, 40000.0, "GBP")),
            ("£30,000 to £40,000", (30000.0, 40000.0, "GBP")),
            ("£31,650 – £49,084 per annum", (31650.0, 49084.0, "GBP")),
            ("€45,000 - €55,000", (45000.0, 55000.0, "EUR")),
            ("$50,000 - $70,000 per year", (50000.0, 70000.0, "USD")),
            ("CHF 80'000 - CHF 100'000", (80000.0, 100000.0, "CHF")),
            ("USD 25 - USD 30 per hour", (25.0, 30.0, "USD")),
            ("EUR 60'000 to EUR 80'000", (60000.0, 80000.0, "EUR")),
        ],
    )
    def test_la_divisa_repetida_no_rompe_el_rango(self, text, esperado):
        assert DataNormalizer._parse_salary_string(text) == esperado

    @pytest.mark.parametrize(
        ("text", "esperado"),
        [
            # Control del auditor: con prefijo único ya funcionaba, sigue igual.
            ("£30,000 - 40,000", (30000.0, 40000.0)),
            ("CHF 80'000 - 100'000", (80000.0, 100000.0)),
            ("GBP30000-40000", (30000.0, 40000.0)),
            ("80'000 - 100'000 CHF", (80000.0, 100000.0)),
        ],
    )
    def test_el_rango_con_divisa_unica_no_cambia(self, text, esperado):
        lo, hi, _ = DataNormalizer._parse_salary_string(text)
        assert (lo, hi) == esperado

    def test_persistido_conserva_el_maximo(self):
        job = DataNormalizer.normalize_salary({"salary_original": "£30,000 - £40,000"})
        assert job["salary_min_chf"] != job["salary_max_chf"]
        assert job["salary_max_chf"] == int(40000 * CURRENCY_TO_CHF["GBP"])


class TestP25DivisaDesconocida:
    """Una divisa que no sabemos convertir NO se guarda 1:1 como CHF."""

    @pytest.mark.parametrize("currency", ["XYZ", "BRL", "JPY", "RUB"])
    def test_divisa_fuera_del_mapa_deja_los_chf_vacios(self, currency):
        job = DataNormalizer.normalize_salary(
            {"salary_original": "100000-165000", "salary_currency": currency}
        )
        assert job["salary_min_chf"] is None
        assert job["salary_max_chf"] is None
        # El dato crudo sobrevive: la fila sigue siendo reparable.
        assert job["salary_original"] == "100000-165000"
        assert job["salary_currency"] == currency

    def test_avisa_por_log(self, caplog):
        with caplog.at_level("WARNING", logger="services.data_normalizer"):
            DataNormalizer.normalize_salary(
                {"salary_original": "100000", "salary_currency": "XYZ"}
            )
        assert any("XYZ" in rec.getMessage() for rec in caplog.records)

    @pytest.mark.parametrize(
        "currency", ["CAD", "AUD", "NOK", "SEK", "DKK", "PLN", "INR", "ZAR", "SGD"]
    )
    def test_las_divisas_anadidas_si_convierten(self, currency):
        """Las 9 nuevas del mapa convierten, y ninguna se queda en 1:1."""
        assert currency in CURRENCY_TO_CHF
        assert CURRENCY_TO_CHF[currency] != 1.0
        job = DataNormalizer.normalize_salary(
            {"salary_original": "100000", "salary_currency": currency}
        )
        assert job["salary_min_chf"] == int(100000 * CURRENCY_TO_CHF[currency])

    def test_las_cuatro_originales_no_cambian(self):
        assert CURRENCY_TO_CHF["CHF"] == 1.0
        job = DataNormalizer.normalize_salary(
            {"salary_original": "50000-60000", "salary_currency": "EUR"}
        )
        assert (job["salary_min_chf"], job["salary_max_chf"]) == (48000, 57600)

    def test_sin_divisa_sigue_asumiendo_chf(self):
        """Un salario sin divisa se sigue tratando como CHF (no se descarta)."""
        job = DataNormalizer.normalize_salary({"salary_original": "80000-100000"})
        assert (job["salary_min_chf"], job["salary_max_chf"]) == (80000, 100000)

    def test_no_pisa_lo_que_el_productor_ya_normalizo(self):
        """La cota de G1/P3-11 manda: si ya hay _chf, no se toca nada."""
        job = DataNormalizer.normalize_salary(
            {"salary_min_chf": 90000, "salary_currency": "XYZ"}
        )
        assert job["salary_min_chf"] == 90000


class TestNoRegresionFormatosVivos:
    """Abanico de formatos reales que G1/G2 dejaron correctos."""

    @pytest.mark.parametrize(
        ("text", "esperado"),
        [
            ("CHF 80'000.-", (80000.0, 80000.0)),
            ("80'000.- CHF", (80000.0, 80000.0)),
            ("45'500.50 CHF", (45500.5, 45500.5)),
            ("25.5 CHF/h", (25.5, 25.5)),
            ("45,50 EUR/h", (45.5, 45.5)),
            ("CHF 1'500 pro Woche", (1500.0, 1500.0)),
            ("60'000 EUR p.a.", (60000.0, 60000.0)),
            ("80.000 - 100.000 EUR", (80000.0, 100000.0)),
            ("13x CHF 6'500", (6500.0, 6500.0)),
            ("Salaire: 5'500 CHF/mois", (5500.0, 5500.0)),
            ("100kEUR", (100000.0, 100000.0)),
            ("Pensum 80-100%", (None, None)),
            ("50 % Anstellung", (None, None)),
            ("Kanton Bern", (None, None)),
            ("competitive salary", (None, None)),
            ("", (None, None)),
        ],
    )
    def test_formatos_estables(self, text, esperado):
        lo, hi, _ = DataNormalizer._parse_salary_string(text)
        assert (lo, hi) == esperado
