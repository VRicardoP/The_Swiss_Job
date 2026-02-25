"""Tests for DataNormalizer service: salary, language, seniority, contract type."""

from services.data_normalizer import DataNormalizer


def _base_job(**overrides):
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


# ---------------------------------------------------------------------------
# Salary normalization
# ---------------------------------------------------------------------------


class TestSalaryNormalization:
    def test_eur_annual_to_chf(self):
        """100000 EUR yearly -> 100000 * 0.96 = 96000 CHF."""
        job = _base_job(
            salary_min_chf=100_000,
            salary_max_chf=120_000,
            salary_currency="EUR",
            salary_period="yearly",
        )
        # salary_min_chf and salary_max_chf are already set, so the
        # normalizer treats them as "already normalized" and skips.
        # We need to clear them and set raw values to trigger conversion.
        job["salary_min_chf"] = None
        job["salary_max_chf"] = None
        job["salary_original"] = "100000-120000 EUR"
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == 96_000
        assert result["salary_max_chf"] == 115_200

    def test_usd_monthly_to_chf_annual(self):
        """8000 USD/month -> 8000 * 0.88 * 12 = 84480 CHF."""
        job = _base_job(
            salary_original="8000 USD",
            salary_currency="USD",
            salary_period="monthly",
        )
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == 84_480
        assert result["salary_max_chf"] == 84_480

    def test_gbp_hourly_to_chf_annual(self):
        """50 GBP/hour -> 50 * 1.12 * 2080 = 116480 CHF."""
        job = _base_job(
            salary_original="50 GBP",
            salary_currency="GBP",
            salary_period="hourly",
        )
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == 116_480
        assert result["salary_max_chf"] == 116_480

    def test_chf_already_set_no_change(self):
        """When both salary_min_chf and salary_max_chf are set, skip conversion."""
        job = _base_job(salary_min_chf=90_000, salary_max_chf=110_000)
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == 90_000
        assert result["salary_max_chf"] == 110_000

    def test_no_salary_data_stays_none(self):
        """No salary data at all -> fields stay None."""
        job = _base_job()
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None

    def test_parse_salary_range_string(self):
        """Parse 'salary_original' string with range: '80000-100000 EUR'."""
        job = _base_job(salary_original="80000-100000 EUR")
        result = DataNormalizer.normalize_salary(job)
        # 80000 * 0.96 = 76800, 100000 * 0.96 = 96000
        assert result["salary_min_chf"] == 76_800
        assert result["salary_max_chf"] == 96_000

    def test_parse_salary_single_value_string(self):
        """Parse 'salary_original' with single value '90000 CHF'."""
        job = _base_job(salary_original="90000 CHF")
        result = DataNormalizer.normalize_salary(job)
        assert result["salary_min_chf"] == 90_000
        assert result["salary_max_chf"] == 90_000

    def test_only_salary_original_set_parses_and_converts(self):
        """Only salary_original set (no min/max/currency) -> should parse and convert."""
        job = _base_job(salary_original="60000-80000 EUR")
        result = DataNormalizer.normalize_salary(job)
        # 60000 * 0.96 = 57600, 80000 * 0.96 = 76800
        assert result["salary_min_chf"] == 57_600
        assert result["salary_max_chf"] == 76_800

    def test_salary_original_with_explicit_currency_override(self):
        """Explicit salary_currency takes precedence over parsed currency."""
        job = _base_job(
            salary_original="50000 EUR",
            salary_currency="GBP",  # Explicit override
        )
        result = DataNormalizer.normalize_salary(job)
        # Uses GBP rate (1.12) since salary_currency is already set
        assert result["salary_min_chf"] == 56_000
        assert result["salary_max_chf"] == 56_000

    def test_salary_with_period_multiplier(self):
        """Salary with explicit period uses the correct multiplier."""
        job = _base_job(
            salary_original="5000 CHF",
            salary_period="monthly",
        )
        result = DataNormalizer.normalize_salary(job)
        # 5000 * 1.0 * 12 = 60000
        assert result["salary_min_chf"] == 60_000
        assert result["salary_max_chf"] == 60_000


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


class TestLanguageDetection:
    def test_german_text(self):
        """German description -> 'de'."""
        job = _base_job(
            title="Softwareentwickler",
            description=(
                "Wir suchen einen erfahrenen Softwareentwickler fuer unser Team "
                "in Zuerich. Sie werden an spannenden Projekten arbeiten und "
                "moderne Technologien einsetzen. Gute Deutschkenntnisse erforderlich."
            ),
        )
        result = DataNormalizer.detect_language(job)
        assert result["language"] == "de"

    def test_french_text(self):
        """French description -> 'fr'."""
        job = _base_job(
            title="Developpeur logiciel",
            description=(
                "Nous recherchons un developpeur logiciel experimente pour rejoindre "
                "notre equipe a Geneve. Vous travaillerez sur des projets innovants "
                "et utiliserez des technologies modernes. Bonne maitrise du francais requise."
            ),
        )
        result = DataNormalizer.detect_language(job)
        assert result["language"] == "fr"

    def test_english_text(self):
        """English description -> 'en'."""
        job = _base_job(
            title="Software Engineer",
            description=(
                "We are looking for an experienced software engineer to join our "
                "team in Zurich. You will work on exciting projects using modern "
                "technologies. Strong English communication skills required."
            ),
        )
        result = DataNormalizer.detect_language(job)
        assert result["language"] == "en"

    def test_short_text_skipped(self):
        """Text shorter than 50 chars -> language stays None."""
        job = _base_job(title="Dev", description="Short text.")
        result = DataNormalizer.detect_language(job)
        assert result["language"] is None


