"""Tests for the IrishJobs.ie + Jobs.ie scraper (StepStone __PRELOADED_STATE__).

Puros, sin red: el parser se prueba contra una fixture HTML recortada
(tests/fixtures/irishjobs_listing.html) con la estructura real
`window.__PRELOADED_STATE__["app-unifiedResultlist"] = {...}` más blobs señuelo
(google-onetap + una referencia de solo-lectura) para verificar el anclaje exacto.
"""

from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.irishjobs import IrishJobsScraper, _parse_salary

FIXTURES = Path(__file__).parent / "fixtures"


def _assert_normalized(result: dict, source: str) -> None:
    """Asserts comunes a todo dict normalizado (réplica de test_scrapers.py)."""
    assert result["source"] == source
    assert result["hash"]
    assert len(result["hash"]) == 32
    assert result["title"]
    assert result["url"]
    assert isinstance(result["tags"], list)
    assert len(result["tags"]) <= 15
    assert isinstance(result["remote"], bool)
    for key in [
        "company",
        "location",
        "canton",
        "description",
        "description_snippet",
        "salary_min_chf",
        "salary_max_chf",
        "salary_original",
        "salary_currency",
        "salary_period",
        "language",
        "seniority",
        "contract_type",
        "employment_type",
        "logo",
    ]:
        assert key in result


