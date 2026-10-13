"""Classifier and teacher-alert regressions with real watchlist tags."""

import pytest

from services.data_normalizer import DataNormalizer
from services.teacher_alert import is_primary_teacher_job

WATCHLIST_TAGS = ["education", "international school", "nae_zurich"]
# Salida real de `extract_job_skills`, que es de donde salen los tags de
# publicjobs / zebis / schuljobs / tes / irishjobs / gastrojob.
SKILL_TAGS = ["english", "french", "microsoft office"]


def _categoria(title: str, tags: list[str]) -> str:
    """Camino real de ingesta: el clasificador NO se llama nunca a pelo."""
    return DataNormalizer.classify_category({"title": title, "tags": tags})["category"]


class TestP22ClasificadorConTagsReales:
    @pytest.mark.parametrize(
        "title,tags",
        [
            ("ENSEIGNANTE PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("ENSEIGNANT-E PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("UN-E ENSEIGNANT-E PRIMAIRE", SKILL_TAGS),
            ("UNO INSTRUCTOR PER LA SCUOLA ELEMENTARE", SKILL_TAGS),
        ],
    )
    def test_el_articulo_no_secuestra_la_docencia_con_tags(self, title, tags):
        """Con un solo tag en minúsculas el guard de G2 dejaba de existir."""
        assert _categoria(title, tags) == "H", (
            "el guard all_caps se calculaba sobre título+tags: cualquier tag "
            "en minúsculas lo desactivaba y el token UN volvía a ganar"
        )

    @pytest.mark.parametrize(
        "title,tags",
        [
            ("UN VOLUNTEER PROGRAMME OFFICER", []),
            ("WHO MEDICAL OFFICER GENEVA", []),
            ("ILO SENIOR ECONOMIST", []),
        ],
    )
    def test_el_organismo_real_en_mayusculas_sigue_siendo_f(self, title, tags):
        """Dirección contraria del trade-off: con el guard de G2 activo estos
        títulos caían en 'otros'. El acrónimo decide cuando NADA más casa."""
        assert _categoria(title, tags) == "F"

    @pytest.mark.parametrize(
        "title",
        ["UN Volunteer Programme Officer", "ILO Senior Economist"],
    )
    def test_el_organismo_en_titulo_mixto_sigue_siendo_f(self, title):
        """No-regresión de G1/P2-9."""
        assert _categoria(title, WATCHLIST_TAGS) == "F"

    def test_el_acronimo_en_minusculas_sigue_sin_casar(self):
        """No-regresión: «un» minúscula nunca fue el organismo."""
        assert _categoria("Un poste de developpeur backend", SKILL_TAGS) != "F"


class TestP22AlertaProfesorDePrimaria:
    """Mordida end-to-end: normalización real → categoría → teacher_alert."""

    @staticmethod
    def _job(title: str, tags: list[str]) -> dict:
        return {
            "hash": "h" * 32,
            "source": "swiss_schools_nae",
            "title": title,
            "company": "NAE Zurich",
            "url": "https://example.test/job/1",
            "location": "Zurich",
            "canton": "ZH",
            "description": "Poste au sein d'une ecole primaire internationale.",
            "description_snippet": "Poste au sein d'une ecole primaire...",
            "remote": False,
            "tags": tags,
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

    @pytest.mark.parametrize(
        "title,tags",
        [
            ("ENSEIGNANTE PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("UNO INSTRUCTOR PER LA SCUOLA ELEMENTARE", SKILL_TAGS),
            # G4/P1-4 — los DOS títulos con los que `5cc91d3` justificó su fix
            # del clasificador NO estaban en esta clase end-to-end: solo se
            # parametrizaban las grafías sin guion. El clasificador los daba
            # por H y la alerta seguía muda.
            ("ENSEIGNANT-E PRIMAIRE POUR UN REMPLACEMENT", WATCHLIST_TAGS),
            ("UN-E ENSEIGNANT-E PRIMAIRE", WATCHLIST_TAGS),
        ],
    )
    def test_la_alerta_vuelve_a_dispararse(self, title, tags):
        job = DataNormalizer.normalize(self._job(title, tags))

        assert job["category"] == "H"
        assert (
            is_primary_teacher_job(job["category"], job["title"], job["tags"]) is True
        ), (
            "categoría F ⇒ is_primary_teacher_job corta en la primera línea y "
            "el email de alerta (caso de uso central) no se envía"
        )
