"""Regresiones de la auditoría G3 — lote B: providers + capa HTTP.

- P1-3: `httpx.AsyncClient` no seguía redirecciones, así que un portal que
  empieza a responder 308 (ostjob/zentraljob desde 2026-08-18) mataba la
  fuente entera; y el `continue` de los status no cubiertos reintentaba SIN
  pausa (cuatro peticiones en ráfaga).
- P2-6: seis providers RSS + careerjet convertían un HTTP 200 con estructura
  ilegible en `[]` mudo → el panel presentaba la fuente rota como sequía.
- P3-11: `jooble.totalCount` sin `_safe_int`.
- P3-12: `base_chmedia` daba la MISMA URL constante a toda oferta sin enlace.
- P3-13: `arbeitnow`/`jobgether` emitían identidad volátil (id de la URL).
- P3-14: `null` explícito en `untalent`/`reliefweb` → AttributeError, y el
  `remote=True` erróneo de reliefweb cuando faltaba el país.
- P3-15: `nav_arbeidsplassen.hits.total` asumía formato Elasticsearch 7+; y
  `restricted` con credencial y sin conector devolvía `[]` mudo.
"""

import httpx
import pytest

from config import settings
from providers.arbeitnow import ArbeitnowProvider
from providers.base_chmedia import build_chmedia_url
from providers.careerjet import CareerjetProvider
from providers.euremotejobs import EURemoteJobsProvider
from providers.globaljobs import GlobalJobsProvider
from providers.jobgether import JobgetherProvider
from providers.jobspresso import JobspressoProvider
from providers.jooble import JoobleProvider
from providers.nav_arbeidsplassen import NavArbeidsplassenProvider
from providers.proz import ProzProvider
from providers.reliefweb import ReliefWebProvider
from providers.remoteco import RemoteCoProvider
from providers.restricted import RestrictedPartnerProvider
from providers.untalent import UNTalentProvider
from providers.weworkremotely import WeWorkRemotelyProvider
from utils import fetch_diagnostics as diag
from utils.http import fetch_rss, fetch_with_retry

# HTML de interstitial (WAF/Cloudflare) servido con HTTP 200: el disparador
# real del P2-6 — el feed responde "bien" pero no hay XML que leer.
_CHALLENGE_HTML = "<html><head><title>Just a moment...</title></head></html>"


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    """Sin esperas reales de backoff en los reintentos de utils.http."""

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("utils.http.asyncio.sleep", _no_sleep)


def _mock_transport(monkeypatch, handler):
    """Fuerza a TODO httpx.AsyncClient de este proceso a usar un MockTransport.

    Se parchea `__init__` (y no el método get/post) a propósito: así la
    máquina de redirecciones real de httpx sigue en juego, que es justo lo que
    verifica el P1-3.
    """
    original_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)


