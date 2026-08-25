"""Regresiones de la auditoría G1 — familia de corrupción de salarios.

Cubre: P2-6 (decimales destruidos y apóstrofe suizo), P2-7 (heurística "k"
sobre el texto entero), P2-8 (week/day guardados como anuales), P2-2/P2-3
(adzuna: area[0] es el país; salario sin moneda), P3-11 (doble conversión con
una sola cota _chf), P3-12 (símbolos €/$/£ nunca casaban), P3-13 (rangos de
porcentaje/pensum parseados como salario).

Cada test codifica el comportamiento CORRECTO: con el bug presente, falla.
"""

from providers.adzuna import AdzunaProvider
from services.data_normalizer import DataNormalizer


def _job(**overrides):
    job = {
        "hash": "abc123",
        "source": "test",
        "title": "Developer",
        "company": "Acme",
        "url": "http://example.com/1",
        "location": "Zurich",
        "canton": "ZH",
        "description": None,
        "description_snippet": None,
        "remote": False,
        "tags": [],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
        "employment_type": None,
    }
    job.update(overrides)
    return job


class TestP26Decimales:
    def test_decimales_se_conservan(self):
        """G1/P2-6: "25.5-30.75 USD/hour" son 25.5 y 30.75, no 255 y 3075."""
        lo, hi, cur = DataNormalizer._parse_salary_string("25.5-30.75 USD")
        assert (lo, hi, cur) == (25.5, 30.75, "USD")

    def test_decimales_convertidos_a_chf(self):
        job = _job(salary_original="25.5-30.75 USD", salary_period="hourly")
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == int(25.5 * 0.88 * 2080)  # 46675
        assert result["salary_max_chf"] == int(30.75 * 0.88 * 2080)  # 56284

    def test_miles_europeo_sigue_funcionando(self):
        lo, hi, _ = DataNormalizer._parse_salary_string("80.000 - 100.000 EUR")
        assert (lo, hi) == (80_000, 100_000)

    def test_apostrofe_suizo(self):
        """G1/P2-6: "CHF 80'000 - 100'000" no debe salir como (0.0, 100.0)."""
        lo, hi, cur = DataNormalizer._parse_salary_string("CHF 80'000 - 100'000")
        assert (lo, hi, cur) == (80_000, 100_000, "CHF")

    def test_decimal_con_coma(self):
        lo, hi, _ = DataNormalizer._parse_salary_string("45,50 EUR pro Stunde")
        assert lo == 45.5 and hi == 45.5


class TestP27HeuristicaK:
    def test_kanton_no_multiplica(self):
        """G1/P2-7: una palabra con "k" en el texto no debe multiplicar x1000."""
        lo, hi, _ = DataNormalizer._parse_salary_string(
            "80 - 90 CHF pro Stunde im Kanton Zürich"
        )
        assert (lo, hi) == (80, 90)

    def test_k_adyacente_si_multiplica(self):
        lo, hi, _ = DataNormalizer._parse_salary_string("80k-100k CHF")
        assert (lo, hi) == (80_000, 100_000)

    def test_k_single_adyacente(self):
        lo, hi, _ = DataNormalizer._parse_salary_string("CHF 95k")
        assert lo == 95_000 and hi == 95_000


class TestP28PeriodosWeekDay:
    def test_week_se_anualiza(self):
        """G1/P2-8: 1500 EUR/week son ~74880 CHF/año, no 1440."""
        job = _job(
            salary_original="1500 EUR/week",
            salary_currency="EUR",
            salary_period="WEEK",
        )
        job = DataNormalizer.sanitize_enums(job)
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == int(1500 * 0.96 * 52)  # 74880
        # El enum de BD no modela week: el periodo NO debe llegar al INSERT.
        assert result["salary_period"] is None

    def test_day_se_anualiza(self):
        job = _job(
            salary_original="400 EUR/day",
            salary_currency="EUR",
            salary_period="DAY",
        )
        job = DataNormalizer.sanitize_enums(job)
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == int(400 * 0.96 * 260)  # 99840
        assert result["salary_period"] is None

    def test_periodos_validos_persisten(self):
        job = _job(salary_original="8000 USD", salary_period="monthly")
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_period"] == "monthly"
        assert result["salary_min_chf"] == int(8000 * 0.88 * 12)


class TestP311UnaSolaCota:
    def test_una_cota_chf_no_se_reconvierte(self):
        """G1/P3-11: una sola cota _chf prellenada no debe re-multiplicarse."""
        job = _job(
            salary_min_chf=90_000,
            salary_currency="EUR",
            salary_period="monthly",
            salary_original="90000 EUR/month",
        )
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == 90_000
        assert result["salary_max_chf"] is None


class TestP312SimbolosMoneda:
    def test_euro_simbolo(self):
        _, _, cur = DataNormalizer._parse_salary_string("€ 50.000 - 60.000")
        assert cur == "EUR"

    def test_dolar_simbolo(self):
        _, _, cur = DataNormalizer._parse_salary_string("$4,500/month")
        assert cur == "USD"

    def test_libra_simbolo(self):
        _, _, cur = DataNormalizer._parse_salary_string("£45,000")
        assert cur == "GBP"


class TestP313RangosPorcentaje:
    def test_pensum_no_es_salario(self):
        """G1/P3-13: "60-80%" es workload, no un salario 60-80."""
        lo, hi, _ = DataNormalizer._parse_salary_string("Pensum 60-80%")
        assert lo is None and hi is None

    def test_pensum_junto_a_salario_real(self):
        lo, hi, cur = DataNormalizer._parse_salary_string(
            "60-80%, CHF 90'000 - 110'000"
        )
        assert (lo, hi, cur) == (90_000, 110_000, "CHF")


class TestAdzuna:
    def _raw(self, **overrides):
        raw = {
            "id": "adzuna-1",
            "title": "Content Editor",
            "redirect_url": "https://adzuna.example/job/1",
            "description": "desc",
            "company": {"display_name": "Acme"},
            "location": {"area": ["UK", "London"], "display_name": "London, UK"},
            "category": {"label": "HR Jobs"},
        }
        raw.update(overrides)
        return raw

    def test_p2_2_area_ultimo_es_ciudad(self):
        """G1/P2-2: area es jerárquico mayor→menor; area[0] es el PAÍS."""
        result = AdzunaProvider().normalize_job(self._raw())
        assert result["location"] == "London"

    def test_p2_3_moneda_derivada_del_pais(self):
        """G1/P2-3: salario GBP sin moneda se guardaba como CHF (rate 1.0)."""
        raw = self._raw(salary_min=40000.0, salary_max=60000.0, _country="gb")
        result = AdzunaProvider().normalize_job(raw)
        assert result["salary_currency"] == "GBP"
        # Y el normalizador convierte de verdad:
        normalized = DataNormalizer.normalize_salary(result)
        assert normalized["salary_min_chf"] == int(40000 * 1.12)
        assert normalized["salary_max_chf"] == int(60000 * 1.12)

    def test_p2_3_pais_eur(self):
        raw = self._raw(salary_min=50000.0, _country="de")
        assert AdzunaProvider().normalize_job(raw)["salary_currency"] == "EUR"
