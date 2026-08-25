"""Regresión de la auditoría G1 — P1-4: saved searches con alertas basura.

Dos bugs compuestos en `_execute_single_search`:
(a) «nuevo» se medía con `last_seen_at`, que el upsert refresca cada día para
    toda oferta aún listada → una búsqueda amplia notificaba TODO el corpus
    activo cada run («Found 800 new jobs» sin ningún alta real);
(b) el umbral de PUNTUACIÓN `min_score` se comparaba contra el CONTEO de
    ofertas → una búsqueda estrecha (<50 resultados) no notificaba jamás.

El fix mide novedad con `first_seen_at` y notifica cuando hay altas reales
(en esta query no se calcula ningún score — el umbral de score no aplica).
"""

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


async def _make_user(db):
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"u-{uid.hex[:8]}@example.com",
            hashed_password=hash_password("TestPass1!"),
            gdpr_consent=True,
        )
    )
    await db.commit()
    return uid


async def _make_job(db, hash_, *, canton="ZH", first_seen_days_ago=0):
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
    if first_seen_days_ago:
        # Oferta VIEJA re-vista: alta antigua, last_seen_at refrescado hoy
        # por el upsert (el mecanismo anti-archivado del pipeline).
        await db.execute(
            update(Job)
            .where(Job.hash == hash_)
            .values(
                first_seen_at=text(f"NOW() - INTERVAL '{first_seen_days_ago} days'"),
                last_seen_at=text("NOW()"),
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_p1_4_solo_altas_reales_y_notifica_sin_umbral_de_score(db_session):
    uid = await _make_user(db_session)
    marker = uuid.uuid4().hex[:8]
    canton = "GL"  # cantón poco usado para no chocar con datos de otros tests

    # Una oferta re-vista (alta hace 30 días) y una genuinamente nueva.
    await _make_job(
        db_session, f"g1p14-old-{marker}", canton=canton, first_seen_days_ago=30
    )
    await _make_job(db_session, f"g1p14-new-{marker}", canton=canton)

    search = SavedSearch(
        user_id=uid,
        name=f"g1p14-{marker}",
        filters={"canton": canton},
        min_score=50,  # umbral de SCORE del usuario: no debe filtrar el CONTEO
        notify_frequency=NotifyFrequency.daily,
        notify_push=False,
        is_active=True,
        last_run_at=datetime.now(timezone.utc) - timedelta(days=2),
        total_matches=0,
    )
    db_session.add(search)
    await db_session.commit()

    count = await _execute_single_search(db_session, search, settings)

    # (a) Solo la oferta NUEVA cuenta — con last_seen_at contaban las dos.
    assert count == 1

    # (b) 1 < min_score(50), pero hay un alta real → DEBE notificar.
    notifs = (
        (
            await db_session.execute(
                select(Notification).where(Notification.user_id == uid)
            )
        )
        .scalars()
        .all()
    )
    assert len(notifs) == 1
    assert notifs[0].data["match_count"] == 1


@pytest.mark.asyncio
async def test_p1_4_sin_altas_no_notifica(db_session):
    """Día sin altas: re-vistas refrescadas NO deben disparar notificación."""
    uid = await _make_user(db_session)
    marker = uuid.uuid4().hex[:8]
    canton = "UR"

    await _make_job(
        db_session, f"g1p14b-{marker}", canton=canton, first_seen_days_ago=30
    )

    search = SavedSearch(
        user_id=uid,
        name=f"g1p14b-{marker}",
        filters={"canton": canton},
        min_score=0,
        notify_frequency=NotifyFrequency.daily,
        notify_push=False,
        is_active=True,
        last_run_at=datetime.now(timezone.utc) - timedelta(days=2),
        total_matches=0,
    )
    db_session.add(search)
    await db_session.commit()

    count = await _execute_single_search(db_session, search, settings)

    assert count == 0
    notifs = (
        (
            await db_session.execute(
                select(Notification).where(Notification.user_id == uid)
            )
        )
        .scalars()
        .all()
    )
    assert notifs == []