class TestP13SeguirRedirecciones:
    """G3/P1-3: 308 permanente → la fuente moría en vez de seguir el redirect."""

    @pytest.mark.asyncio
    async def test_fetch_with_retry_sigue_un_308(self, monkeypatch):
        calls: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            if request.url.path == "/search":
                return httpx.Response(
                    308, headers={"Location": "https://api.ostjob.ch/v2/search"}
                )
            return httpx.Response(200, json={"items": [1, 2]})

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(client, "https://api.ostjob.ch/search")

        assert data == {"items": [1, 2]}
        assert diag.issues() == []
        assert len(calls) == 2, (
            f"debe seguir el redirect una vez, no reintentar: {calls}"
        )

    @pytest.mark.asyncio
    async def test_fetch_with_retry_308_preserva_el_metodo_post(self, monkeypatch):
        """307/308 preservan el método: el POST de jooble sigue siendo POST."""
        methods: list[str] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            methods.append(request.method)
            if request.url.path == "/api/key":
                return httpx.Response(
                    308, headers={"Location": "https://jooble.org/api/key/v2"}
                )
            return httpx.Response(200, json={"jobs": []})

        _mock_transport(monkeypatch, _handler)

        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(
                client,
                "https://jooble.org/api/key",
                method="POST",
                json_body={"keywords": "x"},
            )

        assert data == {"jobs": []}
        assert methods == ["POST", "POST"]

    @pytest.mark.asyncio
    async def test_fetch_rss_sigue_un_301(self, monkeypatch):
        def _handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/feed":
                return httpx.Response(
                    301, headers={"Location": "https://example.org/feed/"}
                )
            return httpx.Response(200, text="<rss><channel/></rss>")

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        async with httpx.AsyncClient() as client:
            text = await fetch_rss(client, "https://example.org/feed")

        assert text == "<rss><channel/></rss>"
        assert diag.issues() == []

    @pytest.mark.asyncio
    async def test_un_status_no_declarado_ni_se_reintenta_ni_pausa(self, monkeypatch):
        """G3/P1-3 + G4/P2-4.

        G3 cerró la «ráfaga de 4 peticiones en 0.00 s» metiendo el camino mudo
        en la escalera de reintentos. Eso costaba 7 s por petición a TODO
        status no-2xx que no fuera 4xx —los 520/521/522/524 de Cloudflare
        incluidos—, y `thehub` paga el helper una vez POR OFERTA: ×46 detalles
        = 322 s contra un `soft_time_limit` de 540 s para los 20 providers.
        G4 lo cierra por el otro lado: `retry_on_status` es la única fuente de
        verdad, así que no hay ni ráfaga ni pausa — hay UNA petición.
        """
        waits: list[float] = []
        peticiones: list[str] = []

        async def _record_sleep(seconds):
            waits.append(seconds)

        monkeypatch.setattr("utils.http.asyncio.sleep", _record_sleep)

        def _handler(request: httpx.Request) -> httpx.Response:
            # 520: challenge de Cloudflare. Ni está en DEFAULT_RETRY_STATUSES
            # ni es 4xx → era el camino que pausaba 7 s en balde.
            peticiones.append(str(request.url))
            return httpx.Response(520, text="cloudflare")

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(client, "https://example.org/api")

        assert data is None
        assert len(peticiones) == 1, "un status no reintentable se reintentó"
        assert waits == [], f"pausa inútil de {sum(waits)} s en un 520"
        assert [i.status for i in diag.issues()] == [520], (
            "el fallo tiene que seguir registrado para source_health"
        )

    @pytest.mark.asyncio
    async def test_un_status_declarado_retryable_sigue_pausando(self, monkeypatch):
        """Control de G3/P1-3: lo que SÍ se reintenta sigue con su escalera."""
        waits: list[float] = []

        async def _record_sleep(seconds):
            waits.append(seconds)

        monkeypatch.setattr("utils.http.asyncio.sleep", _record_sleep)
        _mock_transport(
            monkeypatch, lambda request: httpx.Response(503, text="unavailable")
        )

        diag.begin()
        async with httpx.AsyncClient() as client:
            data = await fetch_with_retry(client, "https://example.org/api")

        assert data is None
        assert waits == [1.0, 2.0, 4.0], f"backoff exponencial esperado, hubo {waits}"


_RSS_PROVIDERS = [
    GlobalJobsProvider,
    WeWorkRemotelyProvider,
    JobspressoProvider,
    EURemoteJobsProvider,
    ProzProvider,
    RemoteCoProvider,
]


