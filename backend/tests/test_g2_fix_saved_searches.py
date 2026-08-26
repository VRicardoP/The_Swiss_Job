"""Regresión de la auditoría G2 — P3-5: saved searches sin cota superior.

El fix G1/P1-4 cambió la medida de novedad a `Job.first_seen_at >=
search.last_run_at`, pero no puso la cota superior `<= now` que sus fixes
hermanos (alert_tasks y el digest de watchlist, mismo commit) sí añadieron.
Una oferta insertada entre la captura de `now` y la ejecución de la query
entraba en ESTE run y también en el SIGUIENTE (`>= last_run_at == now`):
notificación duplicada y `total_matches` inflado.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update

from config import settings
from core.security import hash_password
from models.enums import NotifyFrequency
from models.job import Job
from models.notification import Notification
from models.saved_search import SavedSearch
from models.user import User
from tasks.search_tasks import _execute_single_search


async def _make_user(db) -> uuid.UUID:
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"g2p35-{uid.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
        )
    )
    await db.commit()
    return uid


async def _make_job(db, hash_: str, *, canton: str, first_seen_at=None) -> None:
    db.add(
        Job(
            hash=hash_,
            source="test",
            title=f"Job {hash_}",
            company="Acme",
            url=f"https://example.com/{hash_}",
            canton=canton,
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


async def _make_search(db, uid: uuid.UUID, canton: str, marker: str) -> SavedSearch:
    search = SavedSearch(
        user_id=uid,
        name=f"g2p35-{marker}",
        filters={"canton": canton},
        min_score=50,
        notify_frequency=NotifyFrequency.daily,
        notify_push=False,
        is_active=True,
        last_run_at=datetime.now(timezone.utc) - timedelta(days=2),
        total_matches=0,
    )
    db.add(search)
    await db.commit()
    return search


@pytest.mark.asyncio
async def test_p35_la_oferta_del_borde_no_se_notifica_dos_veces(db_session):
    """Alta posterior a la captura de `now`: cuenta en UN run, no en dos."""
    uid = await _make_user(db_session)
    marker = uuid.uuid4().hex[:8]
    canton = "AI"  # cantón poco usado: no choca con datos de otros tests
    search = await _make_search(db_session, uid, canton, marker)

    # Oferta dada de alta DESPUÉS de la marca de agua que fijará el primer
    # run: simula la fila insertada entre la captura de `now` y la query.
    futuro = datetime.now(timezone.utc) + timedelta(seconds=0.5)
    await _make_job(db_session, f"g2p35-{marker}", canton=canton, first_seen_at=futuro)

    run1 = await _execute_single_search(db_session, search, settings)
    # El reloj alcanza al alta: el run siguiente sí debe verla (no se pierde).
    await asyncio.sleep(1.0)
    run2 = await _execute_single_search(db_session, search, settings)

    notifs = (
        (
            await db_session.execute(
                select(Notification).where(Notification.user_id == uid)
            )
        )
        .scalars()
        .all()
    )
    assert (run1, run2) != (1, 1), "la misma oferta se contó en los dos runs"
    assert run1 + run2 == 1, "el alta del borde debe contarse exactamente una vez"
    assert len(notifs) == 1
    assert search.total_matches == 1


@pytest.mark.asyncio
async def test_p35_el_alta_normal_se_sigue_notificando_una_vez(db_session):
    """No-regresión: el alta ordinaria cuenta en su run y no repite."""
    uid = await _make_user(db_session)
    marker = uuid.uuid4().hex[:8]
    canton = "AR"
    search = await _make_search(db_session, uid, canton, marker)
    await _make_job(db_session, f"g2p35-ok-{marker}", canton=canton)

    run1 = await _execute_single_search(db_session, search, settings)
    run2 = await _execute_single_search(db_session, search, settings)

    assert run1 == 1
    assert run2 == 0
    assert search.total_matches == 1
