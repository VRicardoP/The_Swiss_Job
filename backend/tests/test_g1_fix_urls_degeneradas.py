"""Regresiones de la auditoría G1 — URLs degeneradas (clase VD.1).

- P1-2: hautlac/iscs daban a TODAS las vacantes la URL del listado; con
  jobs.url UNIQUE y upsert por hash, solo sobrevivía una vacante por colegio
  (y una sustitución quedaba invisible hasta la purga a 60 días).
- P3-9: myscience/medjobs sin href degeneraban la URL en la portada.
"""

from bs4 import BeautifulSoup

from scrapers.myscience import MyScienceScraper
from scrapers.swiss_schools_hautlac import HAUTLAC_URL, SwissSchoolsHautLacScraper
from scrapers.swiss_schools_iscs import ISCS_URL, SwissSchoolsISCSScraper

_HAUTLAC_HTML = """
<html><body>
  <div class="hs_cos_wrapper_type_rich_text">
    <h3><span style="background-color: #0b7992">Primary Class Teacher</span></h3>
    <p>Mission text</p>
  </div>
  <div class="hs_cos_wrapper_type_rich_text">
    <h3><span style="background-color: #0b7992">Science Teacher (Secondary)</span></h3>
    <p>Mission text</p>
  </div>
</body></html>
"""

_ISCS_HTML = """
<html><body>
  <ul>
    <li><strong>PRIMARY TEACHER</strong> full time</li>
    <li><strong>MATHEMATICS COORDINATOR</strong> part time</li>
  </ul>
</body></html>
"""


class TestP12UrlPorVacante:
    def test_hautlac_urls_unicas_y_estables(self):
        """G1/P1-2: dos vacantes simultáneas deben tener DOS urls distintas."""
        scraper = SwissSchoolsHautLacScraper()
        soup = BeautifulSoup(_HAUTLAC_HTML, "html.parser")
        stubs = scraper.parse_listing_page(soup)
        assert len(stubs) == 2
        urls = {stub["url"] for stub in stubs}
        assert len(urls) == 2, "cada vacante necesita su propia URL (jobs.url UNIQUE)"
        # La URL del listado a secas ya no identifica a ninguna vacante.
        assert HAUTLAC_URL not in urls
        # Estable entre runs: mismo HTML → mismas URLs.
        again = scraper.parse_listing_page(BeautifulSoup(_HAUTLAC_HTML, "html.parser"))
        assert {s["url"] for s in again} == urls

    def test_iscs_urls_unicas_y_estables(self):
        scraper = SwissSchoolsISCSScraper()
        soup = BeautifulSoup(_ISCS_HTML, "html.parser")
        stubs = scraper.parse_listing_page(soup)
        assert len(stubs) == 2
        urls = {stub["url"] for stub in stubs}
        assert len(urls) == 2
        assert ISCS_URL not in urls
        again = scraper.parse_listing_page(BeautifulSoup(_ISCS_HTML, "html.parser"))
        assert {s["url"] for s in again} == urls


class TestP39HrefAusente:
    def test_myscience_sin_href_descarta_la_tarjeta(self):
        """G1/P3-9: una tarjeta sin enlace no debe salir con url = portada."""
        html = """
        <html><body><div id="results_table">
          <div itemscope>
            <div class="results_title">Postdoc in Physics</div>
            <div class="results_organization">ETH</div>
          </div>
          <div itemscope>
            <a href="/jobs/123"><span class="results_title">Lab Manager</span></a>
            <div class="results_organization">EPFL</div>
          </div>
        </div></body></html>
        """
        stubs = MyScienceScraper().parse_listing_page(
            BeautifulSoup(html, "html.parser")
        )
        assert len(stubs) == 1
        assert stubs[0]["title"] == "Lab Manager"
        assert all(s.get("url") != "https://www.myscience.ch" for s in stubs)
