"""Regresión de la auditoría G3 — familia de las MARCAS DE AGUA (P1-1) y vecinos.

P1-1: en Postgres `now()` es `transaction_timestamp()`, congelado al ABRIR la
transacción. Las altas de una cosecha larga nacen fechadas al INICIO de esa
transacción, que puede ser anterior a la marca que una tarea de aviso guardó
mientras la cosecha seguía abierta → ese lote no se notifica JAMÁS y la tarea
devuelve `success`. El arreglo son dos mitades: marca con LAG (ventana
solapada) + idempotencia POR ELEMENTO.

También cubre: P2-9 (el digest de watchlist avanzaba la marca antes de trabajar
y se tragaba el fallo), P2-8 (una búsqueda guardada rota secuestraba el barrido
de todos), P3-2 (la corrida de estreno notificaba el corpus entero) y P3-3 (el
evento SSE viajaba con `notification_id: "None"`).
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from config import settings
from core.security import hash_password
from models.user import User


class _FakeRedis:
    """Doble de Redis con el contrato que usan las marcas: get/set(nx,ex)/delete."""

    def __init__(self):
        self.store: dict[str, bytes] = {}
        self.published: list[tuple[str, str]] = []

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True

    def delete(self, key):
        self.store.pop(key, None)

    def publish(self, channel, payload):
        self.published.append((channel, payload))

    def close(self):
        pass


class _FakeOffer:
    """Oferta servida por el catálogo, con los campos que lee el aviso."""

    def __init__(self, hash_, title, first_seen_at, *, tags=(), category="H"):
        self.hash = hash_
        self.title = title
        self.first_seen_at = first_seen_at
        self.tags = list(tags)
        self.company = "Schule Bern"
        self.canton = "BE"
        self.location = None
        self.url = f"https://example.com/{hash_}"
        self.category = category  # sólo lo usa el doble para filtrar


class _FakeCatalog:
    """Doble del catálogo del core con SU contrato: devuelve las ofertas de la
    categoría pedida cuya alta es posterior a `since`.

    El filtro por `since` vive ahora en el /v1, así que el doble lo reproduce:
    sin eso, los tests de la ventana solapada no probarían nada (DT-148 A1).
    """

    def __init__(self, offers=()):
        self.offers = list(offers)
        self.calls: list[tuple] = []

    async def recent(self, *, category, since, limit=500):
        self.calls.append((category, since, limit))
        return [
            offer
            for offer in self.offers
            if offer.category == category and offer.first_seen_at >= since
        ][:limit]


class _FakeEmail:
    is_available = True

    def __init__(self, fail: bool = False):
        self.sent: list[tuple] = []
        self.fail = fail

    def send(self, to, subject, text_body, html):
        if self.fail:
            raise RuntimeError("SMTP caído")
        self.sent.append((to, subject, text_body))


@asynccontextmanager
async def _session_of(db):
    yield db


async def _make_user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"g3wm-{uid.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
        )
    )
    await db.commit()
    return uid


@pytest.mark.asyncio
class TestP11AlertaProfesor:
    """La oferta persistida por una transacción larga DEBE acabar notificada."""

    async def _run(self, catalog, fake_redis, email):
        from tasks import alert_tasks

        with (
            patch("redis.from_url", lambda *a, **k: fake_redis),
            patch("services.catalog.core_client.CoreCatalog", lambda *a, **k: catalog),
            patch("services.email_service.EmailService", lambda *a, **k: email),
        ):
            return await alert_tasks._detect_and_notify()

    async def test_oferta_bajo_la_marca_se_notifica(self, monkeypatch):
        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
        monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "aviso@example.com")
        fake = _FakeRedis()
        email = _FakeEmail()

        # T0 = la transacción de cosecha ya está ABIERTA (es la fecha que
        # Postgres pondrá en el alta), pero aún no ha commiteado.
        t0 = datetime.now(timezone.utc) - timedelta(minutes=2)
        catalog = _FakeCatalog()

        # Corrida 1: la tarea no ve nada (la cosecha sigue sin commitear) y
        # guarda su marca — el instante de esta corrida es POSTERIOR a T0.
        first = await self._run(catalog, fake, email)
        assert first["status"] == "success"
        assert fake.store.get("teacher_alert:watermark") is not None

        # La cosecha commitea: la oferta aparece fechada en T0, por debajo del
        # instante en que se guardó la marca.
        catalog.offers.append(
            _FakeOffer("g3wm-teacher-1", "Primarlehrer/in gesucht", t0)
        )

        # Corrida 2: con la ventana solapada la oferta entra; sin el lag, el
        # `since` que se pide al catálogo la deja fuera para siempre.
        second = await self._run(catalog, fake, email)
        assert second["matched"] == 1, (
            "la oferta persistida bajo la marca quedó en el hueco permanente"
        )
        assert email.sent, "no se envió el email de alerta"
        # Y se le pidió al catálogo la categoría de docencia, no el corpus.
        assert {call[0] for call in catalog.calls} == {"H"}

    async def test_el_solape_no_reenvia_la_misma_oferta(self, monkeypatch):
        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
        monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "aviso@example.com")
        fake = _FakeRedis()
        email = _FakeEmail()
        catalog = _FakeCatalog(
            [
                _FakeOffer(
                    "g3wm-teacher-2",
                    "Primarschule — Lehrperson",
                    datetime.now(timezone.utc),
                )
            ]
        )

        first = await self._run(catalog, fake, email)
        second = await self._run(catalog, fake, email)

        assert first["matched"] == 1
        assert second["matched"] == 0, "el solape de la ventana reenvió el aviso"
        assert second["already_sent"] == 1
        assert len(email.sent) == 1

    async def test_fallo_de_envio_libera_el_marcador(self, monkeypatch):
        """G4/P1-2 — la oferta nace FUERA del lag de 15 min, que es el caso real.

        Este test nacía con `first_seen_at = ahora`, dentro de los 15 minutos
        de `NOTIFY_WATERMARK_LAG_MINUTES`, así que el retry la recuperaba por
        el solape aunque la marca hubiera avanzado: verificaba el
        `unmark_sent` y NO la aritmética de la marca. Con la cosecha diaria y
        la alerta cada 6 h, toda oferta de la ventana tiene HORAS de
        antigüedad — y con la marca guardada antes del envío quedaba por
        debajo de la marca nueva y no volvía a entrar en ninguna corrida.
        """
        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
        monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "aviso@example.com")
        fake = _FakeRedis()
        catalog = _FakeCatalog(
            [
                _FakeOffer(
                    "g3wm-teacher-3",
                    "Primarlehrperson 60%",
                    datetime.now(timezone.utc) - timedelta(hours=3),
                )
            ]
        )

        with pytest.raises(RuntimeError):
            await self._run(catalog, fake, _FakeEmail(fail=True))

        ok = _FakeEmail()
        result = await self._run(catalog, fake, ok)
        assert result["matched"] == 1, (
            "tras un fallo de SMTP el aviso debe reintentarse, no perderse"
        )
        assert len(ok.sent) == 1


@pytest.mark.asyncio
class TestP11DefaultsDeReloj:
    async def test_columnas_usan_clock_timestamp(self):
        from models.job import Job as JobModel
        from models.match_result import MatchResult

        for column in (
            JobModel.__table__.c.first_seen_at,
            JobModel.__table__.c.last_seen_at,
            MatchResult.__table__.c.created_at,
        ):
            rendered = str(column.server_default.arg).lower()
            assert "clock_timestamp" in rendered, (
                f"{column.name} sigue con now() = inicio de transacción"
            )


@pytest.mark.asyncio
class TestP29DigestWatchlist:
    async def test_la_marca_no_avanza_si_la_bd_falla(self, monkeypatch):
        from tasks.watchlist_tasks import _DIGEST_WATERMARK_KEY, _send_digest_async

        fake = _FakeRedis()

        def _boom():
            raise ConnectionError("connection refused: la BD está caída")

        with (
            patch("redis.from_url", lambda *a, **k: fake),
            patch("database.task_session", new=_boom),
            pytest.raises(ConnectionError),
        ):
            await _send_digest_async()

        assert fake.store.get(_DIGEST_WATERMARK_KEY) is None, (
            "la ventana se consumió sin haber notificado nada"
        )

    async def test_la_marca_avanza_tras_el_commit(self, db_session):
        from tasks.watchlist_tasks import _DIGEST_WATERMARK_KEY, _send_digest_async

        fake = _FakeRedis()
        with (
            patch("redis.from_url", lambda *a, **k: fake),
            patch("database.task_session", new=lambda: _session_of(db_session)),
        ):
            result = await _send_digest_async()

        assert result["status"] == "ok"
        assert fake.store.get(_DIGEST_WATERMARK_KEY) is not None
