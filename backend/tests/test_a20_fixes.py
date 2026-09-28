"""Regresiones del análisis profundo de código 2026-09-28 (DEUDA_TECNICA §0.C).

Cada prueba fija UN hallazgo A20-* por su síntoma observable, no por su
implementación: si alguien deshace el fix, la prueba vuelve a rojo.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import event, select

from models.job import Job
from models.match_result import MatchResult
from schemas.applications import ApplicationUpdate
from services import routing
from services import scheduler as sched_mod
from services.catalog.local import LocalCatalog
from services.job_repository import JobRepository
from services.match_service import MatchService
from services.matching.identity import set_profile_link
from services.sse_manager import SSEManager
from tasks import maintenance_tasks
from tests.conftest import test_engine
from tests.test_applications_contract import (
    JOB_HASH,
    FakeCoreV1,
    make_core,
    seed_job,
    seed_user,
)
from utils.redact import redact_credentials

# ----------------------------------------------------------------- A20-01


class _PubSubQueSeCae:
    """Primera suscripción: lanza tras conectar. Segunda: entrega un mensaje
    y se queda escuchando (como un pub/sub sano)."""

    intentos = 0

    def __init__(self, user_id):
        self._user_id = user_id

    async def psubscribe(self, _pattern):
        type(self).intentos += 1

    async def punsubscribe(self):
        pass

    async def aclose(self):
        pass

    async def listen(self):
        if type(self).intentos == 1:
            raise ConnectionError("redis reiniciado")
        yield {
            "type": "pmessage",
            "channel": f"sse:{self._user_id}",
            "data": '{"event": "new_matches", "data": {"n": 1}}',
        }
        await asyncio.Event().wait()


async def test_a20_01_el_oyente_sse_sobrevive_a_un_fallo_de_redis():
    user_id = uuid.uuid4()
    _PubSubQueSeCae.intentos = 0
    redis = SimpleNamespace(pubsub=lambda: _PubSubQueSeCae(user_id))
    manager = SSEManager(redis)
    manager.RECONNECT_INITIAL_S = 0.0
    manager.RECONNECT_MAX_S = 0.0

    await manager.start()
    queue = await manager.subscribe(user_id)
    try:
        mensaje = await asyncio.wait_for(queue.get(), timeout=2.0)
    finally:
        await manager.stop()

    assert mensaje["event"] == "new_matches"
    assert _PubSubQueSeCae.intentos == 2, "debe haberse re-suscrito tras el fallo"
    assert manager._listener_task.done()


# ----------------------------------------------------------------- A20-16


async def test_a20_16_si_el_scheduler_no_arranca_se_suelta_el_cerrojo(monkeypatch):
    fake_scheduler = MagicMock(running=False)
    fake_scheduler.start.side_effect = RuntimeError("apscheduler roto")
    monkeypatch.setattr(sched_mod, "setup_schedules", MagicMock())
    monkeypatch.setattr(sched_mod, "scheduler", fake_scheduler)
    r = MagicMock()
    r.set = AsyncMock(return_value=True)
    r.delete = AsyncMock()

    with pytest.raises(RuntimeError):
        await sched_mod._leader_step(r, is_leader=False)

    r.delete.assert_awaited_once_with(sched_mod._LEADER_KEY)


# ----------------------------------------------------------------- A20-15


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("redis://:R3d1sP4ss@redis:6379/0", "redis://:<redacted>@redis:6379/0"),
        ("https://user:pw-secreta@proxy:3128", "https://user:<redacted>@proxy:3128"),
        # Sin `@` no hay userinfo: un puerto no es un secreto.
        ("https://host:8080/path", "https://host:8080/path"),
    ],
)
def test_a20_15_userinfo_con_usuario_vacio(texto, esperado):
    assert redact_credentials(texto) == esperado


# ----------------------------------------------------------------- A20-17


def test_a20_17_la_cache_de_routing_esta_acotada():
    routing._cache.clear()
    try:
        tope = routing._CACHE_MAX_ENTRIES
        # Todas caducadas: la poda las retira y deja sitio.
        for i in range(tope):
            routing._cache[("c", uuid.uuid4(), str(i))] = ("local", -1.0)
        routing._prune_cache(now=0.0)
        assert routing._cache == {}
        # Todas vivas: no cabe podar; se vacía entera (nunca crece sin cota).
        for i in range(tope):
            routing._cache[("c", uuid.uuid4(), str(i))] = ("local", 10.0)
        routing._prune_cache(now=0.0)
        assert len(routing._cache) < tope
    finally:
        routing._cache.clear()


# ----------------------------------------------------------------- A20-10


def test_a20_10_check_job_urls_lanza_en_vez_de_devolver_error(monkeypatch):
    async def revienta(_limit):
        raise RuntimeError("bd caída")

    monkeypatch.setattr(maintenance_tasks, "_check_job_urls_async", revienta)
    with pytest.raises(Exception):
        maintenance_tasks.check_job_urls(limit=1)


def test_a20_10_cleanup_stale_jobs_lanza_en_vez_de_devolver_error(monkeypatch):
    async def revienta(_days):
        raise RuntimeError("bd caída")

    monkeypatch.setattr(maintenance_tasks, "_cleanup_stale_jobs_async", revienta)
    with pytest.raises(Exception):
        maintenance_tasks.cleanup_stale_jobs(max_age_days=1)


# ----------------------------------------------------------------- A20-11


def _oferta(hash_: str) -> dict:
    return {
        "hash": hash_,
        "source": "arbeitnow",
        "title": "Entwickler",
        "company": "Empresa",
        "url": f"https://ejemplo.test/{hash_}",
        "description": "Descripción",
        "tags": ["python"],
    }


async def test_a20_11_upsert_devuelve_is_new_sin_select_previo(db_session):
    repo = JobRepository(db_session)
    h = uuid.uuid4().hex
    sentencias: list[str] = []

    def contar(_conn, _cursor, statement, *_args):
        sentencias.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", contar)
    try:
        primera = await repo.upsert_job(_oferta(h))
        segunda = await repo.upsert_job(_oferta(h))
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", contar)
    await db_session.commit()

    assert primera is True and segunda is False
    selects = [s for s in sentencias if s.lstrip().upper().startswith("SELECT")]
    assert selects == [], "el upsert ya no consulta la fila antes de escribirla"


# ----------------------------------------------------------------- A20-06


async def test_a20_06_los_resultados_nuevos_se_insertan_por_lotes(db_session):
    user_id = await seed_user(db_session)
    hashes = [uuid.uuid4().hex for _ in range(60)]
    for h in hashes:
        await seed_job(db_session, h)
    await db_session.commit()

    def fila(h, con_llm):
        base = {
            "user_id": user_id,
            "job_hash": h,
            "score_embedding": 0.5,
            "score_salary": 0.5,
            "score_location": 0.5,
            "score_recency": 0.5,
            "score_final": 50.0,
            "urgency_score": 0,
            "matching_skills": ["python"],
            "missing_skills": [],
        }
        if con_llm:
            base["score_llm"] = 0.7
            base["explanation"] = "encaja"
        return base

    rows = [fila(h, i % 2 == 0) for i, h in enumerate(hashes)]
    inserts: list[str] = []

    def contar(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("INSERT INTO MATCH_RESULTS"):
            inserts.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", contar)
    try:
        await MatchService(db_session)._insert_new_rows(rows)
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", contar)
    await db_session.commit()

    # Dos conjuntos de columnas (con y sin bloque LLM) ⇒ dos sentencias, no 60.
    assert len(inserts) == 2, len(inserts)
    guardadas = (
        await db_session.execute(
            select(MatchResult.job_hash, MatchResult.score_llm).where(
                MatchResult.user_id == user_id
            )
        )
    ).all()
    assert len(guardadas) == 60
    assert sum(1 for _h, llm in guardadas if llm == 0.7) == 30


# ----------------------------------------------------------------- A20-12


async def test_a20_12_stats_en_una_consulta_da_los_mismos_agregados(db_session):
    from models.enums import ContractType, Seniority

    filas = [
        ("arbeitnow", "ZH", "de", Seniority.senior, ContractType.full_time, 100, 120),
        ("arbeitnow", "ZH", "en", Seniority.senior, None, None, None),
        ("thehub", None, "en", None, ContractType.full_time, 80, 90),
        ("thehub", "BE", None, Seniority.junior, ContractType.contract, None, 70),
    ]
    for i, (src, canton, lang, sen, con, smin, smax) in enumerate(filas):
        h = uuid.uuid4().hex
        db_session.add(
            Job(
                hash=h,
                source=src,
                title=f"T{i}",
                company="C",
                url=f"https://ejemplo.test/{h}",
                canton=canton,
                language=lang,
                seniority=sen,
                contract_type=con,
                salary_min_chf=smin,
                salary_max_chf=smax,
            )
        )
    # Inactiva y duplicada: fuera de todos los agregados.
    h = uuid.uuid4().hex
    db_session.add(
        Job(
            hash=h,
            source="zebis",
            title="X",
            company="C",
            url=f"https://e.test/{h}",
            is_active=False,
        )
    )
    await db_session.commit()

    sentencias: list[str] = []

    def contar(_conn, _cursor, statement, *_args):
        sentencias.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", contar)
    try:
        stats = await LocalCatalog(db_session).stats()
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", contar)

    assert len(sentencias) == 1
    assert stats.total_jobs == 4
    assert stats.by_source == {"arbeitnow": 2, "thehub": 2}
    assert stats.by_canton == {"ZH": 2, "BE": 1}
    assert stats.by_language == {"de": 1, "en": 2}
    assert stats.by_seniority == {"senior": 2, "junior": 1}
    assert stats.by_contract == {"full_time": 2, "contract": 1}
    assert stats.salary_stats.min == 80
    assert stats.salary_stats.max == 120
    assert stats.salary_stats.mean == round((120 + 90 + 70) / 3, 2)


# ----------------------------------------------------------------- A20-13


class _FakeQueRecuerdaUrls(FakeCoreV1):
    def __init__(self, profile_id):
        super().__init__(profile_id)
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(f"{request.method} {request.url}")
        return super().handler(request)


async def test_a20_13_las_escrituras_viajan_acotadas_al_perfil(db_session):
    user_id = await seed_user(db_session)
    await seed_job(db_session, JOB_HASH)
    await db_session.commit()
    profile_id = uuid.uuid4()
    await set_profile_link(db_session, user_id, profile_id, updated_by="tests")
    fake = _FakeQueRecuerdaUrls(profile_id)
    core = make_core(db_session, httpx.MockTransport(fake.handler))
    item = fake.add_item(kind="application", status="applied", notes="n")
    app_id = uuid.UUID(item["id"])

    assert await core.update(user_id, app_id, ApplicationUpdate(notes="x")) is not None
    assert await core.delete(user_id, app_id) is True

    escrituras = [u for u in fake.urls if u.startswith(("PATCH", "DELETE"))]
    assert len(escrituras) == 2
    assert all(f"profile={profile_id}" in u for u in escrituras)
    # Y ya no se drena el feed para comprobar la propiedad.
    assert not any(u.startswith("GET") for u in fake.urls)


# ------------------------------------------------- A20-09 y A20-05 (con sesión)


async def _registrar(client) -> tuple[dict, uuid.UUID]:
    correo = f"a20-{uuid.uuid4().hex[:8]}@example.com"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": correo, "password": "TestPass123!", "gdpr_consent": True},
    )
    assert r.status_code == 201, r.text
    return (
        {"Authorization": f"Bearer {r.json()['access_token']}"},
        uuid.UUID(r.json()["user"]["id"]),
    )


async def test_a20_05_el_perfil_publica_los_pesos_por_defecto_del_motor(client):
    from services.job_matcher import DEFAULT_WEIGHTS

    headers, _ = await _registrar(client)
    r = await client.get("/api/v1/profile", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["default_score_weights"] == DEFAULT_WEIGHTS
    assert "language" in r.json()["default_score_weights"]


@pytest.mark.parametrize("ruta", ["/api/v1/match/history", "/api/v1/match/saved"])
async def test_a20_09_history_y_saved_no_llaman_al_llm_con_translate_false(
    client, db_session, monkeypatch, ruta
):
    from services.groq_service import GroqService
    from services.translation_service import TranslationService

    headers, user_id = await _registrar(client)
    h = uuid.uuid4().hex
    db_session.add(
        Job(
            hash=h,
            source="arbeitnow",
            title="Softwareentwickler gesucht",
            company="Firma",
            url=f"https://ejemplo.test/{h}",
            language="de",
        )
    )
    db_session.add(
        MatchResult(
            user_id=user_id,
            job_hash=h,
            score_embedding=0.8,
            score_salary=0.5,
            score_location=0.5,
            score_recency=0.5,
            score_final=80.0,
            feedback="thumbs_up",
        )
    )
    await db_session.commit()

    llamadas: list[list[str]] = []

    async def translate_titles(self, titulos, *_a, **_kw):
        llamadas.append(list(titulos))
        return {}

    monkeypatch.setattr(TranslationService, "translate_titles", translate_titles)
    monkeypatch.setattr(GroqService, "is_available", property(lambda self: True))

    r = await client.get(f"{ruta}?translate=false", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert llamadas == [], "con translate=false el LLM no se toca"

    # Control negativo: por defecto SÍ se traduce (la ruta llega al servicio).
    r = await client.get(ruta, headers=headers)
    assert r.status_code == 200, r.text
    assert len(llamadas) == 1
