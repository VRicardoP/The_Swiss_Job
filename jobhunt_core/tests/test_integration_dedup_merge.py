"""D2 — fusión diaria de duplicados por regla (`dedup_merge.py`).

Fija: la fusión marca `merged_into` y TRASLADA el estado del usuario con su
rastro en merge_log/merge_transfers; un par rechazado no se fusiona nunca (ni
por transitividad); la regla veta ubicación, texto o salario distintos; las
cadenas quedan a un salto; la pasada es idempotente y reanudable; la cosecha
que vuelve a ver al perdedor no lo resucita; los lectores no lo sirven; y el
borrado GDPR se lleva las imágenes del estado trasladado. Cada test lleva su
control negativo: el mismo montaje sin la condición que se prueba.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import runs
from jobhunt_core.config import settings
from jobhunt_core.dedup_merge import MERGE_RULE, merge_batch
from jobhunt_core.harvest import normalize as normalize_mod
from jobhunt_core.harvest.sink import RawListing, RawListingSink
from jobhunt_core.tests import dbcleanup

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    made = {"sources": [], "scopes": [], "consumers": [], "schools": []}
    yield factory, made

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_consumer_graph(s, made["consumers"])
            await dbcleanup.purge_source_graph(s, made["sources"], made["scopes"])
            if made["schools"]:
                await s.execute(
                    sa.text("DELETE FROM schools WHERE id = ANY(:i)"),
                    {"i": made["schools"]},
                )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _normalizer(raw):
    return {
        "title": raw.get("title"),
        "company": raw.get("company"),
        "description": raw.get("description"),
        "tags": [],
        "location": raw.get("location"),
        "salary": raw.get("salary"),
    }


def _source(factory, made, name):
    """Fuente + scope; `name` con prefijo `legacy:` hace legacy a la fuente."""
    source_id, scope_id = uuid.uuid4(), uuid.uuid4()
    made["sources"].append(source_id)
    made["scopes"].append(scope_id)
    full = f"{name}-{source_id.hex[:8]}"
    normalize_mod.register_normalizer(full, _normalizer)

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :n, 0)"),
                {"id": source_id, "n": full},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:id, :src, '{}'::jsonb, 0)"
                ),
                {"id": scope_id, "src": source_id},
            )
            await s.commit()

    asyncio.run(go())
    return str(scope_id)


def _offer(ext, *, title="Data Engineer", loc="Zürich", salary=None, desc=None):
    payload = {
        "title": title,
        "company": "ACME AG",
        "description": desc or f"Wir suchen {title}",
        "location": loc,
    }
    if salary is not None:
        payload["salary"] = salary
    return RawListing(external_id=ext, url=f"https://x.test/{ext}", payload=payload)


def _harvest(factory, scope, *offers):
    async def go():
        async with factory() as s:
            await RawListingSink().handle(s, scope, tuple(offers))
            await s.commit()

    asyncio.run(go())


def _sql(factory, sql, params=None, fetch=None):
    async def go():
        async with factory() as s:
            r = await s.execute(sa.text(sql), params or {})
            out = getattr(r, fetch)() if fetch else None
            await s.commit()
            return out

    return asyncio.run(go())


def _vac(factory, ext):
    return _sql(
        factory,
        "SELECT i.vacancy_id FROM source_listings sl "
        "JOIN source_listing_incarnations i ON i.source_listing_id = sl.id "
        "WHERE sl.external_id = :e ORDER BY i.seq DESC LIMIT 1",
        {"e": ext},
        "scalar_one",
    )


def _pair(factory, a, b, state="pending", cid=None):
    cid = cid or uuid.uuid4()
    _sql(
        factory,
        "INSERT INTO dedup_candidates (id, vacancy_a, vacancy_b, similarity, state) "
        "VALUES (:id, :a, :b, 0.999, CAST(:st AS dedup_candidate_state))",
        {"id": cid, "a": a, "b": b, "st": state},
    )
    return cid


def _merge_all(factory, batch_size=200):
    async def go():
        totals = {"merged": 0, "collapsed": 0, "vetoed": 0}
        cursor = None
        while True:
            async with factory() as s:
                r = await merge_batch(s, after_id=cursor, batch_size=batch_size)
                await s.commit()
            for k in totals:
                totals[k] += r[k]
            cursor = r["next_after"]
            if r["complete"]:
                return totals

    return asyncio.run(go())


def _merged_into(factory, vid):
    return _sql(
        factory,
        "SELECT merged_into FROM vacancies WHERE id = :v",
        {"v": vid},
        "scalar_one",
    )


def _profile(factory, made):
    cid, pid = uuid.uuid4(), uuid.uuid4()
    made["consumers"].append(cid)
    _sql(
        factory,
        "INSERT INTO consumers (id, name) VALUES (:c, :n)",
        {"c": cid, "n": f"dm-{cid.hex[:8]}"},
    )
    _sql(
        factory,
        "INSERT INTO profiles (id, consumer_id, external_ref) VALUES (:p, :c, 'dm')",
        {"p": pid, "c": cid},
    )
    return pid


def _tag():
    return uuid.uuid4().hex[:8]


def test_merge_moves_user_state_to_native_winner_and_logs_it(db):
    factory, made = db
    t = _tag()
    legacy = _source(factory, made, "legacy:dm")
    native = _source(factory, made, "dm-native")
    _harvest(factory, legacy, _offer(f"{t}-l"))
    _harvest(factory, native, _offer(f"{t}-n"))
    v_legacy, v_native = _vac(factory, f"{t}-l"), _vac(factory, f"{t}-n")
    cand = _pair(factory, v_legacy, v_native)
    pid = _profile(factory, made)
    _sql(
        factory,
        "INSERT INTO profile_vacancy_state (profile_id, vacancy_id, feedback, "
        " feedback_recorded_at, saved_at, notes) "
        "VALUES (:p, :v, 'thumbs_up', clock_timestamp(), clock_timestamp(), 'llamar')",
        {"p": pid, "v": v_legacy},
    )
    _sql(
        factory,
        "INSERT INTO applications (profile_id, vacancy_id, status) "
        "VALUES (:p, :v, 'applied')",
        {"p": pid, "v": v_legacy},
    )

    totals = _merge_all(factory)

    assert totals["merged"] >= 1
    assert _merged_into(factory, v_legacy) == v_native
    assert _merged_into(factory, v_native) is None
    winner_state = _sql(
        factory,
        "SELECT feedback, saved_at IS NOT NULL AS saved, notes "
        "FROM profile_vacancy_state WHERE profile_id = :p AND vacancy_id = :v",
        {"p": pid, "v": v_native},
        "one",
    )
    assert tuple(winner_state) == ("thumbs_up", True, "llamar")
    loser_state = _sql(
        factory,
        "SELECT feedback, saved_at, notes FROM profile_vacancy_state "
        "WHERE profile_id = :p AND vacancy_id = :v",
        {"p": pid, "v": v_legacy},
        "one",
    )
    assert tuple(loser_state) == (None, None, None)  # movido, no copiado
    assert (
        _sql(
            factory,
            "SELECT vacancy_id FROM applications WHERE profile_id = :p",
            {"p": pid},
            "scalar_one",
        )
        == v_native
    )
    log = _sql(
        factory,
        "SELECT ml.id, ml.winner_id, ml.loser_id, ml.evidence->>'rule' AS rule, "
        "       dc.state::text AS state, dc.resolved_by "
        "FROM dedup_candidates dc JOIN merge_log ml ON ml.id = dc.merge_log_id "
        "WHERE dc.id = :c",
        {"c": cand},
        "one",
    )
    assert (log.winner_id, log.loser_id) == (v_native, v_legacy)
    assert log.rule == MERGE_RULE and log.state == "confirmed"
    assert log.resolved_by == MERGE_RULE
    entities = sorted(
        r.entity
        for r in _sql(
            factory,
            "SELECT entity, profile_id FROM merge_transfers WHERE merge_log_id = :m",
            {"m": log.id},
            "all",
        )
    )
    assert entities == [
        "applications",
        "profile_vacancy_state",
        "profile_vacancy_state",
        "vacancies.merged_into",
    ]

    # Control negativo: el GDPR se lleva las imágenes del estado trasladado.
    from jobhunt_core.erasure import _erase_profile_graph

    async def erase():
        async with factory() as s:
            ref, cname = (
                await s.execute(
                    sa.text(
                        "SELECT p.external_ref, c.name FROM profiles p "
                        "JOIN consumers c ON c.id = p.consumer_id WHERE p.id = :p"
                    ),
                    {"p": pid},
                )
            ).one()
            await _erase_profile_graph(s, ref, cname)
            await s.commit()

    def con_perfil():
        return _sql(
            factory,
            "SELECT count(*) FROM merge_transfers WHERE profile_id = :p",
            {"p": pid},
            "scalar_one",
        )

    assert con_perfil() == 3
    asyncio.run(erase())
    assert con_perfil() == 0


def test_rule_vetoes_and_identical_control(db):
    factory, made = db
    t = _tag()
    a = _source(factory, made, "dm-a")
    b = _source(factory, made, "dm-b")
    _harvest(
        factory,
        a,
        _offer(f"{t}-same"),
        _offer(f"{t}-loc", loc="Basel"),
        _offer(f"{t}-txt", title="Data Scientist"),
        _offer(f"{t}-sal", salary="CHF 100k"),
    )
    _harvest(
        factory,
        b,
        _offer(f"{t}-same2"),
        _offer(f"{t}-loc2", loc="Bern"),
        _offer(f"{t}-txt2", title="Data Analyst"),
        _offer(f"{t}-sal2", salary="CHF 120k"),
    )
    pares = {
        k: (_vac(factory, f"{t}-{k}"), _vac(factory, f"{t}-{k}2"))
        for k in ("same", "loc", "txt", "sal")
    }
    for x, y in pares.values():
        _pair(factory, x, y)

    _merge_all(factory)

    def fusionado(k):
        x, y = pares[k]
        return _merged_into(factory, x) == y or _merged_into(factory, y) == x

    assert fusionado("same")  # control: idénticas se fusionan
    assert not fusionado("loc")  # multi-ciudad (regla ratificada)
    assert not fusionado("txt")
    assert not fusionado("sal")  # Clera: salario distinto, vacantes distintas
    pendientes = _sql(
        factory,
        "SELECT count(*) FROM dedup_candidates WHERE state = 'pending' "
        "AND vacancy_a = ANY(:v)",
        {"v": [p[0] for p in pares.values()]},
        "scalar_one",
    )
    assert pendientes == 3  # lo vetado sigue pendiente, NO rechazado


def test_rejected_pair_never_merges_not_even_transitively(db):
    factory, made = db
    t = _tag()
    a = _source(factory, made, "legacy:dm-r1")
    b = _source(factory, made, "legacy:dm-r2")
    c = _source(factory, made, "dm-r3")
    _harvest(factory, a, _offer(f"{t}-a"), _offer(f"{t}-x", title="Otro"))
    _harvest(factory, b, _offer(f"{t}-b"), _offer(f"{t}-y", title="Otro"))
    _harvest(factory, c, _offer(f"{t}-c"))
    va, vb, vc = (_vac(factory, f"{t}-{k}") for k in "abc")
    vx, vy = _vac(factory, f"{t}-x"), _vac(factory, f"{t}-y")
    # x/y: par idéntico pero RECHAZADO (curación manual) — nunca se fusiona.
    _pair(factory, vx, vy, state="rejected")
    # a~c y b~c pendientes, a~b rechazado: fusionar a en c no autoriza b en c.
    _pair(factory, va, vb, state="rejected")
    _pair(factory, va, vc, cid=uuid.UUID(int=1))
    _pair(factory, vb, vc, cid=uuid.UUID(int=2))

    totals = _merge_all(factory)

    assert _merged_into(factory, vx) is None and _merged_into(factory, vy) is None
    assert _merged_into(factory, va) == vc  # c gana: es la nativa
    assert _merged_into(factory, vb) is None
    assert totals["vetoed"] >= 1
    # Control negativo: sin el rechazo a~b, b SÍ se funde en c.
    _sql(
        factory,
        "DELETE FROM dedup_candidates WHERE state = 'rejected' AND "
        "LEAST(vacancy_a, vacancy_b) = LEAST(CAST(:a AS uuid), CAST(:b AS uuid)) AND "
        "GREATEST(vacancy_a, vacancy_b) = GREATEST(CAST(:a AS uuid), CAST(:b AS uuid))",
        {"a": str(va), "b": str(vb)},
    )
    _merge_all(factory)
    assert _merged_into(factory, vb) == vc


def test_chain_stays_one_hop_and_collapsed_pair_is_confirmed(db):
    factory, made = db
    t = _tag()
    l1 = _source(factory, made, "legacy:dm-c1")
    l2 = _source(factory, made, "legacy:dm-c2")
    n = _source(factory, made, "dm-cn")
    _harvest(factory, l1, _offer(f"{t}-a", desc="descripción larga " * 4))
    _harvest(factory, l2, _offer(f"{t}-b", desc="descripción larga " * 4))
    va, vb = _vac(factory, f"{t}-a"), _vac(factory, f"{t}-b")
    # Primera noche: dos copias legacy; gana una de ellas.
    _pair(factory, va, vb)
    _merge_all(factory)
    first_winner = va if _merged_into(factory, vb) == va else vb
    first_loser = vb if first_winner == va else va
    assert _merged_into(factory, first_loser) == first_winner

    # Llega la nativa: gana ella y la cadena se aplana.
    _harvest(factory, n, _offer(f"{t}-n", desc="descripción larga " * 4))
    vn = _vac(factory, f"{t}-n")
    _pair(factory, first_winner, vn, cid=uuid.UUID(int=3))
    collapsed = _pair(factory, first_loser, vn, cid=uuid.UUID(int=4))
    totals = _merge_all(factory)

    assert _merged_into(factory, first_winner) == vn
    assert _merged_into(factory, first_loser) == vn  # un salto, no dos
    assert totals["collapsed"] >= 1
    state = _sql(
        factory,
        "SELECT state::text FROM dedup_candidates WHERE id = :c",
        {"c": collapsed},
        "scalar_one",
    )
    assert state == "confirmed"


def test_idempotent_and_resumable_by_batches(db):
    factory, made = db
    t = _tag()
    a = _source(factory, made, "dm-i1")
    b = _source(factory, made, "dm-i2")
    _harvest(factory, a, *(_offer(f"{t}-a{i}", title=f"Rol {i}") for i in range(3)))
    _harvest(factory, b, *(_offer(f"{t}-b{i}", title=f"Rol {i}") for i in range(3)))
    pairs = [(_vac(factory, f"{t}-a{i}"), _vac(factory, f"{t}-b{i}")) for i in range(3)]
    for i, (x, y) in enumerate(pairs):
        _pair(factory, x, y, cid=uuid.UUID(int=10 + i))

    def fusionados():
        return sum(
            1
            for x, y in pairs
            if _merged_into(factory, x) == y or _merged_into(factory, y) == x
        )

    async def one_batch():
        async with factory() as s:
            r = await merge_batch(s, batch_size=1)
            await s.commit()
            return r

    first = asyncio.run(one_batch())
    assert first["complete"] is False
    assert fusionados() == 1  # se cortó tras un lote: el resto, pendiente
    _merge_all(factory, batch_size=1)  # la pasada siguiente continúa
    assert fusionados() == 3
    logs_before = _sql(
        factory,
        "SELECT count(*) FROM merge_log WHERE winner_id = ANY(:v) OR loser_id = ANY(:v)",
        {"v": [v for p in pairs for v in p]},
        "scalar_one",
    )
    again = _merge_all(factory)  # idempotente: nada que hacer
    logs_after = _sql(
        factory,
        "SELECT count(*) FROM merge_log WHERE winner_id = ANY(:v) OR loser_id = ANY(:v)",
        {"v": [v for p in pairs for v in p]},
        "scalar_one",
    )
    assert logs_before == logs_after == 3
    assert again["merged"] == 0


def test_school_linked_pair_is_left_pending(db):
    factory, made = db
    t = _tag()
    a = _source(factory, made, "dm-s1")
    b = _source(factory, made, "dm-s2")
    _harvest(factory, a, _offer(f"{t}-a"), _offer(f"{t}-c"))
    _harvest(factory, b, _offer(f"{t}-b"), _offer(f"{t}-d"))
    va, vb = _vac(factory, f"{t}-a"), _vac(factory, f"{t}-b")
    vc, vd = _vac(factory, f"{t}-c"), _vac(factory, f"{t}-d")
    _profile(factory, made)
    cid = made["consumers"][-1]
    school, monitor = uuid.uuid4(), uuid.uuid4()
    made["schools"].append(school)
    _sql(
        factory,
        "INSERT INTO schools(id,school_key,name,country) VALUES (:id,:k,'DM','CH')",
        {"id": school, "k": f"CH:dm-{t}"},
    )
    _sql(
        factory,
        "INSERT INTO school_monitors(id,consumer_id,school_id,external_ref,settings) "
        "VALUES (:id,:c,:s,'dm','{}'::jsonb)",
        {"id": monitor, "c": cid, "s": school},
    )
    _sql(
        factory,
        "INSERT INTO school_job_details(id,monitor_id,consumer_id,source_ref,vacancy_id,metadata) "
        "VALUES (:id,:m,:c,'ref',:v,'{}'::jsonb)",
        {"id": uuid.uuid4(), "m": monitor, "c": cid, "v": va},
    )
    _pair(factory, va, vb)
    _pair(factory, vc, vd)  # control: el mismo par sin vínculo escolar

    _merge_all(factory)

    assert _merged_into(factory, va) is None and _merged_into(factory, vb) is None
    assert _merged_into(factory, vc) == vd or _merged_into(factory, vd) == vc


def test_readers_and_harvest_after_merge(db):
    from jobhunt_core import applications
    from jobhunt_core.api.v1 import _vacancy_dtos

    factory, made = db
    t = _tag()
    legacy = _source(factory, made, "legacy:dm-h")
    native = _source(factory, made, "dm-hn")
    _harvest(factory, legacy, _offer(f"{t}-l"))
    _harvest(factory, native, _offer(f"{t}-n"))
    vl, vn = _vac(factory, f"{t}-l"), _vac(factory, f"{t}-n")

    async def dtos():
        async with factory() as s:
            return set(await _vacancy_dtos(s, [vl, vn]))

    assert asyncio.run(dtos()) == {vl, vn}  # control: antes, las dos se sirven
    _pair(factory, vl, vn)
    _merge_all(factory)

    assert asyncio.run(dtos()) == {vn}

    async def resolve():
        async with factory() as s:
            return await applications.resolve_direct(s, vl)

    assert asyncio.run(resolve()) == vn  # un id fusionado lleva al ganador

    # La fuente del perdedor vuelve a publicar la oferta: no la resucita.
    vivas = (
        "SELECT count(*) FROM vacancies v "
        "JOIN source_listing_incarnations i ON i.vacancy_id = v.id "
        "JOIN source_listings sl ON sl.id = i.source_listing_id "
        "WHERE sl.external_id = :e AND v.merged_into IS NULL AND v.archived_at IS NULL"
    )
    _harvest(factory, legacy, _offer(f"{t}-l"))
    assert _sql(factory, vivas, {"e": f"{t}-l"}, "scalar_one") == 0
    assert _merged_into(factory, vl) == vn


def test_any_scope_running_uses_the_lease(db):
    factory, made = db
    scope = _source(factory, made, "dm-run")

    async def go():
        async with factory() as s:
            run_id = await runs.start_run(s, f"dm-{uuid.uuid4()}")
            token = await runs.claim_scope_run(s, run_id, scope)
            await s.commit()
            vivo = await runs.any_scope_running(s)
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET started_at = clock_timestamp() "
                    "- interval '2 hours', heartbeat_at = clock_timestamp() "
                    "- interval '2 hours' WHERE run_id = :r"
                ),
                {"r": run_id},
            )
            colgado = await runs.any_scope_running(s)
            await s.execute(
                sa.text(
                    "UPDATE source_harvest_runs SET heartbeat_at = clock_timestamp() "
                    "WHERE run_id = :r"
                ),
                {"r": run_id},
            )
            await runs.finish_scope_run(s, run_id, scope, "ok", token)
            terminado = await runs.any_scope_running(s)
            await s.execute(
                sa.text("DELETE FROM source_harvest_runs WHERE run_id = :r"),
                {"r": run_id},
            )
            await s.execute(
                sa.text("DELETE FROM harvest_runs WHERE id = :r"), {"r": run_id}
            )
            await s.commit()
            return vivo, colgado, terminado

    vivo, colgado, terminado = asyncio.run(go())
    assert vivo is True
    assert colgado is False  # worker muerto: no bloquea para siempre
    assert terminado is False
