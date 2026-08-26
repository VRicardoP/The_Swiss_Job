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

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select, text, update

from config import settings
from core.security import hash_password
from models.enums import NotifyFrequency
from models.job import Job
from models.notification import Notification
from models.saved_search import SavedSearch
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


async def _make_job(db, hash_: str, *, title: str, category=None, first_seen_at=None):
    db.add(
        Job(
            hash=hash_,
            source="schuljobs",
            title=title,
            company="Schule Bern",
            url=f"https://example.com/{hash_}",
            category=category,
            is_active=True,
        )
    )
    await db.commit()
    if first_seen_at is not None:
        await db.execute(
            update(Job)
            .where(Job.hash == hash_)
            .values(first_seen_at=first_seen_at, last_seen_at=text("NOW()"))
        )
        await db.commit()


@pytest.mark.asyncio
class TestP11AlertaProfesor:
    """La oferta persistida por una transacción larga DEBE acabar notificada."""

    async def _run(self, db, fake_redis, email):
        from tasks import alert_tasks

        with (
            patch("redis.from_url", lambda *a, **k: fake_redis),
            patch("database.task_session", new=lambda: _session_of(db)),
            patch("services.email_service.EmailService", lambda *a, **k: email),
        ):
            return await alert_tasks._detect_and_notify()

    async def test_oferta_bajo_la_marca_se_notifica(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
        monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "aviso@example.com")
        fake = _FakeRedis()
        email = _FakeEmail()

        # T0 = la transacción de cosecha ya está ABIERTA (es la fecha que
        # Postgres pondrá en `first_seen_at`), pero aún no ha commiteado.
        t0 = datetime.now(timezone.utc) - timedelta(minutes=2)

        # Corrida 1: la tarea no ve nada (la cosecha sigue sin commitear) y
        # guarda su marca — el instante de esta corrida es POSTERIOR a T0.
        first = await self._run(db_session, fake, email)
        assert first["status"] == "success"
        assert fake.store.get("teacher_alert:watermark") is not None

        # La cosecha commitea: la oferta aparece fechada en T0, por debajo del
        # instante en que se guardó la marca.
        await _make_job(
            db_session,
            "g3wm-teacher-1",
            title="Primarlehrer/in gesucht",
            category="H",
            first_seen_at=t0,
        )

        # Corrida 2: con la ventana solapada la oferta entra; sin el lag,
        # `first_seen_at > marca` es falso para siempre.
        second = await self._run(db_session, fake, email)
        assert second["matched"] == 1, (
            "la oferta persistida bajo la marca quedó en el hueco permanente"
        )
        assert email.sent, "no se envió el email de alerta"

    async def test_el_solape_no_reenvia_la_misma_oferta(self, db_session, monkeypatch):
        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
        monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "aviso@example.com")
        fake = _FakeRedis()
        email = _FakeEmail()
        await _make_job(
            db_session,
            "g3wm-teacher-2",
            title="Primarschule — Lehrperson",
            category="H",
        )

        first = await self._run(db_session, fake, email)
        second = await self._run(db_session, fake, email)

        assert first["matched"] == 1
        assert second["matched"] == 0, "el solape de la ventana reenvió el aviso"
        assert second["already_sent"] == 1
        assert len(email.sent) == 1

    async def test_fallo_de_envio_libera_el_marcador(self, db_session, monkeypatch):
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
        await _make_job(
            db_session,
            "g3wm-teacher-3",
            title="Primarlehrperson 60%",
            category="H",
            first_seen_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )

        with pytest.raises(RuntimeError):
            await self._run(db_session, fake, _FakeEmail(fail=True))

        ok = _FakeEmail()
        result = await self._run(db_session, fake, ok)
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


@pytest.mark.asyncio
class TestSavedSearches:
    async def _make_search(self, db, uid, *, filters, last_run_at=None, push=False):
        search = SavedSearch(
            user_id=uid,
            name=f"g3-{uuid.uuid4().hex[:6]}",
            filters=filters,
            min_score=50,
            notify_frequency=NotifyFrequency.daily,
            notify_push=push,
            is_active=True,
            last_run_at=last_run_at,
            total_matches=0,
        )
        db.add(search)
        await db.commit()
        return search

    async def test_p32_la_corrida_de_estreno_no_cuenta_el_corpus_viejo(
        self, db_session
    ):
        from tasks.search_tasks import _execute_single_search

        uid = await _make_user(db_session)
        await _make_job(
            db_session,
            "g3wm-old-1",
            title="Oferta antigua",
            first_seen_at=datetime.now(timezone.utc) - timedelta(days=300),
        )
        search = await self._make_search(db_session, uid, filters={}, last_run_at=None)

        count = await _execute_single_search(db_session, search, settings)
        assert count == 0, "la corrida de estreno notificó el corpus activo entero"

    async def test_p11_la_ventana_solapa_con_la_cosecha_lenta(self, db_session):
        from tasks.search_tasks import _execute_single_search

        uid = await _make_user(db_session)
        search = await self._make_search(db_session, uid, filters={})
        # Estreno: fija last_run_at = ahora.
        await _execute_single_search(db_session, search, settings)
        marca = search.last_run_at

        # La cosecha commitea una oferta fechada al inicio de su transacción,
        # dos minutos ANTES de esa marca.
        await _make_job(
            db_session,
            "g3wm-lenta-1",
            title="Oferta de cosecha lenta",
            first_seen_at=marca - timedelta(minutes=2),
        )
        count = await _execute_single_search(db_session, search, settings)
        assert count == 1, "la oferta cayó en el hueco de la marca de agua"

    async def test_p33_el_evento_sse_lleva_el_uuid_real(self, db_session):
        from tasks.search_tasks import _execute_single_search

        uid = await _make_user(db_session)
        search = await self._make_search(db_session, uid, filters={}, push=True)
        await _execute_single_search(db_session, search, settings)
        await _make_job(db_session, "g3wm-sse-1", title="Oferta nueva")

        fake = _FakeRedis()
        with patch("redis.from_url", lambda *a, **k: fake):
            await _execute_single_search(db_session, search, settings)

        assert fake.published, "no se publicó el evento SSE"
        payload = json.loads(fake.published[-1][1])
        nid = payload["data"]["notification_id"]
        assert nid != "None", "el evento viajó sin id de notificación"
        uuid.UUID(nid)
        stored = (
            await db_session.execute(
                select(Notification).where(Notification.id == uuid.UUID(nid))
            )
        ).scalar_one_or_none()
        assert stored is not None, "el evento se publicó antes del commit"

    async def test_p28_una_busqueda_rota_no_secuestra_el_barrido(self, db_session):
        from tasks.search_tasks import _run_saved_searches_async

        uid = await _make_user(db_session)
        # `filters` hostil: la tarea lo consume como texto (`.split`).
        await self._make_search(db_session, uid, filters={"source": 123})
        await self._make_search(db_session, uid, filters={"source": "schuljobs"})

        with patch("database.task_session", new=lambda: _session_of(db_session)):
            result = await _run_saved_searches_async()

        assert result["status"] == "success"
        assert result["failed"] == 1
        assert result["searches_processed"] == 1, (
            "la búsqueda rota abortó el barrido de las demás"
        )
