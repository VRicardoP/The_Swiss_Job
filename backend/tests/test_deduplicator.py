"""Tests for the Deduplicator service — fuzzy hash computation and DB lookup."""

from models.job import Job
from services.deduplicator import Deduplicator


# --- Fuzzy hash computation ---


class TestComputeFuzzyHash:
    def test_same_title_company_same_hash(self):
        """Identical title + company always produce the same fuzzy_hash."""
        h1 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        assert h1 == h2

    def test_case_insensitive(self):
        """Fuzzy hash ignores casing differences."""
        h1 = Deduplicator.compute_fuzzy_hash("PYTHON DEVELOPER", "ACME")
        h2 = Deduplicator.compute_fuzzy_hash("python developer", "acme")
        assert h1 == h2

    def test_seniority_stripped(self):
        """Seniority prefixes are removed before hashing."""
        h1 = Deduplicator.compute_fuzzy_hash("Senior Python Developer", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        assert h1 == h2

    def test_gender_markers_stripped(self):
        """Gender markers like (m/f/d) are removed before hashing."""
        h1 = Deduplicator.compute_fuzzy_hash("Developer (m/f/d)", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Developer", "Acme")
        assert h1 == h2

    def test_company_suffix_ag(self):
        """Legal suffix 'AG' is stripped from company name."""
        h1 = Deduplicator.compute_fuzzy_hash("Developer", "Acme AG")
        h2 = Deduplicator.compute_fuzzy_hash("Developer", "Acme")
        assert h1 == h2

    def test_company_suffix_ltd(self):
        """Legal suffix 'Ltd' is stripped from company name."""
        h1 = Deduplicator.compute_fuzzy_hash("Developer", "Swiss Corp Ltd")
        h2 = Deduplicator.compute_fuzzy_hash("Developer", "Swiss Corp")
        assert h1 == h2

    def test_different_jobs_different_hashes(self):
        """Completely different jobs produce different hashes."""
        h1 = Deduplicator.compute_fuzzy_hash("Python Developer", "Acme")
        h2 = Deduplicator.compute_fuzzy_hash("Java Developer", "Google")
        assert h1 != h2


class TestNormalizeTitleTokens:
    """PF.5: la seniority se filtra POR TOKEN, nunca por substring.

    El substring-replace corrompía palabras legítimas que contienen una palabra de
    seniority: intern->international, lead->leader, head->headset, sr->disruptive.
    """

    def test_substring_intern_not_stripped(self):
        assert (
            Deduplicator._normalize_title("International Marketing")
            == "international marketing"
        )

    def test_substring_lead_not_stripped(self):
        assert Deduplicator._normalize_title("Team Leader") == "team leader"

    def test_substring_head_not_stripped(self):
        assert Deduplicator._normalize_title("Headset Engineer") == "headset engineer"

    def test_substring_sr_not_stripped(self):
        assert (
            Deduplicator._normalize_title("Disruptive Innovation")
            == "disruptive innovation"
        )

    def test_standalone_seniority_token_stripped(self):
        """Como token independiente, la seniority SI se elimina."""
        assert (
            Deduplicator._normalize_title("Senior Data Scientist") == "data scientist"
        )
        assert Deduplicator._normalize_title("Junior QA") == "qa"
        assert Deduplicator._normalize_title("Lead Data Scientist") == "data scientist"

    def test_gender_markers_still_removed(self):
        assert Deduplicator._normalize_title("Data Engineer (m/f/d)") == "data engineer"
        assert Deduplicator._normalize_title("Nurse (all genders)") == "nurse"

    def test_substring_bug_no_longer_collides(self):
        """Regresion: el bug podia acercar titulos distintos; deben diferir."""
        h_intl = Deduplicator.compute_fuzzy_hash("International Analyst", "Acme")
        h_analyst = Deduplicator.compute_fuzzy_hash("Analyst", "Acme")
        assert h_intl != h_analyst

    def test_sr_jr_with_dot_stripped(self):
        # El punto de 'sr.'/'jr.' lo quita _PUNCT_RE antes del filtrado por token.
        assert Deduplicator._normalize_title("Sr. Data Scientist") == "data scientist"
        assert Deduplicator._normalize_title("Jr. Developer") == "developer"

    def test_more_standalone_seniority_stripped(self):
        assert Deduplicator._normalize_title("Head of Sales") == "of sales"
        assert Deduplicator._normalize_title("Intern Researcher") == "researcher"
        assert Deduplicator._normalize_title("Trainee Analyst") == "analyst"

    def test_two_gender_markers_removed(self):
        # Formatos de 2 géneros (frecuentes en DACH/CH) también se quitan (regex).
        assert Deduplicator._normalize_title("Data Engineer (m/w)") == "data engineer"
        assert Deduplicator._normalize_title("Ingénieur (h/f)") == "ingénieur"
        # Dedup cross-source: (m/w), (m/w/d) y sin marcador colapsan al mismo hash.
        base = Deduplicator.compute_fuzzy_hash("Developer", "Acme")
        assert Deduplicator.compute_fuzzy_hash("Developer (m/w)", "Acme") == base
        assert Deduplicator.compute_fuzzy_hash("Developer (m/w/d)", "Acme") == base

    def test_diversity_regex_no_false_positive(self):
        # Rutas/tech con barra NO deben tratarse como marcador de género.
        assert (
            Deduplicator._normalize_title("Frontend/Backend Developer")
            == "frontend backend developer"
        )
        assert (
            Deduplicator._normalize_title("Java/Kotlin Engineer")
            == "java kotlin engineer"
        )

    def test_diversity_regex_ampersand_not_a_marker(self):
        # "R&D/M&A" no debe colapsar con "R&A" (el '&' no debe activar el marcador).
        h_rdma = Deduplicator.compute_fuzzy_hash("R&D/M&A Analyst", "Acme")
        h_ra = Deduplicator.compute_fuzzy_hash("R&A Analyst", "Acme")
        assert h_rdma != h_ra
        assert Deduplicator._normalize_title("C/C++ Developer") == "c c developer"
        assert Deduplicator._normalize_title("F/T Position") == "f t position"


# --- DB lookup (requires db_session fixture) ---


class TestFindFuzzyDuplicate:
    async def test_finds_match_from_different_source(self, db_session):
        """find_fuzzy_duplicate returns the canonical hash when a match exists
        from a different source."""
        job = Job(
            hash="abc123",
            source="jobicy",
            title="Python Developer",
            company="Acme",
            url="http://example.com/job/1",
            fuzzy_hash="deadbeef",
            is_active=True,
        )
        db_session.add(job)
        await db_session.commit()

        result = await Deduplicator.find_fuzzy_duplicate(
            db_session, "deadbeef", "jooble"
        )
        assert result == "abc123"

    async def test_returns_none_for_same_source(self, db_session):
        """find_fuzzy_duplicate ignores matches from the same source."""
        job = Job(
            hash="abc123",
            source="jobicy",
            title="Python Developer",
            company="Acme",
            url="http://example.com/job/1",
            fuzzy_hash="deadbeef",
            is_active=True,
        )
        db_session.add(job)
        await db_session.commit()

        result = await Deduplicator.find_fuzzy_duplicate(
            db_session, "deadbeef", "jobicy"
        )
        assert result is None

    async def test_returns_none_when_no_match(self, db_session):
        """find_fuzzy_duplicate returns None when no matching fuzzy_hash exists."""
        result = await Deduplicator.find_fuzzy_duplicate(
            db_session, "nonexistent", "jooble"
        )
        assert result is None
