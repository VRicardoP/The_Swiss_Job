"""Tests for all 7 scraper normalize_job + parse_listing_page methods."""

from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.financejobs import FinancejobsScraper
from scrapers.gastrojob import GastrojobScraper
from scrapers.gastrojob import _resolve_job_url as _resolve_gastrojob_url
from scrapers.medjobs import MedJobsScraper
from scrapers.myscience import MyScienceScraper
from scrapers.schuljobs import SchulJobsScraper
from scrapers.stelle_admin import StelleAdminScraper, _resolve_job_url
from scrapers.tes import TESScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _assert_normalized(result: dict, source: str) -> None:
    """Common assertions for all normalized job dicts."""
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


# ---------------------------------------------------------------------------
# myScience.ch
# ---------------------------------------------------------------------------


class TestMyScienceScraper:
    def test_source_name(self):
        assert MyScienceScraper().get_source_name() == "myscience"

    def test_parse_listing_page(self):
        html = (FIXTURES / "myscience_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = MyScienceScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Research Scientist in Machine Learning"
        assert stubs[0]["company"] == "ETH Zurich"
        assert stubs[0]["location"] == "Zurich"
        assert "detail_url" in stubs[0]
        assert (
            "/jobs/id69242-research_scientist-eth_zurich-zurich"
            in stubs[0]["detail_url"]
        )

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert MyScienceScraper().parse_listing_page(soup) == []

    def test_parse_job_detail(self):
        html = (FIXTURES / "myscience_detail.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        detail = MyScienceScraper().parse_job_detail(soup)
        assert "description" in detail
        assert "machine learning" in detail["description"].lower()
        assert detail.get("logo", "").endswith("ethz.svg")
        assert detail.get("location") == "Zurich, Rämistrasse 101"
        assert detail.get("employment_type") == "80% - 100%"

    def test_normalize_job(self):
        raw = {
            "title": "Research Scientist",
            "company": "ETH Zurich",
            "url": "https://myscience.ch/jobs/id123",
            "location": "Zurich",
            "description": "Conduct ML research with Python and PyTorch.",
        }
        result = MyScienceScraper().normalize_job(raw)
        _assert_normalized(result, "myscience")
        assert result["title"] == "Research Scientist"
        assert result["company"] == "ETH Zurich"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Postdoc",
            "company": "",
            "url": "https://myscience.ch/jobs/id999",
        }
        result = MyScienceScraper().normalize_job(raw)
        _assert_normalized(result, "myscience")

    def test_build_listing_url(self):
        s = MyScienceScraper()
        assert s.build_listing_url(1, "") == "https://www.myscience.ch/jobs?p=1"
        assert s.build_listing_url(3, "physics") == "https://www.myscience.ch/jobs?p=3"


# ---------------------------------------------------------------------------
# Financejobs.ch
# ---------------------------------------------------------------------------


class TestFinancejobsScraper:
    def test_source_name(self):
        assert FinancejobsScraper().get_source_name() == "financejobs"

    def test_parse_listing_page_current_structure(self):
        """Estructura ACTUAL de Next.js: props.pageProps.jobsSSR (sin
        initialProps). Fixture capturado en vivo el 2026-08-14 (VD.7) — con la
        ruta antigua este parseo devolvía SIEMPRE lista vacía."""
        html = (FIXTURES / "financejobs_listing_current.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Junior Data Analyst im Controlling"
        assert stubs[0]["company"] == "PharmaFocus AG"
        assert stubs[0]["url"].endswith("/de/job/14697110")
        # El datePosted del portal debe llegar al stub (alimenta published_at).
        assert stubs[0]["date_posted"] == "2026-08-14T16:32:34+00:00"

    def test_parse_listing_page_legacy_structure(self):
        """Tolerancia a la estructura HISTÓRICA (props.initialProps.pageProps):
        Next.js ya cambió la forma una vez y puede revertirla. Un arreglo
        ingenuo que solo mueva la ruta rompería este fixture."""
        html = (FIXTURES / "financejobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Senior Financial Analyst"
        assert stubs[0]["company"] == "UBS Group AG"
        assert "Zurich" in stubs[0]["location"]
        assert "/de/job/1846066401" in stubs[0]["url"]
        assert stubs[0]["salary_original"] == "120000-150000 CHF"

    def test_normalize_publishes_aware_datetime(self):
        """El published_at (datePosted, VD.7) deja de ser código muerto: con el
        fixture real debe salir timezone-aware para la ventana de cosecha."""
        html = (FIXTURES / "financejobs_listing_current.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = FinancejobsScraper().parse_listing_page(soup)
        result = FinancejobsScraper().normalize_job(stubs[0])
        assert result["published_at"] is not None
        assert result["published_at"].tzinfo is not None

    def test_unknown_structure_records_fetch_issue(self):
        """Si NINGUNA ruta conocida hacia jobsSSR existe, el fallo debe ser
        VISIBLE (error de fetch), no un "0 ofertas" silencioso — el bucle
        exacto que apagó esta fuente (VD.7)."""
        from utils import fetch_diagnostics as diag

        payload = '{"props": {"pageProps": {"otraCosa": 1}, "__N_SSP": true}}'
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "jobsSSR" in issues[0].detail

    def test_parse_listing_page_no_next_data(self):
        """Sin __NEXT_DATA__ tampoco hay "vacío legítimo": se registra fallo."""
        from utils import fetch_diagnostics as diag

        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        assert len(diag.issues()) == 1

    def test_empty_jobs_ssr_is_legitimate_empty(self):
        """jobsSSR presente con 0 ofertas ⇒ vacío legítimo, SIN fallo de fetch."""
        from utils import fetch_diagnostics as diag

        payload = '{"props": {"pageProps": {"jobsSSR": {"jobs": []}}, "__N_SSP": true}}'
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        assert diag.issues() == []

    def test_null_jobs_records_fetch_issue(self):
        """jobsSSR.jobs = null NO es "0 ofertas": sin guard de tipo lanzaba
        TypeError ('NoneType' is not iterable) que escapaba hasta
        scraping_tasks y dejaba el run SIN clasificar en source_health."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": null}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "jobsSSR.jobs" in issues[0].detail

    def test_non_list_jobs_records_fetch_issue(self):
        """jobsSSR.jobs con tipo inesperado (una cadena) es estructura
        desconocida: sin guard se iteraba carácter a carácter y salía []
        con 0 issues ⇒ veredicto `empty` falso."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": "oops"}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "jobsSSR.jobs" in issues[0].detail

    def test_missing_jobs_key_records_fetch_issue(self):
        """jobsSSR presente pero SIN la clave `jobs` no es vacío legítimo: en
        el portal real `jobs` existe siempre (sonda 2026-08-14), así que su
        ausencia es estructura desconocida y debe ser VISIBLE."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"resultCount": 0}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "jobsSSR.jobs" in issues[0].detail

    def test_non_object_next_data_records_fetch_issue(self):
        """__NEXT_DATA__ con JSON válido pero no-objeto (una lista): sin guard,
        _extract_jobs_ssr lanzaba AttributeError no capturado con la misma
        invisibilidad en source_health que el caso jobs=null."""
        from utils import fetch_diagnostics as diag

        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            "[1, 2, 3]</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "__NEXT_DATA__" in issues[0].detail

    def test_null_location_keeps_offer(self):
        """`location: null` es JSON perfectamente normal (oferta sin
        ubicación) y la clave EXISTE, así que `job.get("location", "")`
        devolvía None y el `.strip()` tumbaba la página ENTERA con
        AttributeError que escapaba hasta scraping_tasks (run sin veredicto).
        La oferta debe CONSERVARSE con ubicación vacía, sin fallo de fetch."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": ['
            '{"jobId": "111", "title": "Analyst", "companyName": "UBS",'
            ' "location": null, "description": "Bank job"}'
            ']}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert len(stubs) == 1
        assert stubs[0]["location"] == ""
        assert stubs[0]["title"] == "Analyst"
        assert diag.issues() == []

    def test_non_string_field_degrades_offer_not_page(self):
        """Un campo de texto con tipo inesperado (companyName numérico)
        reventaba en `.strip()` y una sola oferta rara tumbaba la página
        entera. Debe degradar SOLO ese campo: la oferta sobrevive con el
        fallback "Unknown"."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": ['
            '{"jobId": "222", "title": "Controller", "companyName": 12345,'
            ' "location": "Zug"}'
            ']}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert len(stubs) == 1
        assert stubs[0]["company"] == "Unknown"
        assert diag.issues() == []

    def test_no_parseable_offers_records_fetch_issue(self):
        """`jobs` NO vacía de la que no sale ni un stub (p. ej. el portal
        renombró `title` → `jobTitle`): todo caía en los `continue`
        silenciosos y el run salía `empty` con 0 issues — el bug original
        de VD.7 con otra ropa. Debe ser VISIBLE como fallo de estructura."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": ["raro", "raro2"]}},'
            ' "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ninguno es parseable" in issues[0].detail

    def test_partially_degraded_page_keeps_good_offer(self):
        """Degradación PARCIAL (1 oferta buena + 1 con title no-string): la
        buena se devuelve y NO se registra fallo de estructura — con >=1 stub
        el run sigue siendo `ok`, no `error`."""
        from utils import fetch_diagnostics as diag

        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": ['
            '{"jobId": "333", "title": "Risk Manager", "companyName": "CS",'
            ' "location": "Basel"},'
            '{"jobId": "444", "title": {"weird": true}, "companyName": "X"}'
            ']}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert len(stubs) == 1
        assert stubs[0]["title"] == "Risk Manager"
        assert diag.issues() == []

    def test_page_size_matches_portal(self):
        """El portal pagina de 10 en 10 (jobsSSR.pageSize, sonda 2026-08-14);
        con PAGE_SIZE=20 el motor cortaba la paginación tras la página 1."""
        assert FinancejobsScraper.PAGE_SIZE == 10

    @staticmethod
    def _page_with_jobs(jobs_json: str) -> BeautifulSoup:
        """Página __NEXT_DATA__ mínima con la lista de jobs dada (JSON)."""
        payload = (
            '{"props": {"pageProps": {"jobsSSR": {"jobs": '
            f"{jobs_json}"
            '}}, "__N_SSP": true}}'
        )
        html = (
            '<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{payload}</script></body></html>"
        )
        return BeautifulSoup(html, "lxml")

    def test_job_id_invalido_no_emite_url(self):
        """Fase 3/H5: el jobId se interpolaba sin validar — tipos arbitrarios,
        query, fragmento o traversal producían URLs distintas para la misma
        identidad (hash title|company|url + ix_jobs_url). El portal usa ids
        ENTEROS (fixtures + sonda): lo que no sea entero no negativo o
        decimal ASCII ⇒ oferta sin URL utilizable ⇒ se salta; con el resto
        de la página sana no hay issue."""
        from utils import fetch_diagnostics as diag

        soup = self._page_with_jobs(
            '[{"jobId": {"x": 1}, "title": "Dict Id", "companyName": "A"},'
            ' {"jobId": "42?utm=x", "title": "Query Id", "companyName": "B"},'
            ' {"jobId": "42#frag", "title": "Frag Id", "companyName": "C"},'
            ' {"jobId": "../admin", "title": "Traversal Id", "companyName": "D"},'
            ' {"jobId": true, "title": "Bool Id", "companyName": "E"},'
            ' {"jobId": -5, "title": "Negative Id", "companyName": "F"},'
            ' {"jobId": 14697110, "title": "Valid Int Id", "companyName": "G"}]'
        )
        diag.begin()
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert [s["title"] for s in stubs] == ["Valid Int Id"]
        assert stubs[0]["url"] == "https://www.financejobs.ch/de/job/14697110"
        assert diag.issues() == []

    def test_jc_job_id_fallback_exige_uuid_canonico(self):
        """Fase 3/H5: el fallback jcJobId solo se interpola si es un UUID
        canónico en minúsculas (la forma real del portal); cualquier otra
        cosa deja la oferta sin URL y se salta."""
        soup = self._page_with_jobs(
            '[{"jcJobId": "cbbceba0-ab30-4f23-91fc-9fe4cf3bc8a0",'
            ' "title": "UUID Valido", "companyName": "A"},'
            ' {"jcJobId": "../admin", "title": "UUID Traversal", "companyName": "B"},'
            ' {"jcJobId": "CBBCEBA0-AB30-4F23-91FC-9FE4CF3BC8A0",'
            ' "title": "UUID Mayusculas", "companyName": "C"}]'
        )
        stubs = FinancejobsScraper().parse_listing_page(soup)
        assert [s["title"] for s in stubs] == ["UUID Valido"]
        assert stubs[0]["url"] == (
            "https://www.financejobs.ch/de/job/cbbceba0-ab30-4f23-91fc-9fe4cf3bc8a0"
        )

    def test_pagina_entera_con_ids_invalidos_registra_issue(self):
        """Si NINGUNA oferta de una página no vacía tiene id utilizable, el
        guard existente ("ninguno es parseable") la hace VISIBLE: ids rotos
        en masa son un cambio de forma del portal, no '0 ofertas'."""
        from utils import fetch_diagnostics as diag

        soup = self._page_with_jobs(
            '[{"jobId": "../admin", "title": "T1", "companyName": "A"},'
            ' {"jobId": "42?utm=x", "title": "T2", "companyName": "B"}]'
        )
        diag.begin()
        assert FinancejobsScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ninguno es parseable" in issues[0].detail

    def test_normalize_job(self):
        raw = {
            "title": "Portfolio Manager",
            "company": "Pictet",
            "url": "https://financejobs.ch/de/job/123",
            "location": "Geneva",
            "description": "Manage client portfolios in wealth management.",
            "salary_original": "150000 CHF",
            "employment_type": "Full-time",
        }
        result = FinancejobsScraper().normalize_job(raw)
        _assert_normalized(result, "financejobs")
        assert result["salary_original"] == "150000 CHF"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Analyst",
            "company": "",
            "url": "https://financejobs.ch/de/job/456",
        }
        result = FinancejobsScraper().normalize_job(raw)
        _assert_normalized(result, "financejobs")

    def test_fetch_details_disabled(self):
        assert FinancejobsScraper.FETCH_DETAILS is False


# ---------------------------------------------------------------------------
# Gastrojob.ch
# ---------------------------------------------------------------------------


class TestGastrojobScraper:
    def test_source_name(self):
        assert GastrojobScraper().get_source_name() == "gastrojob"

    def test_parse_listing_page_ajax_fragment(self):
        """Fragmento REAL del endpoint AJAX (capturado 2026-08-15, VD.4b):
        10 ofertas por página. El scraper anterior (selectores .job-item /
        .stellenangebot / hrefs /stelle/) devolvía 0 stubs contra este DOM."""
        html = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = GastrojobScraper().parse_listing_page(soup)
        assert len(stubs) == 10
        first = stubs[0]
        assert first["title"] == "Pâtissier(ère)"
        assert first["company"] == "Cinq Sens Sàrl"
        assert first["location"] == "Neuenburg"
        assert first["employment_type"] == "Teilzeit/Vollzeit"
        # href relativo del fragmento resuelto con urljoin sobre BASE_URL.
        assert first["url"] == "https://www.gastrojob.ch/stellen/stelleninserat/55327"
        assert first["detail_url"] == first["url"]
        assert first["source_id"] == "55327"
        # "Erstmals aktiviert: 14.08.2026 (14:08)" — hora suiza (CEST = +02:00).
        assert first["date_posted"] == "2026-08-14T14:08:00+02:00"
        # Jornada vacía (" bei Landgasthaus... in Aargau") degrada SOLO ese campo.
        koch = next(s for s in stubs if s["source_id"] == "55246")
        assert koch["employment_type"] is None
        assert koch["company"] == "Landgasthaus zum Hirschen AG"
        assert koch["location"] == "Aargau"
        # Ninguna oferta puede salir con la URL base (la colisión de VD.1).
        assert all(s["url"].rstrip("/") != "https://www.gastrojob.ch" for s in stubs)

    def test_parse_listing_page_ajax_p2_ofertas_distintas(self):
        """La página 2 real trae otras 10 ofertas: la paginación del endpoint
        AJAX cambia el contenido de verdad (no repite la página 1)."""
        p1 = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        p2 = (FIXTURES / "gastrojob_listing_ajax_p2.html").read_text()
        stubs_p1 = GastrojobScraper().parse_listing_page(BeautifulSoup(p1, "lxml"))
        stubs_p2 = GastrojobScraper().parse_listing_page(BeautifulSoup(p2, "lxml"))
        assert len(stubs_p2) == 10
        assert {s["url"] for s in stubs_p1}.isdisjoint(s["url"] for s in stubs_p2)

    def test_hrefs_no_utilizables_descartados(self):
        """Una oferta sin URL propia no es utilizable (regla de stelle_admin,
        VD.1) y el host se valida para no abrir un bypass: href vacío,
        protocolo-relativo a host ajeno, esquema no-http, userinfo, backslash
        (diferencial urllib/WHATWG) y paths ajenos ⇒ None."""
        assert _resolve_gastrojob_url("") is None
        assert _resolve_gastrojob_url(None) is None
        assert _resolve_gastrojob_url("//evil.com/stellen/stelleninserat/1") is None
        assert _resolve_gastrojob_url("javascript:alert(1)") is None
        assert (
            _resolve_gastrojob_url(
                "https://x@www.gastrojob.ch/stellen/stelleninserat/2"
            )
            is None
        )
        assert (
            _resolve_gastrojob_url(
                "https://evil.com\\@www.gastrojob.ch/stellen/stelleninserat/3"
            )
            is None
        )
        # Caso load-bearing del `\` SIN `@` (VD.4b H2): urllib parsea el
        # hostname como "evil.com\.gastrojob.ch", que TERMINA en
        # ".gastrojob.ch" y pasaría el check de host; un navegador WHATWG
        # corta el netloc en `\` y navegaría a evil.com. El caso con `@` de
        # arriba NO fija la defensa del backslash (lo caza el check de
        # userinfo); este sí.
        assert (
            _resolve_gastrojob_url(
                "https://evil.com\\.gastrojob.ch/stellen/stelleninserat/1"
            )
            is None
        )
        # Path que no es una oferta: portada, listado, id no numérico.
        assert _resolve_gastrojob_url("/") is None
        assert _resolve_gastrojob_url("/stellen") is None
        assert _resolve_gastrojob_url("/stellen/stelleninserat/abc") is None
        # Control: el href legítimo del fixture resuelve a URL + source_id.
        assert _resolve_gastrojob_url("/stellen/stelleninserat/55327") == (
            "https://www.gastrojob.ch/stellen/stelleninserat/55327",
            "55327",
        )

    def test_hrefs_maliciosos_no_emiten_stub(self):
        """Extremo a extremo: hrefs de ataque que SÍ casan con el selector de
        ítems no emiten stub; el legítimo del mismo fragmento sobrevive."""
        html = """
        <div data-mxn-advertisements-count="4"></div>
        <a href="//evil.com/stellen/stelleninserat/1">
          <article class="row column"><div><h2>Phishing Koch</h2></div></article>
        </a>
        <a href="javascript:alert(1)//stellen/stelleninserat/2">
          <article class="row column"><div><h2>XSS Kellner</h2></div></article>
        </a>
        <a href="https://x@www.gastrojob.ch/stellen/stelleninserat/3">
          <article class="row column"><div><h2>Spoof Chef</h2></div></article>
        </a>
        <a href="/stellen/stelleninserat/55327">
          <article class="row column"><div>
            <h2>Pâtissier(ère)</h2>
            <div class="description">Teilzeit bei Cinq Sens Sàrl in Neuenburg</div>
          </div></article>
        </a>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = GastrojobScraper().parse_listing_page(soup)
        assert len(stubs) == 1
        assert (
            stubs[0]["url"] == "https://www.gastrojob.ch/stellen/stelleninserat/55327"
        )

    def test_href_malformado_no_revienta_la_pagina(self):
        """VD.4b H1: un `[` en posición de autoridad hace que urlsplit lance
        ValueError ("Invalid IPv6 URL"). Sin captura, la excepción escapa de
        parse_listing_page → scraper_engine → scraping_tasks saltándose
        diag.classify y source_health: el run se queda sin veredicto. Un href
        malformado debe degradar ESA oferta, nunca la página ni el run."""
        assert _resolve_gastrojob_url("//[evil/stellen/stelleninserat/1") is None
        # Extremo a extremo: el href malformado casa con el selector de ítems
        # pero no revienta el parseo; el legítimo del mismo fragmento vive.
        html = """
        <div data-mxn-advertisements-count="2"></div>
        <a href="//[evil/stellen/stelleninserat/1">
          <article class="row column"><div><h2>Crash Koch</h2></div></article>
        </a>
        <a href="/stellen/stelleninserat/55327">
          <article class="row column"><div><h2>Pâtissier(ère)</h2></div></article>
        </a>
        """
        stubs = GastrojobScraper().parse_listing_page(BeautifulSoup(html, "lxml"))
        assert len(stubs) == 1
        assert stubs[0]["source_id"] == "55327"

    def test_url_emitida_canonica(self):
        """VD.4b H4: la URL emitida se RECONSTRUYE canónica (esquema y host
        fijos, sin query/fragment/puerto) porque el hash de dedup
        (title|company|url) e ix_jobs_url comparan la URL literal: un
        `?tracking=` del portal crearía una fila nueva por oferta. Y el id se
        restringe a [0-9]: `\\d` casa dígitos unicode (٢١)."""
        canonical = (
            "https://www.gastrojob.ch/stellen/stelleninserat/55327",
            "55327",
        )
        assert _resolve_gastrojob_url("/stellen/stelleninserat/55327?x=1") == canonical
        assert _resolve_gastrojob_url("/stellen/stelleninserat/55327#frag") == canonical
        assert (
            _resolve_gastrojob_url("https://GASTROJOB.CH/stellen/stelleninserat/55327")
            == canonical
        )
        assert (
            _resolve_gastrojob_url(
                "https://gastrojob.ch:8443/stellen/stelleninserat/55327"
            )
            == canonical
        )
        # Dígitos árabes-índicos (U+0662 U+0661): un id no-ASCII nunca es una
        # oferta del portal y no debe emitirse tal cual.
        assert _resolve_gastrojob_url("/stellen/stelleninserat/٢١") is None

    def test_href_duplicado_emite_un_solo_stub(self):
        """VD.4b H6: el mismo href repetido en el fragmento (p. ej. un anuncio
        destacado que también sale en el listado) debe emitir UN solo stub —
        dos filas con la misma URL colisionarían en ix_jobs_url dentro del
        mismo lote (la colisión exacta de stelle_admin/VD.1)."""
        html = """
        <div data-mxn-advertisements-count="2"></div>
        <a href="/stellen/stelleninserat/55327">
          <article class="row column"><div><h2>Pâtissier(ère)</h2></div></article>
        </a>
        <a href="/stellen/stelleninserat/55327">
          <article class="row column"><div><h2>Pâtissier(ère)</h2></div></article>
        </a>
        """
        stubs = GastrojobScraper().parse_listing_page(BeautifulSoup(html, "lxml"))
        assert len(stubs) == 1

    def test_pagina_fuera_de_rango_no_registra_issue(self):
        """No-regresión G2 (VD.4b H5, rev. fase 3): la página fuera de rango
        real devuelve el contador TOTAL (1104 ⇒ 111 páginas) con 0 ítems —
        verificado en vivo 2026-08-15 (la 112+ llega así y el widget del
        portal termina en la 111). "Fuera de rango" se decide por el número
        de página frente a ceil(anunciadas/PAGE_SIZE): fin de paginación
        legítimo, sin falso fetch-issue."""
        from utils import fetch_diagnostics as diag

        scraper = GastrojobScraper()
        p1 = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        diag.begin()
        scraper.build_listing_url(1, "")
        assert len(scraper.parse_listing_page(BeautifulSoup(p1, "lxml"))) == 10
        fuera_de_rango = '<div data-mxn-advertisements-count="1104"></div>'
        scraper.build_listing_url(112, "")  # ceil(1104/10) = 111 ⇒ 112 fuera
        assert scraper.parse_listing_page(BeautifulSoup(fuera_de_rango, "lxml")) == []
        assert diag.issues() == []

    def test_pagina_vacia_dentro_de_rango_registra_issue(self):
        """Fase 3/H3: tras una página válida, un fragmento vacío con contador
        en una página DENTRO del rango esperado (la 2 de 111) se aceptaba
        como fin de paginación — un cambio de markup a mitad de run se
        tragaba esa página y todas las siguientes en silencio. Debe ser
        fallo VISIBLE de estructura."""
        from utils import fetch_diagnostics as diag

        scraper = GastrojobScraper()
        p1 = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        diag.begin()
        scraper.build_listing_url(1, "")
        assert len(scraper.parse_listing_page(BeautifulSoup(p1, "lxml"))) == 10
        vacia_en_rango = '<div data-mxn-advertisements-count="1104"></div>'
        scraper.build_listing_url(2, "")  # 2 <= 111: dentro del rango
        assert scraper.parse_listing_page(BeautifulSoup(vacia_en_rango, "lxml")) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "estructura desconocida" in issues[0].detail

    def test_pagina_de_solo_anuncios_partner_es_vacio_legitimo(self):
        """Fija la decisión de la sonda 2026-08-15: el listado real termina en
        páginas compuestas SOLO por anuncios de partner externo
        (/stellen/externe-partner/, "powered by hoteljob-schweiz.de") que el
        parser no cosecha a propósito — las 110-111 de 111 llegan así con
        fuente SANA. Dentro del rango, esa composición es estructura
        reconocida con 0 ofertas propias: sin issue (marcarla rota sería el
        fallo inverso al que la fase vino a eliminar)."""
        from utils import fetch_diagnostics as diag

        # Réplica mínima de la página 110 real (sonda en vivo).
        html = """
        <div data-mxn-advertisements-count="1108"></div>
        <a href="/stellen/externe-partner/47577">
          <article class="row column"><div>
            <h2>Lehrstelle Restaurationsfachfrau/ -mann EFZ</h2>
            <div class="description">Praktikum bei Hotel Blausee in Bern</div>
            <div class="description"><i>powered by hoteljob-schweiz.de</i></div>
          </div></article>
        </a>
        """
        scraper = GastrojobScraper()
        diag.begin()
        scraper.build_listing_url(110, "")  # dentro del rango (<= 111)
        assert scraper.parse_listing_page(BeautifulSoup(html, "lxml")) == []
        assert diag.issues() == []

    def test_enlaces_propios_rotos_registran_issue_incluso_fuera_de_rango(self):
        """Fase 3/H3: un fragmento que CONTIENE enlaces de oferta propia y
        produce 0 stubs es ilegible se mire como se mire — el contador y el
        número de página son irrelevantes para ese veredicto. Antes, el corte
        "fuera de rango" se evaluaba primero y silenciaba estos fragmentos
        (E1: contador=0; E2: contador=10 ⇒ última esperada la 1, página 2;
        E7: fuera de rango del contador real)."""
        from utils import fetch_diagnostics as diag

        # <a> propio SIN h2 ⇒ 0 stubs pese al enlace reconocible.
        broken_link_html = (
            '<div data-mxn-advertisements-count="{count}"></div>'
            '<a href="/stellen/stelleninserat/123">'
            '<article class="row column"><div>Sin titulo</div></article></a>'
        )
        for count, page in [(0, 1), (10, 2), (1104, 112)]:
            scraper = GastrojobScraper()
            diag.begin()
            scraper.build_listing_url(page, "")
            html = broken_link_html.format(count=count)
            assert scraper.parse_listing_page(BeautifulSoup(html, "lxml")) == []
            issues = diag.issues()
            assert len(issues) == 1, (count, page)
            assert "ninguno es parseable" in issues[0].detail

    def test_contador_negativo_registra_issue(self):
        """Fase 3/H4: int("-5") parsea y ceil(-5/10) = 0 dejaba CUALQUIER
        página "fuera de rango" ⇒ el fallo salía como vacío legítimo
        silencioso. Un contador negativo es tan ilegible como uno no
        numérico y registra el mismo issue."""
        from utils import fetch_diagnostics as diag

        html = '<div data-mxn-advertisements-count="-5"></div>'
        diag.begin()
        assert GastrojobScraper().parse_listing_page(BeautifulSoup(html, "lxml")) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ilegible" in issues[0].detail

    def test_contador_laxo_de_int_registra_issue(self):
        """Fase 3 r2/H3: int() es más laxo que la regla [0-9] del propio
        fichero — "١٠٤" (dígitos árabes) parsea como 104, y "+20", " 20 " y
        "1_0" también parsean. El rango derivado de un contador así puede
        SILENCIAR páginas como falsos "fuera de rango" (aquí: página 500,
        que con el contador parseado salía como fin de paginación legítimo
        con 0 issues). Debe aplicarse el mismo [0-9] estricto que
        _JOB_PATH_RE y _ERSTMALS_RE."""
        from utils import fetch_diagnostics as diag

        for raw in ("١٠٤", "+20", " 20 ", "1_0"):
            html = f'<div data-mxn-advertisements-count="{raw}"></div>'
            scraper = GastrojobScraper()
            diag.begin()
            scraper.build_listing_url(500, "")  # "fuera de rango" del bogus
            assert scraper.parse_listing_page(BeautifulSoup(html, "lxml")) == []
            issues = diag.issues()
            assert len(issues) == 1, raw
            assert "ilegible" in issues[0].detail

    def test_ultima_pagina_esperada_vacia_registra_issue(self):
        """Fase 3/H5 (mutante superviviente: `>` → `>=` en el rango): la
        frontera exacta — 1104 anunciadas ⇒ última esperada la 111 — está
        DENTRO del rango: vacía y sin ningún enlace debe registrar issue;
        solo la 112+ es fin de paginación legítimo."""
        from utils import fetch_diagnostics as diag

        scraper = GastrojobScraper()
        html = '<div data-mxn-advertisements-count="1104"></div>'
        diag.begin()
        scraper.build_listing_url(111, "")  # ceil(1104/10) = 111: el borde
        assert scraper.parse_listing_page(BeautifulSoup(html, "lxml")) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "1104" in issues[0].detail

    def test_enlaces_propios_rotos_dominan_sobre_partner(self):
        """Fase 3/H5 (mutante superviviente: invertir el orden
        partner/propios): con enlaces propios rotos Y anuncios de partner en
        el MISMO fragmento debe dominar el veredicto de ilegible (issue) —
        la rama partner solo puede silenciar fragmentos sin ningún enlace
        propio."""
        from utils import fetch_diagnostics as diag

        html = """
        <div data-mxn-advertisements-count="1108"></div>
        <a href="/stellen/stelleninserat/123">
          <article class="row column"><div>Sin titulo</div></article>
        </a>
        <a href="/stellen/externe-partner/47577">
          <article class="row column"><div>
            <h2>Lehrstelle EFZ</h2>
            <div class="description"><i>powered by hoteljob-schweiz.de</i></div>
          </div></article>
        </a>
        """
        scraper = GastrojobScraper()
        diag.begin()
        scraper.build_listing_url(10, "")  # dentro de rango (<= 111)
        assert scraper.parse_listing_page(BeautifulSoup(html, "lxml")) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ninguno es parseable" in issues[0].detail

    def test_contador_ilegible_registra_issue(self):
        """Fija la decisión: un contador no numérico no permite derivar el
        rango de páginas ⇒ estructura desconocida, fallo VISIBLE."""
        from utils import fetch_diagnostics as diag

        html = '<div data-mxn-advertisements-count="muchas"></div>'
        diag.begin()
        assert GastrojobScraper().parse_listing_page(BeautifulSoup(html, "lxml")) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "ilegible" in issues[0].detail

    def test_markup_sin_contador_tras_pagina_valida_registra_issue(self):
        """VD.4b r2-H1: el guard de fin de paginación solo puede silenciar
        fragmentos CON contador — la página fuera de rango real siempre lo
        trae. Un markup totalmente desconocido y SIN contador tras una página
        buena es un cambio de estructura a mitad de run: fallo VISIBLE, no
        fin de paginación."""
        from utils import fetch_diagnostics as diag

        scraper = GastrojobScraper()
        p1 = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        diag.begin()
        assert len(scraper.parse_listing_page(BeautifulSoup(p1, "lxml"))) == 10
        desconocido = "<html><body><div class='otro-markup'>x</div></body></html>"
        assert scraper.parse_listing_page(BeautifulSoup(desconocido, "lxml")) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "estructura desconocida" in issues[0].detail

    async def test_cambio_de_estructura_a_mitad_de_run_es_visible(self):
        """Fase 3/H3, extremo a extremo por fetch_jobs: la página 1 parsea
        bien y la 2 llega con markup nuevo que CONSERVA el contador (1104 ⇒
        111 páginas: la 2 está dentro del rango). Antes se aceptaba como fin
        de paginación y se perdían esa página y las siguientes en silencio;
        ahora el run conserva las 10 ofertas de la página 1 Y registra el
        fallo (`ok` con issues = degradación parcial visible). El estado de
        página se rearma entre runs de la MISMA instancia: la página 1 rota
        del run 2 también registra su fallo."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from utils import fetch_diagnostics as diag

        scraper = GastrojobScraper()
        scraper._pre_check = AsyncMock(return_value=True)
        scraper._reset_compliance_blocks = AsyncMock()
        # Solo el listado importa aquí: sin detalle y sin pausas.
        scraper.FETCH_DETAILS = False
        scraper.RATE_LIMIT_SECONDS = 0.01

        def _resp(html: str) -> MagicMock:
            response = MagicMock()
            response.status_code = 200
            response.text = html
            return response

        p1 = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        vacia_con_contador = '<div data-mxn-advertisements-count="1104"></div>'

        # Run 1: página 1 buena + página 2 vacía DENTRO del rango ⇒ las 10
        # ofertas se conservan y el cambio de estructura queda registrado.
        diag.begin()
        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            side_effect=[_resp(p1), _resp(vacia_con_contador)],
        ):
            assert len(await scraper.fetch_jobs("")) == 10
        issues = diag.issues()
        assert len(issues) == 1
        assert "estructura desconocida" in issues[0].detail

        # Run 2 (MISMA instancia): la página 1 llega rota ⇒ también visible.
        diag.begin()
        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            side_effect=[_resp(vacia_con_contador)],
        ):
            assert await scraper.fetch_jobs("") == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "estructura desconocida" in issues[0].detail

    def test_published_at_aware_desde_hora_suiza(self):
        """El listado imprime la hora LOCAL suiza sin zona: 14:08 CEST debe
        salir como 12:08 UTC timezone-aware, no asumirse UTC (±1-2 h)."""
        html = (FIXTURES / "gastrojob_listing_ajax_p1.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = GastrojobScraper().parse_listing_page(soup)
        result = GastrojobScraper().normalize_job(stubs[0])
        assert result["published_at"] is not None
        assert result["published_at"].tzinfo is not None
        assert result["published_at"] == datetime(
            2026, 8, 14, 12, 8, tzinfo=timezone.utc
        )

    def test_fecha_con_digitos_unicode_no_casa(self):
        """VD.4b r2-H3: misma regla que la URL canónica — `\\d` casa dígitos
        unicode (١٤.٠٨) que el portal nunca imprime; el patrón de fecha se
        restringe a [0-9] para no fabricar un published_at a partir de una
        entrada que no es del portal."""
        from scrapers.gastrojob import _parse_erstmals_aktiviert

        # Dígitos árabes-índicos en día/mes: no debe casar (con `\\d` casaba
        # y producía "2026-08-14T14:08:00+02:00" vía int()).
        assert (
            _parse_erstmals_aktiviert("Erstmals aktiviert: ١٤.٠٨.2026 (14:08)") is None
        )
        # Control: la forma real del portal sigue casando.
        assert (
            _parse_erstmals_aktiviert("Erstmals aktiviert: 14.08.2026 (14:08)")
            == "2026-08-14T14:08:00+02:00"
        )

    def test_parse_job_detail_microdata(self):
        """Detalle REAL (captura 2026-08-15): microdata schema.org/JobPosting
        con descripción, datePosted, empresa canónica y localidad."""
        html = (FIXTURES / "gastrojob_detail.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        detail = GastrojobScraper().parse_job_detail(soup)
        assert "pâtissier(ère)" in detail["description"].lower()
        assert detail["detail_date_posted"] == "2026-08-14"
        assert detail["detail_company"] == "Cinq Sens Sàrl"
        assert detail["address_locality"] == "Fontaines"

    def test_normalize_combina_listado_y_detalle(self):
        """La empresa del LISTADO manda (identidad estable aunque el detalle
        falle un run); la localidad del detalle se combina con el cantón del
        listado sin perder la extracción de cantón."""
        raw = {
            "title": "Pâtissier(ère)",
            "company": "Cinq Sens Sàrl",
            "location": "Neuenburg",
            "url": "https://www.gastrojob.ch/stellen/stelleninserat/55327",
            "detail_company": "Otra Empresa SA",
            "address_locality": "Fontaines",
        }
        result = GastrojobScraper().normalize_job(raw)
        _assert_normalized(result, "gastrojob")
        assert result["company"] == "Cinq Sens Sàrl"
        assert result["location"] == "Fontaines, Neuenburg"
        assert result["canton"] == "NE"

    def test_normalize_fecha_cae_al_datePosted_del_detalle(self):
        """Sin fecha en el listado, el meta[itemprop=datePosted] del detalle
        (solo YYYY-MM-DD) sigue produciendo published_at timezone-aware."""
        raw = {
            "title": "Koch",
            "company": "X",
            "url": "https://www.gastrojob.ch/stellen/stelleninserat/1",
            "detail_date_posted": "2026-08-14",
        }
        result = GastrojobScraper().normalize_job(raw)
        assert result["published_at"] == datetime(2026, 8, 14, tzinfo=timezone.utc)

    def test_estructura_desconocida_registra_fetch_issue(self):
        """Respuesta 200 NO vacía en la que el parseo no reconoce NADA (ni el
        contador de anuncios): estructura desconocida, no '0 ofertas' — el
        bucle exacto que apagó esta fuente (selectores obsoletos ⇒ 0 stubs ⇒
        soft-block ⇒ kill-switch, VD.4b)."""
        from utils import fetch_diagnostics as diag

        html = "<html><body><div class='otra-cosa'>contenido</div></body></html>"
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert GastrojobScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "estructura desconocida" in issues[0].detail

    def test_contador_a_cero_es_vacio_legitimo(self):
        """El contador real del fragmento a 0 ⇒ vacío legítimo, SIN issue."""
        from utils import fetch_diagnostics as diag

        html = '<div data-mxn-advertisements-count="0"></div>'
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert GastrojobScraper().parse_listing_page(soup) == []
        assert diag.issues() == []

    def test_contador_anuncia_ofertas_sin_stub_registra_issue(self):
        """El portal anuncia ofertas pero ningún ítem es reconocible (p. ej.
        cambió la forma de los <a>): fallo VISIBLE, no `empty` silencioso."""
        from utils import fetch_diagnostics as diag

        html = (
            '<div data-mxn-advertisements-count="1104"></div>'
            '<section class="nuevo-markup">10 Stellen</section>'
        )
        soup = BeautifulSoup(html, "lxml")
        diag.begin()
        assert GastrojobScraper().parse_listing_page(soup) == []
        issues = diag.issues()
        assert len(issues) == 1
        assert "1104" in issues[0].detail

    def test_build_listing_url_endpoint_ajax(self):
        """La URL del run es el endpoint AJAX de paginación del frontend, con
        el mismo percent-encoding que los href de la paginación del sitio."""
        url = GastrojobScraper().build_listing_url(3, "")
        assert url.startswith("https://www.gastrojob.ch/?")
        assert "type=5000" in url
        assert "tx_mxngastrojob_ajax%5Baction%5D=filteredAds" in url
        assert "tx_mxngastrojob_ajax%5B%40widget_0%5D%5BcurrentPage%5D=3" in url

    def test_referer_del_listado(self):
        """El frontend envía Referer en su llamada AJAX; el scraper también."""
        assert (
            GastrojobScraper.DEFAULT_HEADERS["Referer"]
            == "https://www.gastrojob.ch/stellen"
        )

    def test_page_size_matches_portal(self):
        """El fragmento AJAX trae 10 ofertas por página (fixtures p1/p2); con
        PAGE_SIZE=20 el motor cortaría la paginación tras la página 1."""
        assert GastrojobScraper.PAGE_SIZE == 10

    def test_httpx_puro_sin_playwright(self):
        """El endpoint AJAX responde a httpx puro (sonda 2026-08-15): menos
        coste y menos fragilidad que renderizar /stellen con Playwright."""
        assert GastrojobScraper.NEEDS_PLAYWRIGHT is False

    def test_fetch_details_enabled(self):
        assert GastrojobScraper.FETCH_DETAILS is True

    def test_normalize_job(self):
        raw = {
            "title": "Sous Chef",
            "company": "Grand Hotel Zermatt",
            "url": "https://www.gastrojob.ch/stellen/stelleninserat/789",
            "location": "Wallis",
            "description": "Lead the kitchen brigade for our 5-star restaurant.",
        }
        result = GastrojobScraper().normalize_job(raw)
        _assert_normalized(result, "gastrojob")

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Koch",
            "company": "",
            "url": "https://www.gastrojob.ch/stellen/stelleninserat/111",
        }
        result = GastrojobScraper().normalize_job(raw)
        _assert_normalized(result, "gastrojob")
        assert result["company"] == "Unknown"


# ---------------------------------------------------------------------------
# med-jobs.com
# ---------------------------------------------------------------------------


class TestMedJobsScraper:
    def test_source_name(self):
        assert MedJobsScraper().get_source_name() == "medjobs"

    def test_parse_listing_page(self):
        html = (FIXTURES / "medjobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = MedJobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert "Oberärztin" in stubs[0]["title"]
        assert stubs[0]["company"] == "Universitätsspital Zürich"

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert MedJobsScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "Facharzt Chirurgie",
            "company": "Kantonsspital St. Gallen",
            "url": "https://med-jobs.com/de/stelle/123",
            "location": "St. Gallen",
            "description": "Facharzt für allgemeine Chirurgie gesucht.",
        }
        result = MedJobsScraper().normalize_job(raw)
        _assert_normalized(result, "medjobs")

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Arzt",
            "company": "",
            "url": "https://med-jobs.com/de/stelle/456",
        }
        result = MedJobsScraper().normalize_job(raw)
        _assert_normalized(result, "medjobs")

    def test_conservative_rate_limit(self):
        assert MedJobsScraper.RATE_LIMIT_SECONDS >= 3.0

    def test_needs_playwright(self):
        assert MedJobsScraper.NEEDS_PLAYWRIGHT is True


