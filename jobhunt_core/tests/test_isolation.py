"""A-01: el aislamiento operativo es ESTRUCTURAL, no una convención.

DoD: el worker legacy no cruza (colas disjuntas), broker en Redis dedicado,
locks con namespace propio.
"""

from urllib.parse import urlsplit

from jobhunt_core.celery_app import celery_app
from jobhunt_core.config import CoreSettings, settings

# Colas del backend legacy (celery_app del backend: default/scraping/ai).
LEGACY_QUEUES = {"default", "scraping", "ai"}


def test_default_broker_is_dedicated_redis(monkeypatch):
    # La topología por defecto apunta al servicio dedicado. CI puede sustituir
    # el hostname por localhost para alcanzar el contenedor publicado; en prod,
    # CoreSettings exige redis-core (cubierto por test_migrate_guards.py).
    monkeypatch.delenv("CORE_BROKER_URL", raising=False)
    monkeypatch.delenv("CORE_RESULT_BACKEND", raising=False)
    defaults = CoreSettings(_env_file=None)
    assert urlsplit(defaults.CORE_BROKER_URL).hostname == "redis-core"
    assert urlsplit(defaults.CORE_RESULT_BACKEND).hostname == "redis-core"


def test_all_queues_are_core_namespaced():
    queues = {celery_app.conf.task_default_queue}
    for route in celery_app.conf.task_routes.values():
        queues.add(route["queue"])
    assert all(q.startswith("core.") for q in queues), queues
    assert queues.isdisjoint(LEGACY_QUEUES)


def test_expected_core_queues_exist():
    routed = {r["queue"] for r in celery_app.conf.task_routes.values()}
    assert routed == {
        "core.harvest",
        "core.embedding",
        "core.matching",
        "core.notifications",
        "core.default",  # despacho del outbox (A-10)
    }


def test_lock_prefix_is_own_namespace():
    assert settings.CORE_LOCK_PREFIX == "jobhunt:"


def test_db_schema_is_own():
    assert settings.CORE_DB_SCHEMA == "jobhunt"
    assert "jobhunt_core" in settings.CORE_DATABASE_URL  # rol propio, no el admin
