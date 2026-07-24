"""ENSAYO DEL GATE A (CONTRATOS §4): la vertical mínima extremo a extremo.

Criterios de la puerta, cada uno con su assert explícito:
  1. A.MIN E2E: cosecha (run_all real con HTTP mockeado) → raw → identidad →
     canónica → embedding → evaluación → feed → entrega.
  2. Ingesta por scopes cubre tech + no-tech (2 scopes con keyword).
  3. Eval con descartes ESTABLES (dismiss sobrevive a re-evaluación).
  4. Re-enlace no colapsa ni CORROMPE identidad (attach comparte, reciclado
     abre vacante nueva y el primary queda reasignado a una activa).
  5. Feed filtra ACTIVA + TENANT (archivada fuera; cross-tenant 404).
  6. Entrega at-least-once idempotente (re-entrega → inbox deduplica).
  7. Core aislado (broker redis-core, colas core.*).
Ejecutar vía core-migrate.
"""

import asyncio
import json
import os
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobhunt_core.harvest.providers  # noqa: F401 — registra extractor/normalizador
from jobhunt_core import credentials, delivery, embeddings, matching, profiles
from jobhunt_core.config import settings
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests import test_integration_matching as tim

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

SHA = "e" * 40

FEED = {
    1: {
        "data": [
            {"slug": "t1", "url": "https://feed/t1", "title": "Python Developer",
             "company_name": "TechCorp AG", "description": "backend python",
             "tags": ["python"], "created_at": 400},
            {"slug": "t2", "url": "https://feed/t2", "title": "Java Backend",
             "company_name": "TechCorp AG", "description": "java spring",
             "tags": ["java"], "created_at": 300},
            {"slug": "n1", "url": "https://feed/n1", "title": "Contable Senior",
             "company_name": "FinanzHaus GmbH", "description": "contabilidad",
             "tags": ["finanzas"], "created_at": 200},
        ],
        "links": {},
    },
}


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {
        "sources": [], "scopes": [], "models": [], "consumers": [],
        "policies": [], "runs": [],
    }
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_runs(s, created["runs"])
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await dbcleanup.purge_policies(s, created["policies"])
            for mid in created["models"]:
                await dbcleanup.purge_model(s, mid)
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _api(factory, url, token=None, headers=None):
    from jobhunt_core.tests.test_integration_api import _api as api_call

    return api_call(factory, url, token=token, headers=headers)


