"""Tests para TheHubProvider (thehub.io). Puros, sin red."""

from providers.thehub import TheHubProvider

# 21 claves obligatorias del schema unificado normalize_job.
_EXPECTED_KEYS = {
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


def _assert_normalized(result: dict, source: str) -> None:
    """Asserts comunes (replica de tests/test_providers.py:23)."""
    assert result["source"] == source
    assert result["hash"]  # non-empty string
    assert len(result["hash"]) == 32  # MD5 hex
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


class TestTheHubProvider:
    def test_source_name(self):
        assert TheHubProvider().get_source_name() == "thehub"

    def test_normalize_job(self):
        # Raw representativo tomado de la forma real de la API (recon en vivo).
        raw = {
            "id": "6a312c26b148ffae23eaa152",
            "title": "Senior Fullstack Engineer",
            "company": {
                "name": "Boardway",
                "logoImage": {"path": "/files/s3/20260616104652-abc.png"},
            },
            "description": "<p>We are looking for an experienced <b>engineer</b>.</p>",
            "salary": "competitive",
            "salaryRange": {},
            "location": {
                "locality": "Copenhagen",
                "country": "Denmark",
                "address": "Copenhagen, Denmark",
            },
            "countryCode": "DK",
            "isRemote": True,
            "createdAt": "2026-06-16T10:57:42.026Z",
            "absoluteJobUrl": "https://thehub.io/jobs/6a312c26b148ffae23eaa152",
        }
        result = TheHubProvider().normalize_job(raw)

        _assert_normalized(result, "thehub")
        # Las 21 claves están presentes exactamente.
        assert set(result.keys()) == _EXPECTED_KEYS

        assert result["title"] == "Senior Fullstack Engineer"
        assert result["company"] == "Boardway"
        assert result["url"] == "https://thehub.io/jobs/6a312c26b148ffae23eaa152"
        assert result["remote"] is True
        assert result["location"] == "Copenhagen, Denmark"
        # description limpia de HTML.
        assert "<" not in result["description"]
        assert "engineer" in result["description"]
        # Logo servido desde el CDN imgix.
        assert (
            result["logo"]
            == "https://thehub-io.imgix.net/files/s3/20260616104652-abc.png"
        )
        # Salario textual ("competitive") NO se mapea a numérico.
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None
        assert result["salary_original"] is None

    def test_normalize_missing_fields(self):
        # Raw mínimo: solo title + url. No debe petar; defaults tolerantes.
        raw = {
            "title": "Backend Developer",
            "absoluteJobUrl": "https://thehub.io/jobs/xyz",
        }
        result = TheHubProvider().normalize_job(raw)

        assert set(result.keys()) == _EXPECTED_KEYS
        assert result["title"] == "Backend Developer"
        assert result["url"] == "https://thehub.io/jobs/xyz"
        assert result["company"] == ""
        # location ausente → dict vacío tolerado → cadena vacía, canton None.
        assert result["location"] == ""
        assert result["canton"] is None
        assert result["logo"] is None
        assert result["remote"] is False

    def test_normalize_empty_location_dict(self):
        # location {} es habitual en The Hub: no debe romper.
        raw = {
            "title": "Data Engineer",
            "absoluteJobUrl": "https://thehub.io/jobs/abc",
            "company": {"name": "ACME"},
            "location": {},
            "isRemote": True,
        }
        result = TheHubProvider().normalize_job(raw)
        _assert_normalized(result, "thehub")
        assert result["location"] == ""
        assert result["canton"] is None
        assert result["logo"] is None
