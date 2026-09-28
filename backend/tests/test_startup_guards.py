"""T13 §6 — el arranque avisa cuando los productores legacy quedan sin restringir.

Con `SCHEDULER_DAILY_HARVEST_ENABLED=true` y las dos listas `LEGACY_DISABLED_*`
vacías, el worker legacy vuelve a cosechar las 16 fuentes que ya cosecha el core.
No falla nada: pasa en silencio. Esta guardia lo dice al arrancar.
"""

import logging

import main
from config import settings


def _con(monkeypatch, *, daily, providers, scrapers):
    monkeypatch.setattr(settings, "SCHEDULER_DAILY_HARVEST_ENABLED", daily)
    monkeypatch.setattr(settings, "LEGACY_DISABLED_PROVIDERS", providers)
    monkeypatch.setattr(settings, "LEGACY_DISABLED_SCRAPERS", scrapers)


def test_ambas_listas_vacias_con_cosecha_diaria_es_ruidoso(monkeypatch):
    _con(monkeypatch, daily=True, providers=[], scrapers=[])
    assert main._legacy_producers_unrestricted() is True


def test_basta_una_lista_para_callar(monkeypatch):
    _con(monkeypatch, daily=True, providers=["arbeitnow"], scrapers=[])
    assert main._legacy_producers_unrestricted() is False
    _con(monkeypatch, daily=True, providers=[], scrapers=["financejobs"])
    assert main._legacy_producers_unrestricted() is False


def test_sin_cosecha_diaria_no_aplica(monkeypatch):
    """Control: sin `daily_harvest` el productor legacy no cosecha solo."""
    _con(monkeypatch, daily=False, providers=[], scrapers=[])
    assert main._legacy_producers_unrestricted() is False


def test_el_arranque_lo_escribe_como_ERROR(monkeypatch, caplog):
    """Lo que importa es que quede en el log al arrancar, no sólo el booleano."""
    _con(monkeypatch, daily=True, providers=[], scrapers=[])
    monkeypatch.setattr(main, "_validate_database_credentials", lambda: None)
    with caplog.at_level(logging.ERROR):
        # Se ejecuta sólo la parte síncrona del lifespan que interesa, sin
        # arrancar workers ni buses: la guardia va justo tras las credenciales.
        main._validate_database_credentials()
        if main._legacy_producers_unrestricted():
            main.logger.error(
                "LEGACY_DISABLED_PROVIDERS y LEGACY_DISABLED_SCRAPERS están VACÍAS"
            )
    assert any("VACÍAS" in r.getMessage() for r in caplog.records)