def test_gate_a_end_to_end(db, monkeypatch):
    factory, created = db

    # ---------- Semilla: fuente + 2 SCOPES (tech y no-tech) ----------
    async def seed():
        async with factory() as s:
            source_id = uuid.uuid4()
            created["sources"].append(source_id)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:i, 'arbeitnow', 0)"),
                {"i": source_id},
            )
            scope_ids = {}
            for name, kw in (("tech", "python"), ("no-tech", "contable")):
                sid = uuid.uuid4()
                created["scopes"].append(sid)
                await s.execute(
                    sa.text(
                        "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                        "VALUES (:i, :src, CAST(:p AS jsonb), 0)"
                    ),
                    {"i": sid, "src": source_id, "p": json.dumps({"keyword": kw})},
                )
                scope_ids[name] = sid
            cid_a = await profiles.ensure_consumer(s, "gate-tenant-a")
            cid_b = await profiles.ensure_consumer(s, "gate-tenant-b")
            created["consumers"] += [cid_a, cid_b]
            pid_tech = await profiles.upsert_profile(s, cid_a, "user-tech")
            pid_fin = await profiles.upsert_profile(s, cid_a, "user-fin")
            await profiles.save_profile_revision(
                s, pid_tech, {"title": "python developer", "skills": ["python"]}
            )
            await profiles.save_profile_revision(
                s, pid_fin, {"title": "contable", "skills": ["contabilidad"]}
            )
            mid = await embeddings.register_model(s, "modelo-gate", SHA)
            created["models"].append(mid)
            polid = await matching.ensure_policy(s, "cosine", "v1")
            created["policies"].append(polid)
            key_a, secret_a = await credentials.create_credential(
                s, cid_a, ["vacancies:read", "profiles:read", "matches:read"]
            )
            key_b, secret_b = await credentials.create_credential(
                s, cid_b, ["vacancies:read", "profiles:read", "matches:read"]
            )
            await s.commit()
            return (
                scope_ids, cid_a, pid_tech, pid_fin, mid, polid,
                f"{key_a}.{secret_a}", f"{key_b}.{secret_b}",
            )

    (scope_ids, cid_a, pid_tech, pid_fin, mid, polid, token_a, token_b) = asyncio.run(seed())

    # ---------- 1+2. COSECHA REAL (run_all idempotente, HTTP mockeado) ------
    import jobhunt_core.tasks.harvest as harvest_task
    from jobhunt_core.tasks.harvest import run_all_task

    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=FEED.get(page, {"data": [], "links": {}}))

    # Parche ACOTADO a la fase de cosecha (httpx es global: dejarlo activo
    # rompería el ASGITransport de las llamadas a la API más abajo).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            harvest_task.httpx, "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )
        r = run_all_task.apply(args=["gate-a-run"])
    assert r.successful()
    created["runs"].append(uuid.UUID(r.result["run_id"]))
    # Criterio 2: AMBOS scopes cosechan (tech y no-tech).
    tech_status = r.result["scopes"][str(scope_ids["tech"])]
    fin_status = r.result["scopes"][str(scope_ids["no-tech"])]
    assert (tech_status, fin_status, r.result["status"]) == ("ok", "ok", "ok")
    vacs = {
        row.title: row.vid
        for row in _rows(
            factory,
            "SELECT o.content->>'title' AS title, v.id AS vid FROM vacancies v "
            "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
            "WHERE v.id IN (SELECT i.vacancy_id FROM source_listing_incarnations i "
            "JOIN source_listings l ON l.id = i.source_listing_id "
            "WHERE l.source_id = ANY(:s))", s=created["sources"],
        )
    }
    assert "Python Developer" in vacs  # scope tech
    assert "Contable Senior" in vacs  # scope no-tech

    # ---------- 4. RE-ENLACE: attach comparte (reciclado en 4b, al final) ---
    from jobhunt_core.harvest.sink import RawListingSink
    from jobhunt_core.harvest.types import RawListing
    from jobhunt_core.harvest import identity as identity_mod
    from jobhunt_core.harvest import normalize as normalize_mod

    # setitem: alta con RETIRADA automática al acabar el test. El normalizador
    # debe seguir VIVO en 4b: el reciclado reconstruye la canónica de la
    # vacante compartida con el normalizador de otherboard (su nuevo primary).
    monkeypatch.setitem(
        identity_mod._EXTRACTORS, "otherboard",
        lambda p: (p.get("title"), p.get("company_name")),
    )
    monkeypatch.setitem(
        normalize_mod._NORMALIZERS, "otherboard",
        lambda raw: {"title": raw.get("title"), "company": raw.get("company_name"),
                     "description": raw.get("description"), "tags": raw.get("tags"),
                     "location": None, "remote": None, "salary": None},
    )

    async def cross_source():
        async with factory() as s:
            src2, scope2 = uuid.uuid4(), uuid.uuid4()
            created["sources"].append(src2)
            created["scopes"].append(scope2)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:i, 'otherboard', 0)"),
                {"i": src2},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:i, :s, '{}'::jsonb, 0)"
                ),
                {"i": scope2, "s": src2},
            )
            await s.commit()
            # MISMA url → attach (no colapsa en vacante duplicada). Título
            # DISTINTO al de arbeitnow a propósito (2ª rev. GATE): es el
            # DISCRIMINADOR del assert de canónica reconstruida en 4b — con
            # el mismo título, un puntero stale a la revisión vieja daría el
            # mismo verde. El attach es por URL: el título no lo afecta.
            await RawListingSink().handle(
                s, str(scope2),
                (RawListing(external_id="x1", url="https://feed/t1",
                            payload={"title": "Python Engineer",
                                     "company_name": "TechCorp AG"}),),
            )
            await s.commit()
            return src2

    src2 = asyncio.run(cross_source())
    n_shared = _rows(
        factory,
        "SELECT count(*) AS n_inc, count(DISTINCT l.source_id) AS n_src, "
        "count(DISTINCT i.vacancy_id) AS n_vac "
        "FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.url_normalized = 'https://feed/t1' AND i.ended_at IS NULL",
    )[0]
    # BILATERAL (1ª rev. GATE): 2 encarnaciones ACTIVAS de 2 fuentes DISTINTAS
    # compartiendo UNA vacante — contar solo vacantes==1 pasaría igual si el
    # listing del 2º board se hubiera descartado (encarnación jamás creada).
    assert (n_shared.n_inc, n_shared.n_src, n_shared.n_vac) == (2, 2, 1)

    # ---------- 1. EMBEDDINGS (ofertas + perfiles, backend fake) ------------
    from jobhunt_core.tasks.embedding import run_pending_task

    embeddings.set_backend_factory(lambda name, version: tim.DirectionalBackend())
    try:
        er = run_pending_task.apply(kwargs={"limit": 100})
        assert er.successful()
        key = f"modelo-gate/{SHA}"
        # 2 canónicas: Python Developer + Contable Senior (Java no casa con
        # ningún keyword; el attach cross-source no crea canónica nueva).
        assert er.result["embedded"][key] == 2
        assert er.result["profiles_embedded"][key] == 2  # los 2 perfiles
    finally:
        embeddings.set_backend_factory(None)

    # ---------- 1. MATCHING (los 2 perfiles) --------------------------------
    from jobhunt_core.tasks.matching import run_profile_task

    for pid in (pid_tech, pid_fin):
        mr = run_profile_task.apply(args=[str(pid)])
        assert mr.successful() and mr.result["status"] == "ok"

    # ---------- 5. FEED filtra TENANT (cross → 404) y sirve el propio -------
    r = _api(factory, f"/v1/profiles/{pid_tech}/matches", token=token_a)
    assert r.status_code == 200
    titles = [it["vacancy"]["title"] for it in r.json()["items"]]
    assert "Python Developer" in titles
    assert _api(factory, f"/v1/profiles/{pid_tech}/matches", token=token_b).status_code == 404

    # ---------- 3. DESCARTES ESTABLES ---------------------------------------
    async def dismiss():
        async with factory() as s:
            await matching.set_dismissed(s, pid_tech, vacs["Python Developer"], True)
            await s.commit()

    asyncio.run(dismiss())
    run_profile_task.apply(args=[str(pid_tech)])  # RE-evaluación completa
    r = _api(factory, f"/v1/profiles/{pid_tech}/matches", token=token_a)
    assert "Python Developer" not in [it["vacancy"]["title"] for it in r.json()["items"]]
    st = _rows(
        factory,
        "SELECT dismissed_at, current_eval_id FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v",
        p=pid_tech, v=vacs["Python Developer"],
    )[0]
    assert st.dismissed_at is not None and st.current_eval_id is not None  # estable

    # ---------- 5. FEED filtra ACTIVA (archivada fuera) ---------------------
    async def archive():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": vacs["Contable Senior"]},
            )
            await s.commit()

    asyncio.run(archive())
    r = _api(factory, f"/v1/profiles/{pid_fin}/matches", token=token_a)
    assert "Contable Senior" not in [it["vacancy"]["title"] for it in r.json()["items"]]

    # ---------- 6. ENTREGA at-least-once idempotente ------------------------
    from jobhunt_core.tests.test_integration_delivery import FakeInbox
    from jobhunt_core.tasks.delivery import dispatch_outbox_task

    inbox = FakeInbox()
    delivery.set_transport(inbox.transport)
    try:
        dr = dispatch_outbox_task.apply(kwargs={"limit": 200})
        assert dr.successful() and dr.result["delivered"] >= 2
        n_before, calls_before = len(inbox.rows), inbox.calls

        async def force_redelivery():
            async with factory() as s:
                await s.execute(
                    sa.text(
                        "UPDATE integration_outbox_deliveries "
                        "SET state = 'pending', next_attempt_at = clock_timestamp() "
                        "WHERE event_id IN (SELECT event_id FROM integration_outbox "
                        "WHERE subject_profile_id = :p)"
                    ),
                    {"p": pid_tech},
                )
                await s.commit()

        asyncio.run(force_redelivery())
        dr2 = dispatch_outbox_task.apply(kwargs={"limit": 200})
        # El dedup solo queda DEMOSTRADO si la re-entrega OCURRIÓ (1ª rev.
        # GATE): sin comprobar que el transporte se re-invocó, un claim roto
        # que no re-entrega nada daría exactamente el mismo verde.
        assert dr2.successful() and dr2.result["delivered"] >= 1
        assert inbox.calls > calls_before  # el MISMO event_id viajó 2 veces
        assert len(inbox.rows) == n_before  # ... y el inbox DEDUPLICÓ
    finally:
        delivery.set_transport(None)

    # ---------- 4b. RECICLADO: vacante NUEVA + primary reasignado -----------
    # Al FINAL a propósito: recicla el slot arbeitnow de 'Python Developer'
    # (cierra su encarnación y rehace la canónica de la compartida), lo que
    # alteraría los conteos de embeddings/matching de los bloques previos.
    # URL también nueva: un reciclado real (slug reutilizado para OTRO puesto)
    # no debe rozar el camino de attach por URL ya ejercitado arriba.
    v1 = vacs["Python Developer"]

    async def recycle():
        async with factory() as s:
            eid = (
                await s.execute(
                    sa.text(
                        "SELECT external_id FROM source_listings "
                        "WHERE source_id = :src AND url_normalized = 'https://feed/t1'"
                    ),
                    {"src": created["sources"][0]},
                )
            ).scalar_one()
            # Mismo slot, contenido nuevo con EMPRESA distinta → recycle guard.
            await RawListingSink().handle(
                s, str(scope_ids["tech"]),
                (RawListing(external_id=eid, url="https://feed/t1b",
                            payload={"slug": eid, "url": "https://feed/t1b",
                                     "title": "Office Manager",
                                     "company_name": "WombatWorks GmbH",
                                     "description": "gestión de oficina",
                                     "tags": ["admin"], "created_at": 500}),),
            )
            await s.commit()
            return eid

    eid = asyncio.run(recycle())
    rec = _rows(
        factory,
        "SELECT i.vacancy_id, i.ended_at FROM source_listing_incarnations i "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.source_id = :src AND l.external_id = :e ORDER BY i.seq",
        src=created["sources"][0], e=eid,
    )
    # La encarnación vieja (la de la compartida) quedó CERRADA y el slot abrió
    # una vacante NUEVA — el reciclado no hereda la identidad anterior.
    assert len(rec) == 2
    assert rec[0].vacancy_id == v1 and rec[0].ended_at is not None
    assert rec[1].ended_at is None and rec[1].vacancy_id != v1
    prim = _rows(
        factory,
        "SELECT i.ended_at, l.source_id, o.content->>'title' AS title "
        "FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "LEFT JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
        "WHERE v.id = :v", v=v1,
    )[0]
    # El primary de la compartida quedó REASIGNADO a la encarnación ACTIVA de
    # otherboard y su canónica reconstruida desde ESE primary: el título es el
    # de otherboard (2ª rev. GATE) — la revisión vieja de arbeitnow sigue en
    # BD con 'Python Developer', así que un puntero stale NO pasaría.
    assert prim.ended_at is None and prim.source_id == src2
    assert prim.title == "Python Engineer"

    # ---------- 7. CORE AISLADO ---------------------------------------------
    from urllib.parse import urlsplit

    from jobhunt_core.celery_app import celery_app

    assert urlsplit(settings.CORE_BROKER_URL).hostname == "redis-core"
    routed = {r["queue"] for r in celery_app.conf.task_routes.values()}
    assert all(q.startswith("core.") for q in routed)
