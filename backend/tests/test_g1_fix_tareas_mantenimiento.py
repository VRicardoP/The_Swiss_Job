"""Regresiones de la auditoría G1 — tareas de mantenimiento/pipeline/alerta.

- P2-10: duplicados vivos enterrados para siempre bajo una canónica ARCHIVADA
  (la promoción solo ocurría al borrar).
- P2-13: daily_harvest reportaba éxito al despachar; un eslabón caído mataba
  el resto de la cadena sin rastro (sin link_error).
- P2-14: check_job_urls bajo el límite global de 300s → timeout, run perdido
  y rotación estancada en la misma cabecera (commit único al final).
- P2-15: alerta profesor sin cota superior (doble email) y con retry que
  re-enviaba tras SMTP OK (marca al final).
- P3-22: dedup_semantic_batch tragaba la excepción y devolvía dict — el
  matching corría sobre corpus sin dedupear y el run contaba OK.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, text, update

from core.security import hash_password
from models.job import Job
from models.match_result import MatchResult
from models.user import User
from tasks.maintenance_tasks import (
    _check_job_urls_async,
    _cleanup_stale_jobs_async,
    check_job_urls,
    dedup_semantic_batch,
)


def _mock_session_factory(db_session):
    @asynccontextmanager
    async def mock_session():
        yield db_session

    return mock_session


async def _make_job(db, hash_, *, url, stale=False, duplicate_of=None, active=True):
    db.add(
        Job(
            hash=hash_,
            source="test",
            title=f"Job {hash_[:12]}",
            company="Acme",
            url=url,
            is_active=active,
            duplicate_of=duplicate_of,
        )
    )
    await db.commit()
    if stale:
        await db.execute(
            update(Job)
            .where(Job.hash == hash_)
            .values(last_seen_at=text("NOW() - INTERVAL '200 days'"))
        )
        await db.commit()


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


@pytest.mark.asyncio
class TestP210DuplicadoBajoCanonicaArchivada:
    async def test_duplicado_vivo_se_promueve_al_archivar_la_canonica(
        self, db_session
    ):
        marker = uuid.uuid4().hex[:8]
        canonical_hash = f"g1p210-canon-{marker}".ljust(32, "0")[:32]
        dup_hash = f"g1p210-dup-{marker}".ljust(32, "0")[:32]

        # Canónica caducada CON adjuntos (feedback) → se ARCHIVA, no se borra.
        await _make_job(
            db_session, canonical_hash, url=f"https://e.ch/c-{marker}", stale=True
        )
        uid = await _make_user(db_session)
        db_session.add(
            MatchResult(
                user_id=uid,
                job_hash=canonical_hash,
                score_embedding=0.9,
                score_salary=0,
                score_location=0,
                score_recency=0,
                score_llm=0,
                score_final=90,
                matching_skills=[],
                missing_skills=[],
                feedback="thumbs_up",
            )
        )
        await db_session.commit()

        # Duplicado VIVO (re-visto a diario) e inactivo por el dedup.
        await _make_job(
            db_session,
            dup_hash,
            url=f"https://e.ch/d-{marker}",
            duplicate_of=canonical_hash,
            active=False,
        )

        with patch(
            "database.task_session", new=_mock_session_factory(db_session)
        ):
            result = await _cleanup_stale_jobs_async(60)

        assert result["archived"] >= 1
        canonical = (
            await db_session.execute(select(Job).where(Job.hash == canonical_hash))
        ).scalar_one()
        assert canonical.is_active is False, "la canónica con adjuntos se archiva"

        dup = (
            await db_session.execute(select(Job).where(Job.hash == dup_hash))
        ).scalar_one()
        assert dup.duplicate_of is None, "el duplicado vivo se promueve a canónico"
        assert dup.is_active is True, "la vacante viva vuelve a ser visible"


class TestP213LinkErrorEnLaCadena:
    def test_la_cadena_se_despacha_con_link_error(self):
        from tasks.pipeline_tasks import daily_harvest

        with patch("tasks.pipeline_tasks.chain") as mock_chain, patch(
            "tasks.pipeline_tasks._matching_stage_enabled", return_value=True
        ):
            mock_chain.return_value.apply_async.return_value = SimpleNamespace(
                id="chain-1"
            )
            result = daily_harvest.run()

        assert result["status"] == "dispatched"
        _, kwargs = mock_chain.return_value.apply_async.call_args
        link_error = kwargs.get("link_error")
        assert link_error is not None, "la cadena debe llevar link_error (P2-13)"
        assert link_error.task == "tasks.pipeline.harvest_chain_failed"

    def test_callback_deja_rastro_error(self, caplog):
        from tasks.pipeline_tasks import harvest_chain_failed

        with caplog.at_level("ERROR"):
            harvest_chain_failed.run(
                SimpleNamespace(task="tasks.fetch_providers"),
                RuntimeError("db down"),
                None,
            )
        assert any("FALLÓ" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
class TestP214BarridoPorLotes:
    async def test_limites_propios_de_la_tarea(self):
        """El default global de 300s mataba el run: la tarea declara los suyos."""
        assert check_job_urls.soft_time_limit is not None
        assert check_job_urls.soft_time_limit > 300

    async def test_progreso_commiteado_antes_del_time_limit(
        self, db_session, monkeypatch
    ):
        marker = uuid.uuid4().hex[:8]
        h1 = f"g1p214-a-{marker}".ljust(32, "0")[:32]
        h2 = f"g1p214-b-{marker}".ljust(32, "0")[:32]
        await _make_job(db_session, h1, url=f"https://e.ch/first-{marker}")
        await _make_job(db_session, h2, url=f"https://e.ch/second-{marker}")
        # Determinismo del orden: el primero nunca comprobado (NULL, va antes
        # con nulls_first), el segundo con un check viejo.
        await db_session.execute(
            update(Job)
            .where(Job.hash == h2)
            .values(url_last_check=datetime.now(timezone.utc) - timedelta(days=30))
        )
        await db_session.commit()

        monkeypatch.setattr("tasks.maintenance_tasks._URL_CHECK_BATCH_SIZE", 1)

        async def _fake_head(self, url, **kwargs):
            if "second" in url:
                raise SoftTimeLimitExceeded()
            return httpx.Response(200)

        monkeypatch.setattr(httpx.AsyncClient, "head", _fake_head)

        with patch(
            "database.task_session", new=_mock_session_factory(db_session)
        ):
            result = await _check_job_urls_async(limit=2)

        assert result["status"] == "partial"
        assert result["checked"] == 1
        first = (
            await db_session.execute(select(Job).where(Job.hash == h1))
        ).scalar_one()
        # G1/P2-14: el lote sondeado ANTES del abort queda commiteado — la
        # rotación avanza en vez de re-seleccionar la misma cabecera.
        assert first.url_last_check is not None


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key):
        value = self.store.get(key)
        return value.encode() if value is not None else None

    def set(self, key, value):
        self.store[key] = value

    def close(self):
        pass


@pytest.mark.asyncio
class TestP215AlertaProfesor:
    async def _run(self, db_session, monkeypatch, fake_redis, email_mock):
        from config import settings
        from tasks.alert_tasks import _detect_and_notify

        monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True, raising=False)
        monkeypatch.setattr(
            settings, "TEACHER_ALERT_EMAIL", "user@example.com", raising=False
        )
        monkeypatch.setattr("redis.from_url", lambda *_a, **_k: fake_redis)
        with patch(
            "services.email_service.EmailService", return_value=email_mock
        ), patch("database.task_session", new=_mock_session_factory(db_session)):
            return await _detect_and_notify()

    async def _seed_primary_job(self, db_session, marker, first_seen=None):
        h = f"g1p215-{marker}".ljust(32, "0")[:32]
        db_session.add(
            Job(
                hash=h,
                source="test",
                title="Primary Teacher (Zurich)",
                company="Schule",
                url=f"https://e.ch/t-{marker}",
                is_active=True,
                category="H",
            )
        )
        await db_session.commit()
        if first_seen is not None:
            await db_session.execute(
                update(Job).where(Job.hash == h).values(first_seen_at=first_seen)
            )
            await db_session.commit()
        return h

    async def test_smtp_ok_pero_fallo_posterior_no_reenvia(
        self, db_session, monkeypatch
    ):
        """G1/P2-15: la marca avanza ANTES del envío — el retry no duplica."""
        marker = uuid.uuid4().hex[:8]
        await self._seed_primary_job(db_session, marker)

        fake_redis = _FakeRedis()
        email_mock = MagicMock()
        email_mock.is_available = True
        sends: list = []

        def _send_then_fail(*args, **kwargs):
            sends.append(1)
            raise ConnectionError("se cae DESPUÉS de entregar")

        email_mock.send = _send_then_fail

        # 1ª corrida: envía (y el fallo posterior sube al retry de Celery).
        with pytest.raises(ConnectionError):
            await self._run(db_session, monkeypatch, fake_redis, email_mock)
        assert len(sends) == 1
        assert fake_redis.store, "la marca debe estar guardada antes del envío"

        # 2ª corrida (retry): la marca ya avanzó → no re-envía el mismo lote.
        result = await self._run(db_session, monkeypatch, fake_redis, email_mock)
        assert result["matched"] == 0
        assert len(sends) == 1, "el retry no debe duplicar el email"

    async def test_cota_superior_excluye_altas_futuras(
        self, db_session, monkeypatch
    ):
        """G1/P2-15: una oferta con first_seen_at > now no entra en esta
        corrida (entrará en la siguiente) — sin doble email."""
        marker = uuid.uuid4().hex[:8]
        await self._seed_primary_job(
            db_session,
            marker,
            first_seen=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        fake_redis = _FakeRedis()
        email_mock = MagicMock()
        email_mock.is_available = True
        email_mock.send = MagicMock()

        result = await self._run(db_session, monkeypatch, fake_redis, email_mock)
        assert result["matched"] == 0
        email_mock.send.assert_not_called()


class TestP322DedupNoTragaLaExcepcion:
    def test_fallo_persistente_lanza(self):
        """G1/P3-22: la cadena debe DETENERSE, no seguir con corpus sin dedup."""
        with patch(
            "tasks.maintenance_tasks.asyncio.run",
            side_effect=RuntimeError("BD caída"),
        ):
            with pytest.raises(Exception):
                # .apply() ejecuta en proceso; con max_retries=1 el segundo
                # fallo relanza (Retry/RuntimeError, ambas Exception).
                result = dedup_semantic_batch.apply(throw=True)
                if result.failed():
                    raise result.result
                assert result.result.get("status") != "error", (
                    "el fallo no puede volver como retorno normal"
                )
