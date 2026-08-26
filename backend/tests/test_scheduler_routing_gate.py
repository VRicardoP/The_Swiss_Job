"""Tests del gate anti-doble-motor D.1 en los schedulers legacy (plan §15bis).

Mientras dura la migracion canary perfil a perfil, los dos motores conviven:
los schedulers legacy consultan `jobhunt_routing` y OMITEN a los perfiles cuyo
matching ya es del core (core_read/core_primary/rollback_pending), actuando
solo en local/shadow. Cubre:

- run_all_matches: omite migrados SIN invocar el servicio de matching;
- digest diario: el filtro va EN SQL — ni una fila materializada del migrado;
- pipeline: la etapa de matching se omite entera si no queda ningun legacy;
- alerta de docencia: NO se gatea (el core no tiene esa capacidad — Fase E).
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, update

from config import settings
from models.job import Job
from models.match_result import MatchResult
from models.notification import Notification
from models.user import User
from models.user_profile import UserProfile
from services import routing

# Embedding minimo valido para pasar el filtro cv_embedding IS NOT NULL.
_EMBEDDING = [0.0] * 384

# Modos en los que el core es autoritativo => el legacy debe omitir.
_CORE_OWNED = (
    routing.MODE_CORE_READ,
    routing.MODE_CORE_PRIMARY,
    routing.MODE_ROLLBACK_PENDING,
)


@pytest.fixture(autouse=True)
def _fresh_routing_cache():
    routing.invalidate_routing_cache()
    yield
    routing.invalidate_routing_cache()


def _session_factory(db_session):
    """task_session de test: entrega la sesion del fixture."""

    @asynccontextmanager
    async def factory():
        yield db_session

    return factory


def _email(uid: uuid.UUID) -> str:
    return f"d1-{uid.hex[:8]}@example.com"


async def _create_user_with_profile(db) -> uuid.UUID:
    """Usuario activo + perfil con embedding (candidato al matching diario)."""
    uid = uuid.uuid4()
    db.add(User(id=uid, email=_email(uid), hashed_password="x", gdpr_consent=True))
    await db.flush()
    db.add(UserProfile(user_id=uid, cv_embedding=_EMBEDDING, skills=[]))
    await db.commit()
    return uid


async def _seed_match(
    db, uid: uuid.UUID, job_hash: str, source: str = "test", score: float = 95.0
) -> None:
    """Job activo + match por encima del umbral del digest para `uid`."""
    db.add(
        Job(
            hash=job_hash,
            source=source,
            title=f"Job {job_hash}",
            company="C",
            url=f"https://x.ch/{job_hash}",
            is_active=True,
        )
    )
    await db.flush()
    db.add(
        MatchResult(
            user_id=uid,
            job_hash=job_hash,
            score_embedding=0.9,
            score_salary=0.9,
            score_location=0.9,
            score_recency=0.9,
            score_llm=0.0,
            score_final=score,
            matching_skills=[],
            missing_skills=[],
        )
    )
    await db.commit()


def _fake_redis():
    """Redis de test: sin marca de agua previa (get -> None)."""
    r = MagicMock()
    r.get.return_value = None
    return r


# ---------------------------------------------------------------------------
# run_all_matches: omite migrados sin trabajo caro
# ---------------------------------------------------------------------------


async def test_run_all_matches_skips_core_owned_and_processes_legacy(db_session):
    """Procesa local/shadow; omite core_read/core_primary/rollback_pending sin
    invocar el servicio de matching (cero coste LLM para el migrado)."""
    from tasks.matching_tasks import _run_all_matches_async

    legacy_local = await _create_user_with_profile(db_session)
    legacy_shadow = await _create_user_with_profile(db_session)
    await routing.set_routing(
        db_session,
        routing.CAPABILITY_MATCHING,
        routing.MODE_SHADOW,
        profile_id=legacy_shadow,
    )
    for mode in _CORE_OWNED:
        migrated = await _create_user_with_profile(db_session)
        await routing.set_routing(
            db_session, routing.CAPABILITY_MATCHING, mode, profile_id=migrated
        )

    fake_service = AsyncMock()
    fake_service.run_matching = AsyncMock(
        return_value={"status": "success", "results_count": 1}
    )

    with (
        patch("database.task_session", _session_factory(db_session)),
        patch("services.groq_service.GroqService"),
        patch("services.gemini_service.GeminiService"),
        patch("services.match_service.MatchService", return_value=fake_service),
    ):
        summary = await _run_all_matches_async()

    assert summary["skipped_routing"] == len(_CORE_OWNED)
    assert summary["profiles"] == 2
    assert summary["errors"] == 0
    processed = {c.args[0] for c in fake_service.run_matching.await_args_list}
    assert processed == {legacy_local, legacy_shadow}


# ---------------------------------------------------------------------------
# Digest diario: filtro EN SQL — el migrado no recibe correo legacy
# ---------------------------------------------------------------------------


async def test_digest_emails_legacy_user_but_not_migrated(db_session, monkeypatch):
    from tasks.digest_tasks import _send_daily_digest_async

    legacy = await _create_user_with_profile(db_session)
    migrated = await _create_user_with_profile(db_session)
    await routing.set_routing(
        db_session,
        routing.CAPABILITY_MATCHING,
        routing.MODE_CORE_PRIMARY,
        profile_id=migrated,
    )
    await _seed_match(db_session, legacy, "d1-legacy")
    await _seed_match(db_session, migrated, "d1-migrated")

    monkeypatch.setattr(settings, "DAILY_DIGEST_ENABLED", True)
    fake_email = MagicMock()
    fake_email.is_available = True

    with (
        patch("database.task_session", _session_factory(db_session)),
        patch("services.email_service.EmailService", return_value=fake_email),
        patch("redis.from_url", return_value=_fake_redis()),
    ):
        out = await _send_daily_digest_async()

    assert out["users_notified"] == 1
    # El filtrado es EN SQL: la fila del migrado ni siquiera se materializa.
    assert out["candidates"] == 1
    recipients = [c.args[0] for c in fake_email.send.call_args_list]
    assert recipients == [_email(legacy)]


async def test_digest_wildcard_migration_beaten_by_exact_local_row(
    db_session, monkeypatch
):
    """La precedencia exacta > comodin tambien aplica en el filtro SQL: con el
    comodin migrado, un perfil con fila exacta 'local' sigue recibiendo digest."""
    from tasks.digest_tasks import _send_daily_digest_async

    pinned_local = await _create_user_with_profile(db_session)
    migrated = await _create_user_with_profile(db_session)
    await routing.set_routing(
        db_session, routing.CAPABILITY_MATCHING, routing.MODE_CORE_PRIMARY
    )  # comodin: consumer migrado
    await routing.set_routing(
        db_session,
        routing.CAPABILITY_MATCHING,
        routing.MODE_LOCAL,
        profile_id=pinned_local,
    )
    await _seed_match(db_session, pinned_local, "d1-pinned")
    await _seed_match(db_session, migrated, "d1-wildcard")

    monkeypatch.setattr(settings, "DAILY_DIGEST_ENABLED", True)
    fake_email = MagicMock()
    fake_email.is_available = True

    with (
        patch("database.task_session", _session_factory(db_session)),
        patch("services.email_service.EmailService", return_value=fake_email),
        patch("redis.from_url", return_value=_fake_redis()),
    ):
        out = await _send_daily_digest_async()

    assert out["users_notified"] == 1
    recipients = [c.args[0] for c in fake_email.send.call_args_list]
    assert recipients == [_email(pinned_local)]


# ---------------------------------------------------------------------------
# Pipeline: la etapa de matching se omite entera sin perfiles legacy
# ---------------------------------------------------------------------------


async def test_pipeline_probe_true_while_any_legacy_profile(db_session):
    from tasks.pipeline_tasks import _any_legacy_matching_profile_async

    uid = await _create_user_with_profile(db_session)
    with patch("database.task_session", _session_factory(db_session)):
        assert await _any_legacy_matching_profile_async() is True  # default local
        await routing.set_routing(
            db_session,
            routing.CAPABILITY_MATCHING,
            routing.MODE_SHADOW,
            profile_id=uid,
        )
        assert await _any_legacy_matching_profile_async() is True  # shadow es legacy
        await routing.set_routing(
            db_session,
            routing.CAPABILITY_MATCHING,
            routing.MODE_CORE_PRIMARY,
            profile_id=uid,
        )
        assert await _any_legacy_matching_profile_async() is False  # ultimo migrado


def _capture_chain(captured):
    """Doble de celery.chain: registra los nombres de las etapas encadenadas."""

    def fake_chain(*sigs):
        captured["stages"] = [s.task for s in sigs]
        workflow = MagicMock()
        workflow.apply_async.return_value = MagicMock(id="test-chain")
        return workflow

    return fake_chain


def test_daily_harvest_omits_matching_stage_when_no_legacy_left():
    from tasks import pipeline_tasks

    captured: dict = {}
    with (
        patch("tasks.pipeline_tasks.chain", side_effect=_capture_chain(captured)),
        patch("tasks.pipeline_tasks._matching_stage_enabled", return_value=False),
    ):
        out = pipeline_tasks.daily_harvest.apply().result

    assert out["status"] == "dispatched"
    # La cosecha (global) se mantiene entera; SOLO cae la etapa de matching.
    assert captured["stages"] == [
        "tasks.fetch_providers",
        "tasks.scraping.fetch_scrapers",
        "tasks.ai.embed_all_pending",
        "tasks.dedup_semantic_batch",
    ]


def test_daily_harvest_keeps_matching_stage_with_legacy_profiles():
    from tasks import pipeline_tasks

    captured: dict = {}
    with (
        patch("tasks.pipeline_tasks.chain", side_effect=_capture_chain(captured)),
        patch("tasks.pipeline_tasks._matching_stage_enabled", return_value=True),
    ):
        out = pipeline_tasks.daily_harvest.apply().result

    assert out["status"] == "dispatched"
    assert captured["stages"][-1] == "tasks.matching.run_all_matches"


# ---------------------------------------------------------------------------
# Digest de la watchlist: mismo origen (match_results) => mismo gate
# ---------------------------------------------------------------------------


async def test_watchlist_digest_skips_migrated_user(db_session, monkeypatch):
    """El digest de la watchlist se construye desde `match_results` LEGACY igual
    que el diario: para un perfil migrado esos matches ya no se actualizan, asi
    que enviarlo seria correo con recomendaciones viejas para siempre."""
    from tasks.watchlist_tasks import _send_digest_async

    legacy = await _create_user_with_profile(db_session)
    migrated = await _create_user_with_profile(db_session)
    # La PK de UserProfile es `id`, no `user_id`: hay que buscar por la columna.
    await db_session.execute(
        update(UserProfile)
        .where(UserProfile.user_id.in_([legacy, migrated]))
        .values(watchlist_schools_enabled=True)
    )
    await db_session.commit()
    await routing.set_routing(
        db_session,
        routing.CAPABILITY_MATCHING,
        routing.MODE_CORE_PRIMARY,
        profile_id=migrated,
    )
    # El digest SOLO mira ofertas de fuentes de la watchlist y con score en la
    # banda [MIN_SCORE, PUSH_THRESHOLD): sin esto el test pasaria sin gate.
    from tasks.watchlist_tasks import _get_watchlist_sources

    fuente = _get_watchlist_sources()[0]
    banda = (
        settings.WATCHLIST_DIGEST_MIN_SCORE + settings.WATCHLIST_PUSH_THRESHOLD
    ) / 2
    await _seed_match(db_session, legacy, "d1-wl-legacy", source=fuente, score=banda)
    await _seed_match(
        db_session, migrated, "d1-wl-migrated", source=fuente, score=banda
    )

    with patch("database.task_session", _session_factory(db_session)):
        out = await _send_digest_async()

    # El legacy SI recibe (prueba que el escenario es real) y el migrado NO.
    assert out["users_notified"] == 1
    notified = [
        row.user_id
        for row in (await db_session.execute(select(Notification.user_id))).all()
    ]
    assert notified == [legacy]


# ---------------------------------------------------------------------------
# Alerta de docencia: NO se gatea (decision D.1 — el core no la cubre)
# ---------------------------------------------------------------------------


async def test_teacher_alert_fires_even_with_whole_consumer_migrated(
    db_session, monkeypatch
):
    """Reverso del invariante: se gatea lo que el core puede duplicar. El core
    no tiene la capacidad de colegios/docencia (Fase E): aunque TODO el
    consumer este migrado (comodin core_primary), la alerta sigue saliendo."""
    from tasks.alert_tasks import _detect_and_notify

    await routing.set_routing(
        db_session, routing.CAPABILITY_MATCHING, routing.MODE_CORE_PRIMARY
    )
    await routing.set_routing(
        db_session, routing.CAPABILITY_SCHOOLS, routing.MODE_CORE_PRIMARY
    )
    db_session.add(
        Job(
            hash="d1-teacher",
            source="test",
            title="Primarlehrperson 80%",
            company="Schule Test",
            url="https://x.ch/d1-teacher",
            is_active=True,
            category="H",
        )
    )
    await db_session.commit()

    monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
    monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "teacher@example.com")
    fake_email = MagicMock()
    fake_email.is_available = True

    with (
        patch("database.task_session", _session_factory(db_session)),
        patch("services.email_service.EmailService", return_value=fake_email),
        patch("redis.from_url", return_value=_fake_redis()),
    ):
        out = await _detect_and_notify()

    # G3/P1-1: la tarea añade `already_sent` (ofertas que el solape de la
    # ventana vuelve a ver y el marcador ya avisó). Se comprueban las claves
    # que fija este contrato, no la forma exacta del dict.
    assert out["status"] == "success"
    assert out["candidates"] == 1
    assert out["matched"] == 1
    fake_email.send.assert_called_once()
    assert fake_email.send.call_args.args[0] == "teacher@example.com"
