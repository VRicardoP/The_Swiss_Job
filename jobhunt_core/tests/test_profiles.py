"""Perfiles (A-07): normalización, texto embebible y hashes — unit sin BD."""

from jobhunt_core import profiles


def test_normalize_profile_coerces_defensively():
    """Lección A-05 #2: contenido basura degrada, jamás revienta."""
    c = profiles.normalize_profile(
        {
            "title": "  Backend Dev  ", "cv_text": 42,
            "skills": ["python", 7, " sql ", None], "languages": "de",
            "locations": ["Zurich"], "experience_years": True,  # bool NO es int
            "salary_min": 80000, "salary_max": "mucho", "remote_pref": ["any"],
        }
    )
    assert c == {
        "title": "Backend Dev", "cv_text": None, "skills": ["python", "sql"],
        "languages": [], "locations": ["Zurich"], "experience_years": None,
        "salary_min": 80000, "salary_max": None, "remote_pref": None,
    }
    assert profiles.normalize_profile("no-dict") is None
    # Sin texto embebible (ni title, ni cv_text, ni skills) → None.
    assert profiles.normalize_profile({"locations": ["Berna"]}) is None


def test_profile_text_mirrors_legacy_composition():
    """Legacy profile_tasks: title + cv_text + skills — mismo espacio que la
    sombra de Fase B; salario/idiomas/ubicaciones NO re-embeben."""
    c = profiles.normalize_profile(
        {"title": "Dev", "cv_text": "10 años", "skills": ["py", "sql"],
         "salary_min": 1, "languages": ["de"]}
    )
    assert profiles.build_profile_text(c) == "Dev 10 años py sql"

    c2 = profiles.normalize_profile(
        {"title": "Dev", "cv_text": "10 años", "skills": ["py", "sql"],
         "salary_min": 999999, "languages": ["fr", "it"]}
    )
    assert profiles.profile_content_hash(c) != profiles.profile_content_hash(c2)
    assert profiles.profile_text_hash(c) == profiles.profile_text_hash(c2)


def test_text_hash_derives_from_encoder_input():
    """Disciplina rev. A-06 2ª #5: mismo texto embebible ⇒ mismo hash."""
    a = profiles.normalize_profile({"title": "A B"})
    b = profiles.normalize_profile({"title": "A", "cv_text": "B"})
    assert profiles.build_profile_text(a) == profiles.build_profile_text(b)
    assert profiles.profile_text_hash(a) == profiles.profile_text_hash(b)