class TestIrishJobsScraper:
    def test_source_name(self):
        assert IrishJobsScraper().get_source_name() == "irishjobs"

    def test_page_url_both_hosts(self):
        s = IrishJobsScraper()
        assert (
            s._page_url("https://www.irishjobs.ie", 1)
            == "https://www.irishjobs.ie/jobs/work-from-home?page=1"
        )
        assert (
            s._page_url("https://www.jobs.ie", 3)
            == "https://www.jobs.ie/jobs/work-from-home?page=3"
        )
        # build_listing_url usa el host primario
        assert s.build_listing_url(2, "") == (
            "https://www.irishjobs.ie/jobs/work-from-home?page=2"
        )

    def test_fetch_details_disabled(self):
        # SIN segunda llamada HTTP por oferta: todo sale del blob del listado.
        assert IrishJobsScraper.FETCH_DETAILS is False

    # ------------------------------------------------------------------
    # parse_listing_page contra la fixture real recortada
    # ------------------------------------------------------------------

    def test_parse_listing_page(self):
        html = (FIXTURES / "irishjobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = IrishJobsScraper().parse_listing_page(soup)

        assert len(stubs) == 3

        # Oferta 0: "Not Disclosed" → sin salario numérico; logo real presente.
        job0 = stubs[0]
        assert job0["id"] == 107715623
        assert job0["title"] == "Legal PA - Commercial Litigation"
        assert job0["company"] == "Lex Consultancy"
        assert job0["url"].startswith(
            "https://www.irishjobs.ie/job/"
        )  # relativa→absoluta
        assert job0["remote"] is True  # derivado del scope, no del item
        assert job0["salary_min_chf"] is None
        assert job0["salary_max_chf"] is None
        assert job0["salary_currency"] is None
        assert job0["logo"].endswith(".png")
        # textSnippet con <strong> → descripción sin HTML
        assert "<strong>" not in job0["description"]
        assert job0["description"]

        # Oferta 1: rango limpio "€35,000 - €45,000 per annum". El stub NO trae los
        # importes en *_chf (irían en EUR); solo currency+period+original. La
        # conversión EUR→CHF la hace DataNormalizer.normalize_salary aguas abajo.
        job1 = stubs[1]
        assert job1["salary_min_chf"] is None
        assert job1["salary_max_chf"] is None
        assert job1["salary_currency"] == "EUR"
        assert job1["salary_period"] == "yearly"
        assert job1["salary_original"] == "€35,000 - €45,000 per annum"

        # Oferta 2: "€90,000 - €00" → currency/period detectados; importes a normalizar
        # después. logo vacío → None.
        job2 = stubs[2]
        assert job2["salary_min_chf"] is None
        assert job2["salary_max_chf"] is None
        assert job2["salary_currency"] == "EUR"
        assert job2["logo"] is None

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert IrishJobsScraper().parse_listing_page(soup) == []

    def test_parse_listing_page_ignores_decoy_blobs(self):
        # Solo señuelos (google-onetap + referencia de solo-lectura), sin asignación.
        html = (
            "<html><head>"
            '<script>window.__PRELOADED_STATE__["google-onetap"] = '
            '{"clientId":"x","items":[{"id":1}]};</script>'
            '<script>var r = window.__PRELOADED_STATE__["app-unifiedResultlist"];'
            "</script>"
            "</head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        assert IrishJobsScraper().parse_listing_page(soup) == []

    def test_parse_listing_page_malformed_blob_returns_empty(self):
        # La clave existe pero el literal está corrupto → decode falla → [] (no peta).
        html = (
            "<html><head><script>"
            'window.__PRELOADED_STATE__["app-unifiedResultlist"] = {"searchResults":'
            "</script></head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        assert IrishJobsScraper().parse_listing_page(soup) == []

    # ------------------------------------------------------------------
    # normalize_job → esquema unificado
    # ------------------------------------------------------------------

    def test_normalize_job(self):
        raw = {
            "id": 123,
            "title": "Remote Python Developer",
            "company": "ACME",
            "location": "County Dublin",
            "url": "https://www.irishjobs.ie/job/remote-python/acme-job123",
            "remote": True,
            "description": "Build APIs with FastAPI and customer success tooling.",
            "logo": "https://www.irishjobs.ie/CompanyLogos/abc.png",
            "salary_original": "€60,000 - €70,000 per annum",
            "salary_min_chf": 60000,
            "salary_max_chf": 70000,
            "salary_currency": "EUR",
            "salary_period": "yearly",
        }
        result = IrishJobsScraper().normalize_job(raw)
        _assert_normalized(result, "irishjobs")
        assert result["title"] == "Remote Python Developer"
        assert result["company"] == "ACME"
        assert result["remote"] is True
        assert result["salary_min_chf"] == 60000
        assert result["salary_max_chf"] == 70000
        assert result["salary_currency"] == "EUR"
        assert result["salary_period"] == "yearly"
        assert result["canton"] is None  # ubicación irlandesa, sin cantón suizo

    def test_normalize_missing_fields(self):
        # Raw mínimo (title + url): no debe petar; defaults sanos.
        raw = {
            "title": "Analyst",
            "url": "https://www.jobs.ie/job/analyst/acme-job1",
        }
        result = IrishJobsScraper().normalize_job(raw)
        _assert_normalized(result, "irishjobs")
        assert result["company"] == "Unknown"
        assert result["remote"] is False  # sin flag de scope → False
        assert result["salary_min_chf"] is None
        assert result["logo"] is None

    def test_normalize_job_has_21_keys(self):
        raw = {"title": "X", "url": "https://www.jobs.ie/job/x/y-job2"}
        result = IrishJobsScraper().normalize_job(raw)
        expected = {
            "hash",
            "source",
            "title",
            "company",
            "location",
            "canton",
            "description",
            "description_snippet",
            "url",
            "remote",
            "tags",
            "logo",
            "salary_min_chf",
            "salary_max_chf",
            "salary_original",
            "salary_currency",
            "salary_period",
            "language",
            "seniority",
            "contract_type",
            "employment_type",
        }
        assert set(result.keys()) == expected

    def test_salary_converted_to_chf_through_pipeline(self):
        # Regresión (P1): el scraper deja salary_*_chf=None y delega la conversión
        # EUR→CHF + anualización a DataNormalizer.normalize_salary (como los otros
        # scrapers). Un "€/hora" NO debe guardarse como CHF/año sin convertir
        # (el bug original guardaba €22/h como 22 CHF/año → error ~2000x).
        from services.data_normalizer import DataNormalizer

        stub = {
            "id": 9,
            "title": "Support Engineer",
            "company": "ACME",
            "location": "Dublin",
            "url": "https://www.irishjobs.ie/job/support/acme-job9",
            "remote": True,
            "description": "Support role",
            "logo": None,
            "salary_original": "€22.00 - €25.00 per hour",
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_currency": "EUR",
            "salary_period": "hourly",
        }
        job = DataNormalizer.normalize_salary(IrishJobsScraper().normalize_job(stub))
        # 22 EUR/h × 0.96 × 2080 ≈ 43.929: anualizado y convertido, nunca el crudo 22.
        assert job["salary_min_chf"] is not None and job["salary_min_chf"] > 1000
        assert job["salary_max_chf"] is not None and job["salary_max_chf"] > 1000

    def test_items_to_stubs_leaves_chf_none(self):
        # El stub del listado NO debe traer salary_*_chf prellenados (irían en EUR).
        data = {
            "searchResults": {
                "items": [
                    {
                        "id": 1,
                        "title": "Dev",
                        "url": "/job/dev/acme-job1",
                        "companyName": "ACME",
                        "salary": "€35,000 - €45,000 per annum",
                    }
                ]
            }
        }
        stubs = IrishJobsScraper()._items_to_stubs(data, "https://www.irishjobs.ie")
        assert stubs[0]["salary_min_chf"] is None
        assert stubs[0]["salary_max_chf"] is None
        assert stubs[0]["salary_currency"] == "EUR"
        assert stubs[0]["salary_period"] == "yearly"

    # ------------------------------------------------------------------
    # _parse_salary — parser del string de display con tolerancia a basura
    # ------------------------------------------------------------------

    def test_parse_salary_not_disclosed(self):
        assert _parse_salary("€ Not Disclosed") == (None, None, None, None)

    def test_parse_salary_empty(self):
        assert _parse_salary("") == (None, None, None, None)

    def test_parse_salary_clean_range(self):
        assert _parse_salary("€35,000 - €45,000 per annum") == (
            35000,
            45000,
            "EUR",
            "yearly",
        )

    def test_parse_salary_single_value(self):
        assert _parse_salary("€31,921 per annum") == (31921, 31921, "EUR", "yearly")

    def test_parse_salary_malformed_max(self):
        # "€00" no parsea limpio (<= 0) → max None, min conservado.
        assert _parse_salary("€90,000 - €00 per annum") == (
            90000,
            None,
            "EUR",
            "yearly",
        )

    def test_parse_salary_hourly(self):
        assert _parse_salary("€22.00 - €25.00 per hour") == (22, 25, "EUR", "hourly")

    # ------------------------------------------------------------------
    # Dedupe entre hosts por id de plataforma
    # ------------------------------------------------------------------

    def test_dedupe_new_by_platform_id(self):
        seen: set = set()
        page1 = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]
        page2 = [{"id": 2, "title": "B-otro-host"}, {"id": 3, "title": "C"}]
        fresh1 = IrishJobsScraper._dedupe_new(page1, seen)
        fresh2 = IrishJobsScraper._dedupe_new(page2, seen)
        assert [j["id"] for j in fresh1] == [1, 2]
        assert [j["id"] for j in fresh2] == [3]  # id 2 ya visto en el otro host
