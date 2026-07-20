"""Tests para NavArbeidsplassenProvider (puros, sin red).

El raw de entrada replica un hit real de Elasticsearch de la API pública NAV
(`_id` + `_source`), recortado a los campos que consume el provider.
"""

from providers.nav_arbeidsplassen import NavArbeidsplassenProvider

# Hit representativo capturado en vivo (recortado). uuid == _id.
_RAW_HIT: dict = {
    "_id": "8fea8d40-2a18-4a6a-9156-c246e911b440",
    "_source": {
        "title": "Statsautorisert regnskapsfører + oppdragsansvarlig",
        "businessName": "Reai As",
        "employer": {"name": "REAI AS"},
        "published": "2026-07-19T13:22:27+02:00",
        "uuid": "8fea8d40-2a18-4a6a-9156-c246e911b440",
        "generatedSearchMetadata": {
            "shortSummary": (
                "Autorisert regnskapsfører søkes for faglig ansvar og testing "
                "av nytt regnskapssystem. Hjemmekontor tilbys."
            )
        },
        "locationList": [
            {
                "country": "NORGE",
                "address": None,
                "city": None,
                "postalCode": None,
                "county": "OSLO",
                "municipal": "OSLO",
            }
        ],
        "properties": {
            "workLanguage": ["Norsk", "Engelsk"],
            "searchtagsai": ["GRFS", "Kvalitetssystem", "Mva"],
            "remote": "Hjemmekontor",
        },
    },
}


def _assert_normalized(result: dict, source: str) -> None:
    """Asserts comunes replicados de tests/test_providers.py:_assert_normalized."""
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


# Las 21 claves exactas que debe devolver normalize_job.
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


class TestNavArbeidsplassenProvider:
    def test_source_name(self):
        assert NavArbeidsplassenProvider().get_source_name() == "nav_arbeidsplassen"

    def test_normalize_job(self):
        result = NavArbeidsplassenProvider().normalize_job(_RAW_HIT)
        _assert_normalized(result, "nav_arbeidsplassen")

        # Las 21 claves están presentes, ni una de más ni de menos.
        assert set(result.keys()) == _EXPECTED_KEYS

        assert result["title"] == "Statsautorisert regnskapsfører + oppdragsansvarlig"
        assert result["company"] == "Reai As"
        # URL de detalle = prefijo + uuid (_id).
        assert result["url"] == (
            "https://arbeidsplassen.nav.no/stillinger/stilling/"
            "8fea8d40-2a18-4a6a-9156-c246e911b440"
        )
        # Todo lo cosechado por faceta remote es remoto.
        assert result["remote"] is True
        # Descripción viene de shortSummary (no de una 2ª llamada HTTP).
        assert "regnskapsfører" in result["description"]
        # Idioma derivado de workLanguage estructural (Norsk primero → "no").
        assert result["language"] == "no"
        # Ubicación deduplicada: "OSLO, OSLO, NORGE" → "OSLO, NORGE".
        assert result["location"] == "OSLO, NORGE"
        # Sin salario en la búsqueda → None (nunca strings).
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None
        assert result["salary_original"] is None
        # searchtagsai se propagan como tags.
        assert "GRFS" in result["tags"]

    def test_normalize_job_english_only(self):
        """workLanguage sin noruego → idioma "en"."""
        raw = {
            "_id": "abc",
            "_source": {
                "title": "Backend Engineer",
                "businessName": "ACME",
                "generatedSearchMetadata": {"shortSummary": "Build APIs"},
                "properties": {"workLanguage": ["Engelsk"]},
            },
        }
        result = NavArbeidsplassenProvider().normalize_job(raw)
        assert result["language"] == "en"

    def test_normalize_missing_fields(self):
        """Raw mínimo (solo lo que produce title + url): no peta y valida."""
        raw = {
            "_id": "uuid-minimo",
            "_source": {"title": "Utvikler"},
        }
        result = NavArbeidsplassenProvider().normalize_job(raw)
        assert set(result.keys()) == _EXPECTED_KEYS
        assert result["title"] == "Utvikler"
        assert result["url"].endswith("uuid-minimo")
        # Sin datos → defaults seguros, no None inesperados en required.
        assert result["company"] == ""
        assert result["description"] == ""
        assert result["location"] == ""
        assert result["canton"] is None
        assert result["language"] == "no"  # default
        assert isinstance(result["tags"], list)
        assert result["remote"] is True

    def test_company_falls_back_to_employer_name(self):
        """Sin businessName usa employer.name."""
        raw = {
            "_id": "x",
            "_source": {
                "title": "Rolle",
                "employer": {"name": "Fallback Employer AS"},
            },
        }
        result = NavArbeidsplassenProvider().normalize_job(raw)
        assert result["company"] == "Fallback Employer AS"

    def test_register_uuid_deduplicates(self):
        """_register_uuid marca True solo la primera vez que ve un uuid."""
        seen: set[str] = set()
        hit = {"_id": "dup"}
        assert NavArbeidsplassenProvider._register_uuid(hit, seen) is True
        assert NavArbeidsplassenProvider._register_uuid(hit, seen) is False
        # Hit sin _id nunca se registra.
        assert NavArbeidsplassenProvider._register_uuid({}, seen) is False
