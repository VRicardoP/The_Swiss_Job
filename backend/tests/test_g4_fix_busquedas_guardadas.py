"""G4 — familia de las BÚSQUEDAS GUARDADAS.

- **P1-3**: la guarda de `3a80587` (`try/except → rollback; continue`) era
  INERTE ante un fallo de FLUSH. Tras un flush fallido SQLAlchemy desactiva la
  transacción y EXPIRA los objetos que participaban en ella; la primera
  sentencia del handler era `logger.exception(..., search.id, search.user_id)`,
  cuyo formateo dispara un refresh de esos atributos → `PendingRollbackError`
  desde DENTRO del `except`, ANTES de llegar al `rollback`. La excepción salía
  del bucle igual que sin guarda. Y el disparador es alcanzable desde la API
  pública: `SavedSearchCreate.name` admite 200 caracteres y el título de la
  notificación (`"New matches for '<name>'"`, +18) va a un `varchar(200)`.
- **P2-3**: `q`, `seniority`, `contract_type`, `salary_min` y `salary_max` se
  declaraban en el contrato, se guardaban y se mostraban, pero NO se aplicaban.
- **P2-5**: `filter_unsent` marcaba las ofertas como «ya notificadas» y nadie
  retiraba el marcador cuando la corrida fallaba: 14 días de ofertas marcadas
  sin ninguna notificación detrás.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
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

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, bytes] = {}

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
        pass

    def close(self):
        pass


@asynccontextmanager
async def _session_of(db):
    yield db


async def _make_user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"g4ss-{uid.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
        )
    )
    await db.commit()
    return uid


async def _make_search(db, uid, *, name, filters=None, last_run_at=None) -> SavedSearch:
    search = SavedSearch(
        user_id=uid,
        name=name,
        filters=filters if filters is not None else {},
        min_score=50,
        notify_frequency=NotifyFrequency.daily,
        notify_push=False,
        is_active=True,
        last_run_at=last_run_at,
        total_matches=0,
    )
    db.add(search)
    await db.commit()
    return search


async def _make_job(db, hash_: str, **cols) -> None:
    base = dict(
        source="schuljobs",
        title="Oferta",
        company="Schule Bern",
        url=f"https://example.com/{hash_}",
        is_active=True,
    )
    base.update(cols)
    first_seen_at = base.pop("first_seen_at", None)
    db.add(Job(hash=hash_, **base))
    await db.commit()
    await db.execute(
        update(Job)
        .where(Job.hash == hash_)
        .values(
            first_seen_at=first_seen_at or datetime.now(timezone.utc),
            last_seen_at=text("NOW()"),
        )
    )
    await db.commit()


class TestP13FalloDeFlushNoSecuestraElBarrido:
    async def test_un_fallo_de_flush_no_secuestra_el_barrido(self, db_session):
        """La CLASE entera: cualquier fallo de FLUSH deja la sesión desactivada
        y los objetos EXPIRADOS. El handler tiene que hacer `rollback` ANTES de
        tocar `search.id`/`search.user_id` para el log.

        Se reproduce con el mismo mecanismo que el disparador real (un título
        más largo que `varchar(200)`), pero forzado desde el doble para que el
        test siga midiendo la CLASE aunque el recorte del título cierre el
        disparador concreto.
        """
        from tasks.search_tasks import _run_saved_searches_async

        uid = await _make_user(db_session)
        await _make_search(db_session, uid, name="rota")
        await _make_search(db_session, uid, name="sana")
        await _make_job(db_session, "g4ss-flush-1", title="Oferta nueva")

        def _notificacion_venenosa(**kw):
            # Mismo mecanismo que el disparador real (título > varchar(200)),
            # pero inyectado desde el doble: así el test mide la CLASE de
            # fallo aunque el recorte del título cierre el disparador de la
            # API. Se parchea el MODELO, que es lo que ambas versiones del
            # código importan en tiempo de llamada.
            if kw.get("data", {}).get("search_name") == "rota":
                kw["title"] = "X" * 250
            return Notification(**kw)

        fake = _FakeRedis()
        with (
            patch("redis.from_url", lambda *a, **k: fake),
            patch("database.task_session", new=lambda: _session_of(db_session)),
            patch("models.notification.Notification", _notificacion_venenosa),
        ):
            # Sin el fix esto LANZA PendingRollbackError desde dentro del
            # `except` (el logger toca atributos expirados antes del rollback)
            # y la búsqueda sana no se ejecuta jamás.
            result = await _run_saved_searches_async()

        assert result["failed"] == 1
        assert result["searches_processed"] == 1, (
            "la búsqueda sana quedó detrás de la rota y no se ejecutó"
        )
        titles = list((await db_session.execute(select(Notification.title))).scalars())
        assert titles == ["New matches for 'sana'"]

    async def test_el_titulo_se_recorta_en_vez_de_reventar(self, db_session):
        """El recorte es lo que cierra el disparador: la búsqueda de nombre
        largo debe NOTIFICAR, no fallar."""
        from tasks.search_tasks import _execute_single_search

        uid = await _make_user(db_session)
        search = await _make_search(db_session, uid, name="L" * 200)
        await _make_job(db_session, "g4ss-trim-1", title="Oferta nueva")

        fake = _FakeRedis()
        with patch("redis.from_url", lambda *a, **k: fake):
            count = await _execute_single_search(db_session, search, settings)

        assert count == 1
        title = (await db_session.execute(select(Notification.title))).scalar_one()
        assert len(title) <= 200
        # El nombre completo sobrevive en el payload, que es texto libre.
        data = (await db_session.execute(select(Notification.data))).scalar_one()
        assert data["search_name"] == "L" * 200


class TestP23FiltrosInertes:
    async def _run(self, db, uid, filters) -> int:
        from tasks.search_tasks import _execute_single_search

        search = await _make_search(db, uid, name=uuid.uuid4().hex[:8], filters=filters)
        fake = _FakeRedis()
        with patch("redis.from_url", lambda *a, **k: fake):
            return await _execute_single_search(db, search, settings)

    async def test_q_filtra_de_verdad(self, db_session):
        uid = await _make_user(db_session)
        await _make_job(db_session, "g4ss-q-1", title="Python developer")
        await _make_job(db_session, "g4ss-q-2", title="Concierge de escuela")

        assert await self._run(db_session, uid, {"q": "Python"}) == 1, (
            "`q` es un campo INERTE: el usuario guarda «Python» y recibe el "
            "corpus entero como novedades"
        )

    async def test_seniority_y_contract_type_filtran(self, db_session):
        uid = await _make_user(db_session)
        await _make_job(db_session, "g4ss-sen-1", title="A", seniority="senior")
        await _make_job(db_session, "g4ss-sen-2", title="B", seniority="junior")

        assert await self._run(db_session, uid, {"seniority": "senior"}) == 1
        assert await self._run(db_session, uid, {"contract_type": "internship"}) == 0

    async def test_el_rango_salarial_filtra_por_solape(self, db_session):
        uid = await _make_user(db_session)
        await _make_job(
            db_session,
            "g4ss-sal-1",
            title="A",
            salary_min_chf=90000,
            salary_max_chf=110000,
        )
        await _make_job(
            db_session,
            "g4ss-sal-2",
            title="B",
            salary_min_chf=40000,
            salary_max_chf=50000,
        )

        assert await self._run(db_session, uid, {"salary_min": 80000}) == 1
        assert await self._run(db_session, uid, {"salary_max": 60000}) == 1

    async def test_min_score_sigue_inerte_a_proposito(self, db_session):
        """G3/P3-4: documentado como inerte. No se toca."""
        uid = await _make_user(db_session)
        await _make_job(db_session, "g4ss-ms-1", title="Oferta nueva")
        assert await self._run(db_session, uid, {}) == 1


class TestP25MarcadoresHuerfanos:
    async def test_un_fallo_tras_marcar_retira_los_marcadores(self, db_session):
        """Si la corrida revienta después de `filter_unsent`, las ofertas NO
        pueden quedarse marcadas como «ya notificadas» 14 días."""
        from tasks.search_tasks import _SENT_PREFIX, _execute_single_search

        uid = await _make_user(db_session)
        search = await _make_search(db_session, uid, name="marcadores")
        await _make_job(db_session, "g4ss-mark-1", title="Oferta nueva")
        await _make_job(db_session, "g4ss-mark-2", title="Otra oferta")

        fake = _FakeRedis()
        boom = RuntimeError("commit caído")
        with (
            patch("redis.from_url", lambda *a, **k: fake),
            patch(
                "tasks.search_tasks._notify_and_commit",
                side_effect=boom,
            ),
        ):
            with pytest.raises(RuntimeError):
                await _execute_single_search(db_session, search, settings)
        await db_session.rollback()

        vivos = [k for k in fake.store if k.startswith(f"{_SENT_PREFIX}:")]
        assert vivos == [], (
            "quedan marcadores «ya notificada» sin notificación detrás: la "
            "corrida siguiente devolverá 0 novedades"
        )

        # Y la corrida siguiente sí las ve (el rollback expiró `search`).
        await db_session.refresh(search)
        with patch("redis.from_url", lambda *a, **k: fake):
            assert await _execute_single_search(db_session, search, settings) == 2
