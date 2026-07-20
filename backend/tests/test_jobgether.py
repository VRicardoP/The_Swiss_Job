"""Tests for JobgetherProvider.normalize_job (pure, sin red)."""

from providers.jobgether import JobgetherProvider

# Las 21 claves obligatorias del esquema unificado; todas deben existir siempre.
EXPECTED_KEYS = {
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
    """Aserciones comunes a todo dict normalizado (replica test_providers.py)."""
    assert result["source"] == source
    assert result["hash"]  # non-empty
    assert len(result["hash"]) == 32  # MD5 hex
    assert result["title"]
    assert result["url"]
    assert isinstance(result["tags"], list)
    assert len(result["tags"]) <= 15
    assert isinstance(result["remote"], bool)
    # Las 21 claves deben estar presentes, ni una más ni una menos.
    assert set(result.keys()) == EXPECTED_KEYS


# Oferta real (recorte de la API en vivo) con salario presente.
_RAW_WITH_SALARY = {
    "title": "Senior Software Engineer (Python) - Contractor",
    "companyData": {
        "name": "Very",
        "logo": "https://cdn-s3.jobgether.com/very/profile.webp",
    },
    "slug": "6a2ba788135f3346537f4f73-senior-software-engineer-python-contractor",
    "requiredLocations": "Colombia",
    "remoteOfferType": "Full Remote",
    "contractType": "Freelance",
    "skills": [
        {"_id": "python", "name": "Python (Programming Language)"},
        {"_id": "react-jsx", "name": "React Jsx"},
        {"_id": "typescript", "name": "TypeScript"},
    ],
    "salary": {
        "average": 96000,
        "currency": "USD",
        "max": 115200,
        "min": 76800,
        "range": [76800, 115200],
    },
    "createdAt": "2026-07-18T12:30:20.208Z",
}


class TestJobgetherProvider:
    def test_source_name(self):
        assert JobgetherProvider().get_source_name() == "jobgether"

    def test_normalize_job(self):
        result = JobgetherProvider().normalize_job(_RAW_WITH_SALARY)
        _assert_normalized(result, "jobgether")

        assert result["title"] == "Senior Software Engineer (Python) - Contractor"
        # company sale de companyData.name, no del id plano `company`.
        assert result["company"] == "Very"
        assert result["logo"] == "https://cdn-s3.jobgether.com/very/profile.webp"
        # url = base + slug (el slug ya incluye el id).
        assert result["url"] == (
            "https://jobgether.com/offer/"
            "6a2ba788135f3346537f4f73-senior-software-engineer-python-contractor"
        )
        assert result["location"] == "Colombia"
        # remote deriva de remoteOfferType (estructural), no del título.
        assert result["remote"] is True
        # employment_type = contractType; NO se confunde con remoteOfferType.
        assert result["employment_type"] == "Freelance"
        assert result["contract_type"] is None
        # tags: skills de la API + extraídos, dedup, tope 15.
        assert "Python (Programming Language)" in result["tags"]
        assert "TypeScript" in result["tags"]
        # No hay 2ª llamada por oferta: sin descripción en el listado.
        assert result["description"] == ""
        assert result["description_snippet"] is None

    def test_normalize_salary_present(self):
        result = JobgetherProvider().normalize_job(_RAW_WITH_SALARY)
        # Importes NO son CHF → salary_*_chf a None; original + currency poblados.
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None
        assert result["salary_currency"] == "USD"
        assert result["salary_original"] == "76800-115200 USD"
        # No hay periodo declarado en la fuente → no se inventa.
        assert result["salary_period"] is None

    def test_normalize_salary_absent(self):
        raw = {
            "title": "AI Platform Engineer",
            "companyData": {"name": "Smarthis"},
            "slug": "abc123-ai-platform-engineer",
            "remoteOfferType": "Full Remote",
            "contractType": "Full time",
            # salary con average 0 = ausencia de salario.
            "salary": {"average": 0, "currency": "", "min": 0, "max": 0},
        }
        result = JobgetherProvider().normalize_job(raw)
        _assert_normalized(result, "jobgether")
        assert result["salary_original"] is None
        assert result["salary_currency"] is None
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None

    def test_normalize_missing_fields(self):
        # Raw mínimo: solo title + slug. No debe petar y produce dict válido.
        raw = {"title": "Data Annotator", "slug": "xyz-data-annotator"}
        result = JobgetherProvider().normalize_job(raw)
        _assert_normalized(result, "jobgether")
        assert result["title"] == "Data Annotator"
        assert result["url"] == "https://jobgether.com/offer/xyz-data-annotator"
        assert result["company"] == ""
        assert result["logo"] is None
        # Sin remoteOfferType → remote es False (bool real, no None).
        assert result["remote"] is False
        assert result["employment_type"] is None
        assert result["salary_original"] is None

    def test_normalize_empty_slug_discards(self):
        # Sin slug la url queda vacía → _process_raw_jobs la descartaría.
        raw = {"title": "Ghost Job", "companyData": {"name": "X"}}
        result = JobgetherProvider().normalize_job(raw)
        assert result["url"] == ""
        assert set(result.keys()) == EXPECTED_KEYS
