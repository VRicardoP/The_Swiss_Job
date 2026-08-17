"""Tests for the IrishJobs.ie + Jobs.ie scraper (StepStone __PRELOADED_STATE__).

Puros, sin red: el parser se prueba contra una fixture HTML recortada
(tests/fixtures/irishjobs_listing.html) con la estructura real
`window.__PRELOADED_STATE__["app-unifiedResultlist"] = {...}` más blobs señuelo
(google-onetap + una referencia de solo-lectura) para verificar el anclaje exacto.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from bs4 import BeautifulSoup

from scrapers.irishjobs import IrishJobsScraper, _STATE_ANCHOR_RE, _parse_salary
from utils import fetch_diagnostics as diag

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

    def test_entero_kilometrico_en_el_blob_registra_issue_sin_lanzar(self):
        """Fase 3 r3/H4: un entero JSON de 5000 dígitos hace que json.loads
        lance un ValueError PLANO ("Exceeds the limit…", límite CPython de
        conversión decimal, NO JSONDecodeError) que ESCAPABA de
        parse_listing_page. Capturarlo SIN registrar sería peor (falso
        `empty` con 0 issues): se exige EXACTAMENTE un issue KIND_NETWORK y
        veredicto `error` — misma forma que utils/http y financejobs."""
        html = (
            "<html><head><script>"
            'window.__PRELOADED_STATE__["app-unifiedResultlist"] = '
            f'{{"searchResults": {"9" * 5000}}};'
            "</script></head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert IrishJobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        assert "StepStone redeploy" in issues[0].detail
        assert diag.classify(0, issues) == "error"

    def test_anidamiento_extremo_registra_issue_sin_lanzar(self):
        """No bloqueante r6: la pata RecursionError del except estaba sin
        fijar — estrecharlo a solo ValueError sobrevivía a toda la batería.
        100k corchetes anidados hacen que el scanner C de json lance
        RecursionError (NO ValueError): no debe escapar y se exige
        EXACTAMENTE un issue KIND_NETWORK con veredicto `error` (G1)."""
        blob = '{"searchResults": ' + "[" * 100_000 + "]" * 100_000 + "}"
        html = (
            "<html><head><script>"
            'window.__PRELOADED_STATE__["app-unifiedResultlist"] = '
            f"{blob};"
            "</script></head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert IrishJobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        assert "RecursionError" in issues[0].detail
        assert diag.classify(0, issues) == "error"

    def test_blob_truncado_registra_issue_y_es_error(self):
        """Fase 4 r4/R3-1: un 200 con el literal TRUNCADO (redeploy que corta
        el blob) devolvía [] sin registrar nada — como no escapa ninguna
        excepción, la red por-fuente de los tasks tampoco sintetizaba
        OUTCOME_ERROR y la fuente ROTA salía `empty` con 0 issues (violación
        MATERIAL de G1). G2 protegido: una página legítimamente vacía de
        ambos hosts (sonda 2026-08-17, búsqueda sin resultados con
        "total": 0) SÍ trae el blob con items: [] — nunca pisa este camino."""
        html = (
            "<html><head><script>"
            'window.__PRELOADED_STATE__["app-unifiedResultlist"] = {"searchResults":'
            "</script></head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert IrishJobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        assert "StepStone redeploy" in issues[0].detail
        assert diag.classify(0, issues) == "error"

    def test_sin_ancla_registra_issue_y_es_error(self):
        """Fase 4 r4/R3-1 (el modo de fallo hermano): un 200 SIN la asignación
        del blob (redeploy que la retira; quedan solo señuelos) también salía
        `empty` con 0 issues. Mismo registro que financejobs cuando no
        encuentra su __NEXT_DATA__."""
        html = (
            "<html><head>"
            '<script>window.__PRELOADED_STATE__["google-onetap"] = '
            '{"clientId":"x","items":[{"id":1}]};</script>'
            "</head><body></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert IrishJobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        assert "StepStone redeploy" in issues[0].detail
        assert diag.classify(0, issues) == "error"

    # ------------------------------------------------------------------
    # Estructura INTERNA del blob (r5/H1): la deriva post-decode también
    # registra issue — el peldaño siguiente de r4/R3-1, que solo cubría
    # hasta decodificar. Solo `items: []` es vacío legítimo (G2).
    # ------------------------------------------------------------------

    @staticmethod
    def _soup_with_blob(blob_json: str) -> BeautifulSoup:
        html = (
            "<html><head><script>"
            'window.__PRELOADED_STATE__["app-unifiedResultlist"] = '
            f"{blob_json};"
            "</script></head><body></body></html>"
        )
        return BeautifulSoup(html, "lxml")

    def _assert_blob_records_single_issue(self, blob_json: str) -> None:
        """El blob decodifica pero su estructura deriva: [] + UN issue = error."""
        diag.begin()
        assert (
            IrishJobsScraper().parse_listing_page(self._soup_with_blob(blob_json)) == []
        )
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        assert "StepStone redeploy" in issues[0].detail
        assert diag.classify(0, issues) == "error"

    def test_search_results_no_objeto_registra_issue_sin_lanzar(self):
        """r5/H1: `searchResults` no-dict ("maintenance") hacía que
        `.get("items")` lanzara AttributeError que ESCAPABA del parser."""
        self._assert_blob_records_single_issue('{"searchResults": "maintenance"}')

    def test_items_renombrado_registra_issue(self):
        """r5/H1: StepStone renombra `items` (p. ej. a `results`) → antes
        `empty` con 0 issues — fuente ROTA presentada como SECA (material)."""
        self._assert_blob_records_single_issue(
            '{"searchResults": {"results": [{"id": 1, "title": "Dev", '
            '"url": "/job/dev"}]}}'
        )

    def test_items_no_lista_registra_issue(self):
        """r5/H1: `items` no-lista → antes `empty` con 0 issues. El payload 42
        NO es iterable y por eso discrimina el guard de tipo: sin él, el `for`
        lanzaría TypeError. El string "maintenance" solo NO discrimina — es
        iterable y el guard final "ninguno parseable" lo enmascara."""
        self._assert_blob_records_single_issue('{"searchResults": {"items": 42}}')
        self._assert_blob_records_single_issue(
            '{"searchResults": {"items": "maintenance"}}'
        )

    def test_items_de_strings_registra_issue(self):
        """r5/H1: lista no vacía cuyos elementos no son objetos → todos caían
        en el `continue` y el run salía `empty` con 0 issues."""
        self._assert_blob_records_single_issue(
            '{"searchResults": {"items": ["a", "b", "c"]}}'
        )

    def test_items_sin_title_url_registra_issue(self):
        """r5/H1: objetos reales pero sin title/url parseables (renombrado de
        campos) → guard "N elementos y ninguno parseable" como financejobs."""
        self._assert_blob_records_single_issue(
            '{"searchResults": {"items": [{"id": 1}, {"id": 2}]}}'
        )

    def test_items_vacios_es_empty_sin_issue(self):
        """G2 (NO puede romperse): la búsqueda sin resultados real devuelve
        `items: []` con `total: 0` — vacío legítimo, CERO issues."""
        diag.begin()
        soup = self._soup_with_blob(
            '{"searchResults": {"items": [], "meta": {"total": 0}}}'
        )
        assert IrishJobsScraper().parse_listing_page(soup) == []
        assert diag.issues() == []
        assert diag.classify(0, diag.issues()) == "empty"

    async def test_meta_degenerado_no_tumba_la_cosecha(self):
        """r5/H1 (letra de G1): con stubs válidos, un `meta` no-dict hacía que
        `meta.get("total")` lanzara AttributeError y tumbara el host entero —
        las ofertas ya parseadas se perdían. Ahora solo se pierde el corte por
        total (siguen cortando la página incompleta y el tope de páginas)."""
        scraper = IrishJobsScraper()
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = (
            "<html><head><script>"
            'window.__PRELOADED_STATE__["app-unifiedResultlist"] = '
            '{"searchResults": {"items": [{"id": 7, "title": "Dev", '
            '"url": "/job/dev/acme-job7"}], "meta": "maintenance"}};'
            "</script></head><body></body></html>"
        )
        client.get = AsyncMock(return_value=resp)
        diag.begin()

        stubs = await scraper._harvest_host(client, "https://www.irishjobs.ie", set())

        # 1 stub < PAGE_SIZE → corta tras la primera página; sin excepción.
        assert len(stubs) == 1
        assert stubs[0]["id"] == 7
        assert diag.issues() == []  # hubo ofertas: no es un fallo de la fuente

    def test_decode_state_literal_truncado_registra_issue(self):
        """r5/H4: contrato de la nueva costura — `_decode_state` recibe el
        offset del literal ya anclado por su llamante y registra el fallo de
        un literal truncado (la rama "sin ancla" duplicada se eliminó: era
        inalcanzable y su mutante sobrevivía a toda la suite)."""
        text = 'window.__PRELOADED_STATE__["app-unifiedResultlist"] = {"searchRes'
        scraper = IrishJobsScraper()
        match = _STATE_ANCHOR_RE.search(text)
        assert match is not None
        diag.begin()
        assert scraper._decode_state(text, match.end(), scraper.LISTING_URL) is None
        issues = diag.issues()
        assert len(issues) == 1
        assert "truncated state literal" in issues[0].detail

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

    def test_normalize_job_has_22_keys(self):
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
            "published_at",
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
        stubs = IrishJobsScraper()._items_to_stubs(
            data,
            "https://www.irishjobs.ie",
            "https://www.irishjobs.ie/jobs/work-from-home?page=1",
        )
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

    # ------------------------------------------------------------------
    # Diagnóstico por host (VD.10, H3)
    # ------------------------------------------------------------------

    async def test_failed_host_records_its_own_url(self):
        # Fuente de DOS hosts: el issue debe culpar a la URL del host que
        # cayó. Sin `url=` en _request_with_retry, el fallback atribuía los
        # fallos de jobs.ie a la LISTING_URL de irishjobs.ie (el diagnóstico
        # mentía sobre cuál de los dos estaba roto).
        scraper = IrishJobsScraper()
        client = MagicMock()
        resp = MagicMock()
        resp.status_code = 404  # no-200 fuera de BLOCK_STATUS: sin retry ni BD
        client.get = AsyncMock(return_value=resp)
        diag.begin()

        stubs = await scraper._harvest_host(client, "https://www.jobs.ie", set())

        assert stubs == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].status == 404
        assert issues[0].url == "https://www.jobs.ie/jobs/work-from-home?page=1"