# ---------------------------------------------------------------------------
# stelle.admin.ch (jobs.admin.ch)
# ---------------------------------------------------------------------------


class TestStelleAdminScraper:
    def test_source_name(self):
        assert StelleAdminScraper().get_source_name() == "stelle_admin"

    def test_needs_playwright(self):
        assert StelleAdminScraper.NEEDS_PLAYWRIGHT is True

    def test_parse_listing_page(self):
        html = (FIXTURES / "stelle_admin_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)
        assert len(stubs) == 2
        assert "Informatiker" in stubs[0]["title"]
        assert stubs[0]["location"] == "Bern"
        assert stubs[0]["employment_type"] == "80-100%"

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert StelleAdminScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "IT Projektleiter/in",
            "company": "Bundesamt für Informatik",
            "url": "https://jobs.admin.ch/job/xyz?lang=de",
            "location": "Bern",
            "description": "Leitung von IT-Projekten der Bundesverwaltung.",
            "employment_type": "100%",
        }
        result = StelleAdminScraper().normalize_job(raw)
        _assert_normalized(result, "stelle_admin")
        assert result["employment_type"] == "100%"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Sachbearbeiter/in",
            "url": "https://jobs.admin.ch/job/abc",
        }
        result = StelleAdminScraper().normalize_job(raw)
        _assert_normalized(result, "stelle_admin")
        assert result["company"] == "Swiss Federal Administration"

    def test_fetch_details_disabled(self):
        assert StelleAdminScraper.FETCH_DETAILS is False

    def test_registro_sin_url_propia_se_salta(self):
        """VD.1: un card sin <a href> (o con enlace a portada/listado) NO puede
        salir con la URL base — las 7 ofertas colisionaban en ix_jobs_url."""
        html = """
        <div class="job-card">
          <h3 class="title"><a href="/job/abc123">Informatiker/in EFZ</a></h3>
        </div>
        <div class="job-card">
          <h3 class="title">Jurist/in ohne Link zur Stelle</h3>
        </div>
        <div class="job-card">
          <h3 class="title">Sachbearbeiter/in Paginierung</h3>
          <a href="/?lang=de&page=2">weiter</a>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)

        assert len(stubs) == 1
        assert stubs[0]["url"] == "https://jobs.admin.ch/job/abc123"
        urls = {s["url"].rstrip("/") for s in stubs}
        assert "https://jobs.admin.ch" not in urls

    def test_estrategia3_casa_con_offene_stellen(self):
        """VD.1: los enlaces reales del DOM renderizado tienen la forma
        /offene-stellen/<slug>/<uuid> y el fallback debe reconocerlos."""
        html = """
        <a href="/offene-stellen/informatiker-in-efz/1a2b3c4d">
          Informatiker/in EFZ 80-100%
        </a>
        <a href="/impressum">Impressum</a>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)

        assert len(stubs) == 1
        assert (
            stubs[0]["url"]
            == "https://jobs.admin.ch/offene-stellen/informatiker-in-efz/1a2b3c4d"
        )

    def test_host_ajeno_rechazado_y_relativo_sin_barra_resuelto(self):
        """F4: un href hacia un host ajeno presente en el DOM renderizado no
        puede persistirse como URL clicable (host confusion / phishing), y un
        relativo sin '/' inicial se resuelve con urljoin en vez de producir un
        host corrupto (`jobs.admin.choffene-stellen/...`)."""
        html = """
        <div class="job-card">
          <h3 class="title">
            <a href="https://evil.com/stelle/phishing-angebot">
              Sachbearbeiter/in Bundesverwaltung
            </a>
          </h3>
        </div>
        <div class="job-card">
          <h3 class="title">
            <a href="offene-stellen/informatiker-in/1a2b3c4d">
              Informatiker/in EFZ
            </a>
          </h3>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)

        assert len(stubs) == 1
        assert (
            stubs[0]["url"]
            == "https://jobs.admin.ch/offene-stellen/informatiker-in/1a2b3c4d"
        )

        # La estrategia 3 (fallback por patrón) tampoco puede aceptar un host
        # ajeno aunque su path imite la forma de una oferta real.
        html3 = """
        <a href="https://evil.com/offene-stellen/jurist-in/9f8e7d6c">
          Jurist/in Völkerrecht (portal falso)
        </a>
        """
        soup3 = BeautifulSoup(html3, "lxml")
        assert StelleAdminScraper().parse_listing_page(soup3) == []

    def test_backslash_y_variantes_de_url_maliciosa_rechazadas(self):
        """G1: diferencial de parsers urllib/WHATWG — urllib solo corta el
        netloc en `/ ? #`, el navegador tambien en `\\`: con
        `https://evil.com\\@jobs.admin.ch/...` urllib ve un netloc que termina
        en `.admin.ch` (aceptado) pero el navegador del usuario navega a
        `evil.com`. Las cuatro validaciones van juntas."""
        # 1. El href del escenario reproducido (backslash antes de la `@`).
        evil = "https://evil.com\\@jobs.admin.ch/offene-stellen/jurist-in/9f8e7d6c"
        assert _resolve_job_url(evil) is None

        # 2. Variante protocolo-relativa del mismo ataque.
        assert (
            _resolve_job_url("//evil.com\\@jobs.admin.ch/offene-stellen/x/1a2b") is None
        )

        # 3. Esquema no-http con autoridad: pasaba la comprobacion de host.
        assert _resolve_job_url("ftp://x.admin.ch/stelle/y") is None

        # 4. Userinfo hacia un host legitimo: spoofing visual del enlace.
        assert _resolve_job_url("https://cualquier-cosa@sub.admin.ch/x") is None

        # Control: un href legitimo sigue resolviendo igual que antes.
        assert (
            _resolve_job_url("/offene-stellen/jurist-in/9f8e7d6c")
            == "https://jobs.admin.ch/offene-stellen/jurist-in/9f8e7d6c"
        )

        # Extremo a extremo contra parse_listing_page: el href del ataque en
        # el DOM renderizado no emite stub por NINGUNA estrategia (la card lo
        # prueba en 1/2 y, al quedar stubs vacio, el fallback lo reintenta).
        html = f"""
        <div class="job-card">
          <h3 class="title">
            <a href="{evil}">Jurist/in V&#246;lkerrecht (Bundesverwaltung)</a>
          </h3>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        assert StelleAdminScraper().parse_listing_page(soup) == []

    def test_estrategia3_patron_sobre_url_resuelta(self):
        """G4: un relativo sin '/' inicial no contiene `/offene-stellen/` en
        crudo — el patron del fallback se comprueba sobre la URL RESUELTA,
        igual que hacen el dedup y las estrategias 1/2."""
        html = """
        <a href="offene-stellen/informatiker-in/1a2b3c4d">
          Informatiker/in EFZ 80-100%
        </a>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)

        assert len(stubs) == 1
        assert (
            stubs[0]["url"]
            == "https://jobs.admin.ch/offene-stellen/informatiker-in/1a2b3c4d"
        )

    def test_estrategias12_no_repiten_url_entre_records(self):
        """F5: dos cards sin enlace propio cuyo primer <a> es el MISMO enlace
        de paginación CON path emitirían la misma URL y reventarían de nuevo
        contra ix_jobs_url (el modo de fallo histórico de VD.1): dentro del
        run, una URL ya emitida por otro record se descarta."""
        html = """
        <div class="job-card">
          <h3 class="title">Wissenschaftliche/r Mitarbeiter/in</h3>
          <a href="/offene-stellen?page=2">weiter</a>
        </div>
        <div class="job-card">
          <h3 class="title">Fachspezialist/in Digitalisierung</h3>
          <a href="/offene-stellen?page=2">weiter</a>
        </div>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)

        assert len(stubs) == 1

    def test_estrategia3_dedup_por_url_absoluta(self):
        """VD.1: el mismo enlace en forma relativa y absoluta es UNA identidad
        (el dedup por href crudo los contaba como dos)."""
        html = """
        <a href="/offene-stellen/jurist-in/9f8e7d6c">Jurist/in Völkerrecht</a>
        <a href="https://jobs.admin.ch/offene-stellen/jurist-in/9f8e7d6c">
          Jurist/in Völkerrecht (mismo enlace absoluto)
        </a>
        """
        soup = BeautifulSoup(html, "lxml")
        stubs = StelleAdminScraper().parse_listing_page(soup)

        assert len(stubs) == 1