# ---------------------------------------------------------------------------
# Seniority inference
# ---------------------------------------------------------------------------


class TestSeniorityInference:
    def test_senior_title(self):
        """'Senior Python Developer' -> 'senior'."""
        job = _base_job(title="Senior Python Developer")
        result = DataNormalizer.infer_seniority(job)
        assert result["seniority"] == "senior"

    def test_junior_title(self):
        """'Junior QA Engineer' -> 'junior'."""
        job = _base_job(title="Junior QA Engineer")
        result = DataNormalizer.infer_seniority(job)
        assert result["seniority"] == "junior"

    def test_intern_german(self):
        """'Praktikant Informatik' -> 'intern'."""
        job = _base_job(title="Praktikant Informatik")
        result = DataNormalizer.infer_seniority(job)
        assert result["seniority"] == "intern"

    def test_head_title(self):
        """'Head of Engineering' -> 'head'."""
        job = _base_job(title="Head of Engineering")
        result = DataNormalizer.infer_seniority(job)
        assert result["seniority"] == "head"

    def test_lead_title(self):
        """'Team Lead Backend' -> 'lead'."""
        job = _base_job(title="Team Lead Backend")
        result = DataNormalizer.infer_seniority(job)
        assert result["seniority"] == "lead"

    def test_plain_title_no_seniority(self):
        """'Software Developer' -> None (no seniority keyword)."""
        job = _base_job(title="Software Developer")
        result = DataNormalizer.infer_seniority(job)
        assert result["seniority"] is None


# ---------------------------------------------------------------------------
# Contract type inference
# ---------------------------------------------------------------------------


class TestContractTypeInference:
    def test_full_time_from_employment_type(self):
        """employment_type='Full-Time' -> 'full_time'."""
        job = _base_job(employment_type="Full-Time")
        result = DataNormalizer.infer_contract_type(job)
        assert result["contract_type"] == "full_time"

    def test_part_time_from_title(self):
        """Title containing 'Teilzeit' -> 'part_time'."""
        job = _base_job(title="Teilzeit Sachbearbeiter")
        result = DataNormalizer.infer_contract_type(job)
        assert result["contract_type"] == "part_time"

    def test_apprenticeship_from_title(self):
        """'Lehrstelle Informatik' -> 'apprenticeship'."""
        job = _base_job(title="Lehrstelle Informatik")
        result = DataNormalizer.infer_contract_type(job)
        assert result["contract_type"] == "apprenticeship"

    def test_temporary_from_description(self):
        """Description snippet containing 'temporary' -> 'temporary'."""
        job = _base_job(description_snippet="This is a temporary position for 6 months")
        result = DataNormalizer.infer_contract_type(job)
        assert result["contract_type"] == "temporary"

    def test_no_indicators_returns_none(self):
        """No contract indicators -> None."""
        job = _base_job(
            title="Developer", employment_type=None, description_snippet=None
        )
        result = DataNormalizer.infer_contract_type(job)
        assert result["contract_type"] is None


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestNormalizeIntegration:
    def test_normalize_runs_all_steps(self):
        """normalize() enriches salary, language, seniority, and contract type."""
        job = _base_job(
            title="Senior Software Engineer",
            description=(
                "We are looking for an experienced software engineer to join our "
                "team in Zurich. You will work on exciting projects using modern "
                "technologies. Strong English communication skills required."
            ),
            salary_original="100000-120000 CHF",
            employment_type="Full-Time",
        )
        result = DataNormalizer.normalize(job)

        # Salary parsed from salary_original
        assert result["salary_min_chf"] == 100_000
        assert result["salary_max_chf"] == 120_000

        # Language detected
        assert result["language"] == "en"

        # Seniority inferred from title
        assert result["seniority"] == "senior"

        # Contract type inferred from employment_type
        assert result["contract_type"] == "full_time"

    def test_already_set_fields_not_overwritten(self):
        """Fields that are already populated should not be overwritten."""
        job = _base_job(
            title="Junior Data Scientist",
            description=(
                "Nous recherchons un data scientist pour notre equipe. "
                "Vous travaillerez sur des projets innovants dans notre bureau "
                "a Lausanne. Bonne maitrise du francais indispensable."
            ),
            salary_min_chf=85_000,
            salary_max_chf=95_000,
            language="it",  # Pre-set: should not be overwritten
            seniority="mid",  # Pre-set: should not be overwritten
            contract_type="contract",  # Pre-set: should not be overwritten
        )
        result = DataNormalizer.normalize(job)

        # Salary already set -> unchanged
        assert result["salary_min_chf"] == 85_000
        assert result["salary_max_chf"] == 95_000

        # Language was already "it" -> not overwritten
        assert result["language"] == "it"

        # Seniority was "mid" -> not overwritten (even though title says "Junior")
        assert result["seniority"] == "mid"

        # Contract type was "contract" -> not overwritten
        assert result["contract_type"] == "contract"
