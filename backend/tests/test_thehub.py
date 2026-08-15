"""Tests para TheHubProvider (thehub.io). Puros, sin red.

Desde VD.9 la fuente usa la API v2 (api.thehub.io): listado adelgazado +
detalle por oferta. Las fixtures son recortes del JSON REAL capturado en la
sonda del 2026-08-14/15 (tests/fixtures/thehub_v2_jobs_p1.json y
thehub_job_single.json).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import providers.thehub as thehub_module
from providers.thehub import TheHubProvider

FIXTURES = Path(__file__).parent / "fixtures"

# 22 claves obligatorias del schema unificado normalize_job.
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
    "published_at",
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


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestTheHubProvider:
    def test_source_name(self):
        assert TheHubProvider().get_source_name() == "thehub"

    def test_normalize_job_from_real_detail(self):
        # Detalle REAL de la API v2 (fixture de la sonda 2026-08-15).
        raw = _load_fixture("thehub_job_single.json")
        result = TheHubProvider().normalize_job(raw)

        _assert_normalized(result, "thehub")
        # Las 22 claves están presentes exactamente.
        assert set(result.keys()) == _EXPECTED_KEYS

        assert result["title"] == "Director of Creative Strategy"
        assert result["company"] == "Superside"
        # URL pública CONSTRUIDA por id de Mongo — absoluteJobUrl ya no
        # existe en la API v2 y /jobs/<key> da 404: el slug NO debe usarse.
        assert result["url"] == "https://thehub.io/jobs/6a728411650cb842b85b5040"
        assert "director-of-creative-strategy" not in result["url"]
        assert result["remote"] is True
        assert result["location"] == "Colombia"
        # description limpia de HTML.
        assert "<" not in result["description"]
        assert "Superside is hiring" in result["description"]
        # Logo servido desde el CDN imgix.
        assert result["logo"] == (
            "https://thehub-io.imgix.net"
            "/files/s3/20230413134639-fd77455089345a11e758a84d126a6f83.png"
        )
        # createdAt (ISO8601 Z) → published_at timezone-aware en UTC.
        assert result["published_at"] == datetime(
            2026, 8, 5, 0, 30, 9, 123000, tzinfo=timezone.utc
        )
        # Salario textual ("competitive") NO se mapea a numérico.
        assert result["salary_min_chf"] is None
        assert result["salary_max_chf"] is None
        assert result["salary_original"] is None

    def test_normalize_missing_fields(self):
        # Raw mínimo: solo title + id (forma degradada del listado v2).
        raw = {
            "id": "6a630388ec77206d10a2cd74",
            "title": "Backend Developer",
        }
        result = TheHubProvider().normalize_job(raw)

        assert set(result.keys()) == _EXPECTED_KEYS
        assert result["title"] == "Backend Developer"
        assert result["url"] == "https://thehub.io/jobs/6a630388ec77206d10a2cd74"
        assert result["company"] == ""
        # location ausente → dict vacío tolerado → cadena vacía, canton None.
        assert result["location"] == ""
        assert result["canton"] is None
        assert result["logo"] is None
        assert result["remote"] is False
        # Sin createdAt (detalle fallido) → sin fecha, nunca inventada.
        assert result["published_at"] is None

    def test_normalize_without_id_yields_empty_url(self):
        # Sin id no hay URL construible: url vacía ⇒ _process_raw_jobs la
        # descarta (contrato "title and url must be non-empty"). Un
        # absoluteJobUrl residual NO debe rescatarla: ya no existe en v2.
        provider = TheHubProvider()
        raw = {
            "title": "Ghost Job",
            "absoluteJobUrl": "https://thehub.io/jobs/legacy",
        }
        assert provider.normalize_job(raw)["url"] == ""
        assert provider._process_raw_jobs([raw]) == []

    def test_normalize_empty_location_dict(self):
        # location {} es habitual en The Hub: no debe romper.
        raw = {
            "id": "6a630388ec77206d10a2cd74",
            "title": "Data Engineer",
            "company": {"name": "ACME"},
            "location": {},
            "isRemote": True,
        }
        result = TheHubProvider().normalize_job(raw)
        _assert_normalized(result, "thehub")
        assert result["location"] == ""
        assert result["canton"] is None
        assert result["logo"] is None

    def test_normalize_rejects_non_objectid(self):
        """VD.9/H3: el id se valida como ObjectId de Mongo (24 hex) antes de
        interpolarlo en URLs — un id inyectado ('../..', '//evil.com')
        acababa persistido como URL clicable y disparaba un GET con
        traversal contra api.thehub.io. Un id no válido se trata como
        ausente ⇒ url vacía ⇒ _process_raw_jobs descarta la oferta. (Las 40
        filas antiguas de thehub en BD son 40/40 ObjectIds de esa forma.)"""
        provider = TheHubProvider()
        for bad_id in [
            "../..",
            "x?y=1",
            "//evil.com",
            "6A728411650CB842B85B5040",  # 24 chars pero hex en mayúsculas
            "6a728411650cb842b85b50",  # 22 chars: longitud incorrecta
            "",
        ]:
            raw = {"id": bad_id, "title": "Injected"}
            assert provider.normalize_job(raw)["url"] == ""
            assert provider._process_raw_jobs([raw]) == []

    async def test_fetch_jobs_survives_malformed_api(self, monkeypatch):
        """VD.9/H1 (+V2-3): una API malformada NO tumba el lote entero. Antes,
        un doc no-dict, un id no-string o un `docs` no-lista lanzaban
        AttributeError desde _enrich/fetch_jobs y se perdía todo el run de
        thehub (incluidos los refrescos de last_seen_at de páginas ya
        descargadas) — asimétrico con normalize_job, que sí los tolera vía
        _process_raw_jobs. Ahora: doc no-dict se salta con log, id no-string
        cuenta como ausente y un `docs` no-lista corta la paginación.

        Endurecido en V2-3 (mutación): (1) `docs` NO ITERABLE (7) en la página
        2 — con un dict, iterar daba claves string que el guard de doc-no-dict
        descartaba y el mutante sin guard sobrevivía — y aserción de que la
        página 3 NO se pide (el corte es real, no un continue); (2) un id
        string malicioso ("../../admin") atraviesa fetch_jobs y se aserta que
        NINGUNA URL solicitada contiene el traversal — un passthrough en
        _valid_job_id habría lanzado un GET con traversal contra
        api.thehub.io sin que ningún test lo viera."""
        listing_p1 = {
            "docs": [
                {
                    "id": "6a728411650cb842b85b5040",
                    "title": "Valid Job",
                    "company": {"name": "ACME"},
                },
                {"id": 123, "title": "Numeric Id"},  # id no-string
                "string-doc",  # doc no-dict
                {"id": "../../admin", "title": "Injected Path"},  # id traversal
            ],
            "pages": 3,
        }
        listing_p2 = {"docs": 7}  # docs no-lista Y no iterable

        requested_urls: list[str] = []
        pages_requested: list[int] = []

        async def fake_fetch(client, url, **kwargs):
            requested_urls.append(url)
            if url == TheHubProvider.API_URL:
                page = kwargs.get("params", {}).get("page")
                pages_requested.append(page)
                return listing_p1 if page == 1 else listing_p2
            return None  # detalle: fallo simulado (agotó reintentos)

        monkeypatch.setattr(thehub_module, "fetch_with_retry", fake_fetch)
        monkeypatch.setattr(thehub_module, "PAGE_DELAY_SECONDS", 0)
        monkeypatch.setattr(thehub_module, "DETAIL_DELAY_SECONDS", 0)

        jobs = await TheHubProvider().fetch_jobs("")

        # Solo sobrevive la oferta válida: el id numérico y el traversal
        # quedan sin URL (se descartan con log), el doc string se salta y la
        # página 2 corta la paginación sin excepción.
        assert [j["title"] for j in jobs] == ["Valid Job"]
        assert jobs[0]["url"] == "https://thehub.io/jobs/6a728411650cb842b85b5040"
        # El corte por docs malformado es real: la página 3 nunca se pide.
        assert pages_requested == [1, 2]
        # El id traversal jamás se interpola en una URL de detalle: ningún
        # GET (listado o detalle) contiene el path inyectado.
        assert all("../" not in u and "admin" not in u for u in requested_urls)

    async def test_fetch_jobs_v2_listing_plus_detail(self, monkeypatch):
        """Camino completo con las fixtures reales: listado v2 → detalle por
        id. El detalle de la 2ª oferta FALLA (None) y la oferta se emite
        igualmente con los campos del listado — el run no se cae."""
        listing = _load_fixture("thehub_v2_jobs_p1.json")
        detail = _load_fixture("thehub_job_single.json")
        requested: list[str] = []

        async def fake_fetch(client, url, **kwargs):
            requested.append(url)
            if url == TheHubProvider.API_URL:
                # Solo la página 1 trae docs; la 2 corta la paginación.
                if kwargs.get("params", {}).get("page") == 1:
                    return listing
                return {"docs": []}
            if url.endswith("/jobs/single/6a728411650cb842b85b5040"):
                return detail
            # Detalle de la 2ª oferta: fallo simulado (agotó reintentos).
            return None

        monkeypatch.setattr(thehub_module, "fetch_with_retry", fake_fetch)
        monkeypatch.setattr(thehub_module, "PAGE_DELAY_SECONDS", 0)
        monkeypatch.setattr(thehub_module, "DETAIL_DELAY_SECONDS", 0)

        jobs = await TheHubProvider().fetch_jobs("")

        assert len(jobs) == 2
        # El listado sale de la API v2 real — thehub.io/api/jobs es un 404
        # de Keystone desde el rediseño del portal.
        assert requested[0] == "https://api.thehub.io/v2/jobs"
        # El detalle se pidió por id de Mongo, nunca por key/slug.
        detail_urls = [u for u in requested if "/jobs/single/" in u]
        assert detail_urls == [
            "https://api.thehub.io/jobs/single/6a728411650cb842b85b5040",
            "https://api.thehub.io/jobs/single/6a630388ec77206d10a2cd74",
        ]
        assert not any("director-of-creative-strategy" in u for u in detail_urls)

        enriched, degraded = jobs
        # Oferta CON detalle: description + fecha del detalle.
        assert enriched["url"] == "https://thehub.io/jobs/6a728411650cb842b85b5040"
        assert "Superside is hiring" in enriched["description"]
        assert enriched["published_at"] == datetime(
            2026, 8, 5, 0, 30, 9, 123000, tzinfo=timezone.utc
        )
        # Oferta SIN detalle: se emite con lo del listado (misma URL/hash
        # que tendría con detalle → la re-vista refresca last_seen_at).
        assert degraded["title"] == "Tech Lead  / Cofounder"
        assert degraded["company"] == "Greppet"
        assert degraded["url"] == "https://thehub.io/jobs/6a630388ec77206d10a2cd74"
        assert degraded["published_at"] is None