# ---------------------------------------------------------------------------
# TES.com
# ---------------------------------------------------------------------------


class TestTESScraper:
    def test_source_name(self):
        assert TESScraper().get_source_name() == "tes"

    def test_parse_listing_page(self):
        html = (FIXTURES / "tes_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = TESScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Director of Co-Curricular Learning"
        assert stubs[0]["company"] == "Collège Alpin Beau Soleil SA"
        assert stubs[0]["location"] == "Switzerland"
        assert "/jobs/vacancy/" in stubs[0]["url"]
        assert stubs[0]["salary_original"] == "CHF 90000 - CHF 120000 per year"
        assert stubs[0]["employment_type"] == "Full Time"
        assert stubs[0]["logo"].endswith("beau-soleil-logo.png")
        # Second job has no salary
        assert stubs[1]["salary_original"] is None
        # Third job has no logo
        assert stubs[2]["logo"] is None

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert TESScraper().parse_listing_page(soup) == []

    def test_normalize_job(self):
        raw = {
            "title": "Primary Teacher",
            "company": "Zurich International School",
            "url": "https://www.tes.com/jobs/vacancy/primary-teacher-123",
            "location": "Zurich, Switzerland",
            "description": "Teach Year 3 students in our British curriculum school.",
            "employment_type": "Full Time",
            "salary_original": "CHF 90000 per year",
        }
        result = TESScraper().normalize_job(raw)
        _assert_normalized(result, "tes")
        assert result["title"] == "Primary Teacher"
        assert result["company"] == "Zurich International School"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Teacher",
            "company": "",
            "url": "https://www.tes.com/jobs/vacancy/teacher-999",
        }
        result = TESScraper().normalize_job(raw)
        _assert_normalized(result, "tes")

    def test_fetch_details_disabled(self):
        assert TESScraper.FETCH_DETAILS is False

    def test_page_size_one(self):
        assert TESScraper.PAGE_SIZE == 1


