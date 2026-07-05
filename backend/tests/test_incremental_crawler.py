"""Tests del crawler incremental (cursores + early-stop) y las fuentes restringidas.

Cubre:
- BaseJobProvider.job_identity y _page_all_known (lógica del early-stop).
- CursorStore.update_after_run / known_identities (sin DB).
- Conectores restringidos: auth_missing sin credencial y gating en el registro.
"""

from providers import get_all_providers, get_provider_names
from providers.restricted import JobCloudPartnerProvider, XingPartnerProvider
from services.cursor_store import CursorStore
from services.job_service import BaseJobProvider
from models.source_cursor import SourceCursor


class _Dummy(BaseJobProvider):
    """Subclase concreta mínima para probar los helpers de BaseJobProvider."""

    SOURCE_NAME = "dummy"

    async def fetch_jobs(self, query, location="Switzerland"):
        return []

    def normalize_job(self, raw):
        return raw


# --- Identidad y early-stop ----------------------------------------------------


def test_job_identity_prefers_url_then_fallbacks():
    assert BaseJobProvider.job_identity({"url": "u1"}) == "u1"
    assert BaseJobProvider.job_identity({"detail_url": "d1"}) == "d1"
    assert BaseJobProvider.job_identity({"source_id": "s1"}) == "s1"
    assert BaseJobProvider.job_identity({"hash": "h1"}) == "h1"
    assert BaseJobProvider.job_identity({}) == ""


def test_page_all_known_without_cursor_never_stops():
    d = _Dummy()
    # Sin known_urls inyectado, nunca corta (comportamiento legacy).
    assert d._page_all_known([{"url": "a"}, {"url": "b"}]) is False


def test_page_all_known_true_when_all_seen():
    d = _Dummy()
    d._known_urls = {"a", "b", "c"}
    assert d._page_all_known([{"url": "a"}, {"url": "b"}]) is True


def test_page_all_known_false_when_a_new_job_appears():
    d = _Dummy()
    d._known_urls = {"a", "b"}
    # "z" es nuevo → hay novedad → no parar.
    assert d._page_all_known([{"url": "a"}, {"url": "z"}]) is False


def test_page_all_known_empty_page_is_false():
    d = _Dummy()
    d._known_urls = {"a"}
    assert d._page_all_known([]) is False


# --- CursorStore ---------------------------------------------------------------


def test_update_after_run_prepends_and_caps(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "CURSOR_RECENT_IDENTITIES_MAX", 3)
    cursor = SourceCursor(source_key="x", scope_key="default", recent_identities=["b", "c"])

    CursorStore().update_after_run(
        cursor, fetched_identities=["a", "b"], new_count=1, pages_read=1
    )

    # "a" nuevo va primero; "b" dedup; se recorta a 3.
    assert cursor.recent_identities == ["a", "b", "c"]
    assert cursor.consecutive_empty_runs == 0
    assert cursor.bootstrap_complete is True
    assert cursor.avg_new_jobs_per_run > 0


def test_update_after_run_counts_empty_runs():
    cursor = SourceCursor(source_key="x", scope_key="default", recent_identities=[])
    store = CursorStore()

    store.update_after_run(cursor, [], new_count=0, pages_read=1)
    store.update_after_run(cursor, [], new_count=0, pages_read=1)

    assert cursor.consecutive_empty_runs == 2
    assert cursor.last_empty_at is not None


def test_known_identities_returns_set():
    cursor = SourceCursor(source_key="x", recent_identities=["a", "b", "a"])
    assert CursorStore.known_identities(cursor) == {"a", "b"}


# --- Fuentes restringidas ------------------------------------------------------


async def test_restricted_provider_auth_missing_returns_empty(monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "JOBCLOUD_PARTNER_API_KEY", "")
    provider = JobCloudPartnerProvider()
    jobs = await provider.fetch_jobs("", "Switzerland")
    assert jobs == []


def test_restricted_sources_registered_but_not_active_without_creds(monkeypatch):
    from config import settings

    # Sin credenciales, get_all_providers NO debe instanciarlas...
    for attr in (
        "JOBCLOUD_PARTNER_API_KEY",
        "LINKEDIN_PARTNER_TOKEN",
        "INDEED_PARTNER_KEY",
        "GLASSDOOR_PARTNER_KEY",
        "XING_PARTNER_TOKEN",
    ):
        monkeypatch.setattr(settings, attr, "")

    active = {p.get_source_name() for p in get_all_providers()}
    restricted = {
        "jobcloud_partner",
        "linkedin_authorized",
        "indeed_partner",
        "glassdoor_partner",
        "xing_partner",
    }
    assert restricted.isdisjoint(active)

    # ...pero SÍ están registradas en el catálogo (restricted_ready).
    names = set(get_provider_names())
    assert restricted.issubset(names)


def test_restricted_provider_normalize_shape():
    provider = XingPartnerProvider()
    job = provider.normalize_job(
        {"title": "T", "company": "C", "url": "https://x/y", "description": "d"}
    )
    assert job["source"] == "xing_partner"
    assert job["url"] == "https://x/y"
    assert BaseJobProvider._REQUIRED_FIELDS.issubset(job.keys())
