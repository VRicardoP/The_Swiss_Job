"""Regresiones de la auditoría G1 — providers menores.

- P3-1: `.get(k, "").strip()` reventaba con `null` explícito en el JSON
  (None.strip() → AttributeError → oferta perdida en _process_raw_jobs).
- P3-3: `maxPages`/`pages` con default silencioso y TypeError si string.
- P3-4: sets de categorías MUERTOS (workingnomads/dailyremote) eliminados.
- P3-5: ictjobs prellenaba salary_*_chf (int("80'000") ValueError incluido);
  translatorscafe arrastraba la empresa dentro del título.
"""

import xml.etree.ElementTree as ET

import providers.dailyremote as dailyremote_mod
import providers.workingnomads as workingnomads_mod
from providers.careerjet import CareerjetProvider
from providers.ictjobs import ICTJobsProvider
from providers.jobgether import JobgetherProvider
from providers.jsearch import JSearchProvider
from providers.translatorscafe import TranslatorsCafeProvider


class TestP31NullExplicito:
    def test_jsearch_normaliza_con_nulls(self):
        """G1/P3-1: employer_name/job_title null no debe tirar la oferta."""
        raw = {
            "job_title": "Content Editor",
            "employer_name": None,
            "job_apply_link": "https://e.ch/1",
            "job_description": None,
            "job_city": None,
            "job_state": None,
            "job_country": "CH",
        }
        result = JSearchProvider().normalize_job(raw)
        assert result["title"] == "Content Editor"
        assert result["company"] == ""

    def test_jsearch_titulo_null(self):
        raw = {"job_title": None, "employer_name": "Acme", "job_apply_link": None}
        result = JSearchProvider().normalize_job(raw)
        assert result["title"] == ""


class TestP33PaginacionSegura:
    def test_safe_int_string(self):
        assert JobgetherProvider._safe_int("5") == 5

    def test_safe_int_basura_es_falsy(self):
        # 0 falsy → el llamante sigue paginando hasta su tope (sin default 1
        # silencioso ni TypeError con `page >= "5"`).
        assert JobgetherProvider._safe_int(None) == 0
        assert JobgetherProvider._safe_int("cinco") == 0
        assert CareerjetProvider._safe_int({}) == 0


class TestP34SetsMuertos:
    def test_workingnomads_sin_set_muerto(self):
        assert not hasattr(workingnomads_mod, "_RELEVANT_CATEGORIES"), (
            "set muerto reintroducido sin cablearlo a ningún filtro"
        )

    def test_dailyremote_sin_set_muerto(self):
        assert not hasattr(dailyremote_mod, "_RELEVANT_CATEGORIES")


class TestP35Salarios:
    def test_ictjobs_no_prellena_chf_y_tolera_apostrofe(self):
        """G1/P3-5: la regla general — _chf en None, salary_original al
        normalizador (que ya parsea "80'000")."""
        raw = {
            "id": 1,
            "title": {"rendered": "System Engineer"},
            "link": "https://ictjobs.ch/1",
            "content": {"rendered": "desc"},
            "acf": {
                "location": "Zürich",
                "salary_min": "80'000",
                "salary_max": "100'000",
            },
        }
        result = ICTJobsProvider().normalize_job(raw)
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None
        assert result["salary_currency"] == "CHF"
        assert "80'000" in result["salary_original"]

        # Y el normalizador lo convierte bien (CHF, rate 1.0).
        from services.data_normalizer import DataNormalizer

        normalized = DataNormalizer.normalize_salary(dict(result))
        assert normalized["salary_min_chf"] == 80_000
        assert normalized["salary_max_chf"] == 100_000


class TestP35TranslatorscafeTitulo:
    def _item(self, title):
        item = ET.Element("item")
        for tag, text in (
            ("title", title),
            ("link", "https://tc.example/job/1"),
            ("guid", "https://tc.example/job/1"),
            ("description", "Translation job description english"),
        ):
            el = ET.SubElement(item, tag)
            el.text = text
        return item

    def test_titulo_no_arrastra_la_empresa(self):
        result = TranslatorsCafeProvider().normalize_job(
            self._item("EN>DE Translator — Acme Translations")
        )
        assert result["title"] == "EN>DE Translator"
        assert result["company"] == "Acme Translations"

    def test_sin_separador_conserva_titulo(self):
        result = TranslatorsCafeProvider().normalize_job(
            self._item("Proofreader English")
        )
        assert result["title"] == "Proofreader English"
        assert result["company"] == ""