# ---------------------------------------------------------------------------
# schuljobs.ch
# ---------------------------------------------------------------------------


class TestSchulJobsScraper:
    def test_source_name(self):
        assert SchulJobsScraper().get_source_name() == "schuljobs"

    def test_parse_listing_page(self):
        html = (FIXTURES / "schuljobs_listing.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        stubs = SchulJobsScraper().parse_listing_page(soup)
        assert len(stubs) == 3
        assert stubs[0]["title"] == "Primarlehrperson Zyklus 2"
        assert stubs[0]["company"] == "Tagesschule. Für das Kind"
        assert stubs[0]["canton"] == "ZH"
        assert stubs[0]["location"] == "Zürich"
        assert "J954885" in stubs[0]["detail_url"]
        # Third job has relative URL
        assert stubs[2]["detail_url"].startswith("https://")

    def test_parse_listing_page_empty(self):
        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        assert SchulJobsScraper().parse_listing_page(soup) == []

    def test_parse_job_detail(self):
        html = (FIXTURES / "schuljobs_detail.html").read_text()
        soup = BeautifulSoup(html, "lxml")
        detail = SchulJobsScraper().parse_job_detail(soup)
        assert detail["title"] == "Primarlehrperson Zyklus 2"
        assert detail["company"] == "Tagesschule. Für das Kind"
        assert detail["location"] == "Zürich"
        assert detail["canton"] == "ZH"
        assert detail["employment_type"] == "PART_TIME"
        assert "Primarlehrer" in detail["description"]
        assert detail["logo"].endswith("fuerdaskind.png")

    def test_normalize_job(self):
        raw = {
            "title": "Primarlehrperson Zyklus 2",
            "company": "Tagesschule. Für das Kind",
            "url": "https://www.schuljobs.ch/job/primarlehrperson/J954885",
            "location": "Zürich",
            "canton": "ZH",
            "description": "Wir suchen eine Primarlehrperson für Zyklus 2.",
            "employment_type": "PART_TIME",
        }
        result = SchulJobsScraper().normalize_job(raw)
        _assert_normalized(result, "schuljobs")
        assert result["title"] == "Primarlehrperson Zyklus 2"
        assert result["canton"] == "ZH"

    def test_normalize_job_minimal(self):
        raw = {
            "title": "Lehrperson",
            "url": "https://www.schuljobs.ch/job/lp/J000001",
        }
        result = SchulJobsScraper().normalize_job(raw)
        _assert_normalized(result, "schuljobs")

    def test_fetch_details_enabled(self):
        assert SchulJobsScraper.FETCH_DETAILS is True

    def test_parse_listing_page_ajax_fragment(self):
        """Verify parse_listing_page works on AJAX HTML fragments (no <html> wrapper)."""
        html_fragment = """
        <article class="jobs-job">
          <div>
            <h3>
              <a class="js-joboffer-detail"
                 href="https://www.schuljobs.ch/job/schulleiter-in/J970200">
                Schulleiter/in 80-100%
              </a>
            </h3>
            <p>BE · Bern · Bildungsdirektion Bern</p>
          </div>
        </article>
        <article class="jobs-job">
          <div>
            <h3>
              <a class="js-joboffer-detail"
                 href="https://www.schuljobs.ch/job/logopaedin/J970201">
                Logopädin 60%
              </a>
            </h3>
            <p>AG · Aarau · Schule Aarau</p>
          </div>
        </article>
        """
        soup = BeautifulSoup(html_fragment, "lxml")
        stubs = SchulJobsScraper().parse_listing_page(soup)
        assert len(stubs) == 2
        assert stubs[0]["title"] == "Schulleiter/in 80-100%"
        assert stubs[0]["company"] == "Bildungsdirektion Bern"
        assert stubs[0]["canton"] == "BE"
        assert stubs[1]["title"] == "Logopädin 60%"
        assert stubs[1]["canton"] == "AG"

    def test_searchhash_extraction(self):
        """Verify searchhash can be extracted from initial page HTML."""
        html = """
        <html><body>
        <section class="js-list-result" data-searchhash="abc123def" data-total="100">
        </section>
        <a class="btn btn-more-jobs js-btn-scroll" data-nextpage="2">
          Weitere Jobs anzeigen …
        </a>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        result_list = soup.select_one("[data-searchhash]")
        assert result_list is not None
        assert result_list.get("data-searchhash") == "abc123def"
        btn = soup.select_one("[data-nextpage]")
        assert btn is not None
        assert int(btn.get("data-nextpage")) == 2