class TestP26EstructuraIlegibleEsError:
    """G3/P2-6: un 200 que no podemos leer NO es una sequía legítima."""

    @pytest.mark.parametrize(
        "provider_cls", _RSS_PROVIDERS, ids=lambda c: c.SOURCE_NAME
    )
    @pytest.mark.parametrize(
        "body", [_CHALLENGE_HTML, ""], ids=["challenge_html", "cuerpo_vacio"]
    )
    @pytest.mark.asyncio
    async def test_rss_ilegible_sale_como_error(self, provider_cls, body, monkeypatch):
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text=body)

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        jobs = await provider_cls().fetch_jobs("")
        issues = diag.issues()

        assert jobs == []
        assert issues, f"{provider_cls.SOURCE_NAME}: el 200 ilegible debe dejar issue"
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.parametrize(
        "provider_cls", _RSS_PROVIDERS, ids=lambda c: c.SOURCE_NAME
    )
    @pytest.mark.asyncio
    async def test_rss_sin_channel_sale_como_error(self, provider_cls, monkeypatch):
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text="<feed xmlns='http://www.w3.org/2005/Atom'/>"
            )

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        jobs = await provider_cls().fetch_jobs("")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "error"

    @pytest.mark.parametrize(
        "provider_cls", _RSS_PROVIDERS, ids=lambda c: c.SOURCE_NAME
    )
    @pytest.mark.asyncio
    async def test_channel_valido_sin_items_sigue_siendo_empty(
        self, provider_cls, monkeypatch
    ):
        """Un feed bien formado y sin vacantes es `empty` legítimo: no alarmar."""

        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<rss><channel></channel></rss>")

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        jobs = await provider_cls().fetch_jobs("")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "empty"


class TestP26CareerjetMudo:
    """G3/P2-6: el affid caducado devuelve 200 con type=ERROR."""

    @pytest.fixture(autouse=True)
    def _affid(self, monkeypatch):
        monkeypatch.setattr(settings, "CAREERJET_AFFID", "x", raising=False)

    @pytest.mark.asyncio
    async def test_type_error_sale_como_error(self, monkeypatch):
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"type": "ERROR", "error": "affid not valid"}
            )

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        jobs = await CareerjetProvider().fetch_jobs("teacher")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "error"

    @pytest.mark.asyncio
    async def test_respuesta_jobs_sin_la_clave_jobs_sale_como_error(self, monkeypatch):
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"type": "JOBS", "hits": 12})

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        jobs = await CareerjetProvider().fetch_jobs("teacher")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "error"

    @pytest.mark.asyncio
    async def test_busqueda_sin_resultados_sigue_siendo_empty(self, monkeypatch):
        def _handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"type": "JOBS", "hits": 0, "jobs": []})

        _mock_transport(monkeypatch, _handler)

        diag.begin()
        jobs = await CareerjetProvider().fetch_jobs("teacher")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "empty"


class TestP311JoobleTotalCount:
    """G3/P3-11: un totalCount string lanzaba TypeError fuera de fetch_jobs."""

    @pytest.fixture(autouse=True)
    def _api_key(self, monkeypatch):
        monkeypatch.setattr(settings, "JOOBLE_API_KEY", "k", raising=False)

    @staticmethod
    def _job(idx: int) -> dict:
        return {
            "title": f"Lehrer {idx}",
            "company": "Schule",
            "link": f"https://jooble.org/jdp/{idx}",
            "snippet": "text",
            "location": "Zurich",
        }

    @pytest.mark.asyncio
    async def test_total_count_string_no_rompe_la_fuente(self, monkeypatch):
        pages: list[int] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            pages.append(len(pages) + 1)
            return httpx.Response(
                200,
                json={"totalCount": "1000", "jobs": [self._job(len(pages))]},
            )

        _mock_transport(monkeypatch, _handler)

        jobs = await JoobleProvider().fetch_jobs("lehrer")

        assert len(jobs) == JoobleProvider.MAX_PAGES
        assert len(pages) == JoobleProvider.MAX_PAGES

    @pytest.mark.asyncio
    async def test_total_count_ausente_no_corta_en_la_pagina_1(self, monkeypatch):
        pages: list[int] = []

        def _handler(_request: httpx.Request) -> httpx.Response:
            pages.append(len(pages) + 1)
            return httpx.Response(200, json={"jobs": [self._job(len(pages))]})

        _mock_transport(monkeypatch, _handler)

        jobs = await JoobleProvider().fetch_jobs("lehrer")

        assert len(jobs) == JoobleProvider.MAX_PAGES


class TestP312ChmediaUrlConstante:
    """G3/P3-12: sin enlace usable NO se inventa https://{domain} (VD.1)."""

    def test_sin_enlace_devuelve_cadena_vacia(self):
        assert build_chmedia_url("ostjob.ch", {}) == ""

    def test_mailto_sin_alternativa_devuelve_cadena_vacia(self):
        raw = {"urlApplication": "mailto:hr@schule.ch"}
        assert build_chmedia_url("ostjob.ch", raw) == ""

    def test_dos_ofertas_sin_enlace_no_comparten_url(self):
        a = build_chmedia_url("ostjob.ch", {"urlApplication": "mailto:a@x.ch"})
        b = build_chmedia_url("ostjob.ch", {"urlApplication": "mailto:b@y.ch"})
        assert a == b == ""  # ambas se descartan en _process_raw_jobs, no colisionan

    def test_externalid_sigue_construyendo_url(self):
        raw = {"externalId": "12345"}
        assert build_chmedia_url("ostjob.ch", raw) == "https://ostjob.ch/stelle/12345"


class TestP313IdentidadCanonica:
    """G3/P3-13: la reemisión de la misma vacante creaba una fila nueva."""

    def test_arbeitnow_mismo_puesto_con_id_distinto_comparte_hash(self):
        provider = ArbeitnowProvider()
        base = {
            "title": "Senior Google Performance Analytics Manager",
            "company_name": "eFly Marketplace Services GmbH",
            "description": "<p>x</p>",
            "location": "Stuttgart",
        }
        url = (
            "https://www.arbeitnow.com/jobs/companies/efly-marketplace-services-gmbh/"
            "senior-google-performance-analytics-manager-stuttgart-"
        )
        first = provider.normalize_job({**base, "url": url + "459633"})
        repost = provider.normalize_job({**base, "url": url + "198909"})

        assert first["hash"] == repost["hash"]
        # La URL publicada sigue siendo la real de cada emisión.
        assert repost["url"].endswith("198909")

    def test_arbeitnow_puestos_distintos_no_colapsan(self):
        provider = ArbeitnowProvider()
        base = {"company_name": "ACME", "description": "", "location": "Bern"}
        a = provider.normalize_job(
            {**base, "title": "Lehrer", "url": "https://x.com/jobs/lehrer-1"}
        )
        b = provider.normalize_job(
            {**base, "title": "Koch", "url": "https://x.com/jobs/koch-2"}
        )
        assert a["hash"] != b["hash"]

    def test_jobgether_mismo_puesto_con_objectid_distinto_comparte_hash(self):
        provider = JobgetherProvider()
        base = {
            "title": "Partnership Engagement Manager",
            "companyData": {"name": "ACME"},
            "requiredLocations": "Remote",
        }
        first = provider.normalize_job(
            {**base, "slug": "6a805c16490731f1e1a56c93-partnership-engagement-manager"}
        )
        repost = provider.normalize_job(
            {**base, "slug": "6b9091270000000000000000-partnership-engagement-manager"}
        )

        assert first["hash"] == repost["hash"]
        assert repost["url"].endswith(
            "/offer/6b9091270000000000000000-partnership-engagement-manager"
        )

    def test_jobgether_slug_sin_objectid_se_respeta_entero(self):
        provider = JobgetherProvider()
        base = {"title": "Accountant", "companyData": {"name": "ACME"}}
        a = provider.normalize_job({**base, "slug": "accountant-madrid"})
        b = provider.normalize_job({**base, "slug": "accountant-berlin"})
        assert a["hash"] != b["hash"]


class TestP314NullExplicito:
    """G3/P3-14: `null` explícito rompía con AttributeError (o mentía)."""

    def test_untalent_type_null_no_revienta(self):
        raw = {
            "title": "Programme Assistant",
            "organization": "UN",
            "url": "https://untalent.org/jobs/1",
            "type": None,
            "duty_station": "Geneva",
        }
        job = UNTalentProvider().normalize_job(raw)
        assert job["remote"] is False

    def test_reliefweb_language_y_country_null_no_revientan(self):
        raw = {
            "id": "1",
            "fields": {
                "title": "Field Officer",
                "source": [{"name": "NGO"}],
                "url": "https://reliefweb.int/job/1",
                "city": [{"name": "Nairobi"}],
                "country": [{"name": None}],
                "language": [{"name": None}],
            },
        }
        job = ReliefWebProvider().normalize_job(raw)
        assert job["location"] == "Nairobi"
        # Sin país NO es una oferta remota: es un dato que falta.
        assert job["remote"] is False

    def test_reliefweb_sin_ubicacion_sigue_siendo_remota(self):
        raw = {
            "id": "2",
            "fields": {
                "title": "Consultant",
                "source": [{"name": "NGO"}],
                "url": "https://reliefweb.int/job/2",
            },
        }
        assert ReliefWebProvider().normalize_job(raw)["remote"] is True


class TestP315NavTotalYRestricted:
    """G3/P3-15: `hits.total` en formato ES6 y el `[]` mudo de restricted."""

    @staticmethod
    def _hit(idx: int) -> dict:
        return {
            "_id": f"uuid-{idx}",
            "_source": {
                "title": f"Rådgiver {idx}",
                "businessName": "NAV",
                "uuid": f"uuid-{idx}",
                "properties": {"remote": "Hjemmekontor"},
            },
        }

    def test_total_hits_formato_es6_y_es7(self):
        provider = NavArbeidsplassenProvider()
        assert provider._total_hits({"total": 1234}) == 1234
        assert provider._total_hits({"total": {"value": 1234}}) == 1234
        assert provider._total_hits({"total": "1234"}) == 1234
        assert provider._total_hits({}) == 0

    @pytest.mark.asyncio
    async def test_formato_es6_no_rompe_ni_corta_la_paginacion(self, monkeypatch):
        seen: list[int] = []

        def _handler(request: httpx.Request) -> httpx.Response:
            offset = int(request.url.params.get("from", 0))
            seen.append(offset)
            return httpx.Response(
                200,
                json={
                    "hits": {
                        "total": 1000,  # ES6: entero, no {"value": N}
                        "hits": [self._hit(len(seen))],
                    }
                },
            )

        _mock_transport(monkeypatch, _handler)

        jobs = await NavArbeidsplassenProvider().fetch_jobs("")

        assert jobs, "un total en formato ES6 no debe tumbar la fuente"
        assert len(seen) > len(  # más de una página por faceta
            [o for o in seen if o == 0]
        )

    @pytest.mark.asyncio
    async def test_restricted_con_credencial_y_sin_conector_registra_issue(self):
        class _Fake(RestrictedPartnerProvider):
            SOURCE_NAME = "fake_partner"
            AUTHORIZED_ROUTE = "feed partner"

            def _credential(self) -> str:
                return "tok"

        diag.begin()
        jobs = await _Fake().fetch_jobs("")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "error"

    @pytest.mark.asyncio
    async def test_restricted_sin_credencial_sigue_mudo_y_sin_peticiones(self):
        class _Fake(RestrictedPartnerProvider):
            SOURCE_NAME = "fake_partner_off"
            CREDENTIAL_ATTR = "MISSING_TOKEN_ATTR"
            AUTHORIZED_ROUTE = "feed partner"

        diag.begin()
        jobs = await _Fake().fetch_jobs("")

        assert jobs == []
        assert diag.classify(0, diag.issues()) == "empty"
