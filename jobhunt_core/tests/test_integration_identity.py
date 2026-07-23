"""Identidad determinista (A-05) contra Postgres real.

DoD: guard de reciclado (empresa por tokens) → nueva incarnación/vacante sin
corromper historial; attach cross-source por url_normalized + link_evidence;
alias external_id↔URL; medio → dedup_candidates (pending). JAMÁS se funde por
semántica sola. Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobhunt_core.harvest.providers  # noqa: F401 — registra el extractor arbeitnow
from jobhunt_core.config import settings
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"sources": [], "scopes": [], "extra_vacs": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            srcs = created["sources"]
            vac_ids = (
                await s.execute(
                    sa.text(
                        "SELECT DISTINCT i.vacancy_id FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.source_id = ANY(:srcs)"
                    ),
                    {"srcs": srcs},
                )
            ).scalars().all()
            vac_ids = list(vac_ids) + created["extra_vacs"]
            # Orden FK-seguro; candidatos/evidencia antes que vacantes/slots.
            if vac_ids:
                await s.execute(
                    sa.text(
                        "DELETE FROM dedup_candidates "
                        "WHERE vacancy_a = ANY(:v) OR vacancy_b = ANY(:v)"
                    ),
                    {"v": vac_ids},
                )
            await s.execute(
                sa.text(
                    "DELETE FROM link_evidence WHERE source_listing_id IN "
                    "(SELECT id FROM source_listings WHERE source_id = ANY(:srcs))"
                ),
                {"srcs": srcs},
            )
            await s.execute(
                sa.text(
                    "DELETE FROM source_listing_revisions WHERE incarnation_id IN ("
                    "SELECT i.id FROM source_listing_incarnations i "
                    "JOIN source_listings l ON l.id = i.source_listing_id "
                    "WHERE l.source_id = ANY(:srcs))"
                ),
                {"srcs": srcs},
            )
            await s.execute(
                sa.text(
                    "DELETE FROM source_listing_incarnations WHERE source_listing_id IN "
                    "(SELECT id FROM source_listings WHERE source_id = ANY(:srcs))"
                ),
                {"srcs": srcs},
            )
            if vac_ids:
                await s.execute(
                    sa.text("DELETE FROM vacancies WHERE id = ANY(:v)"), {"v": vac_ids}
                )
            await s.execute(
                sa.text("DELETE FROM source_listings WHERE source_id = ANY(:srcs)"),
                {"srcs": srcs},
            )
            if created["extra_vacs"]:
                await s.execute(
                    sa.text("DELETE FROM vacancies WHERE id = ANY(:v)"),
                    {"v": created["extra_vacs"]},
                )
            for sid in created["scopes"]:
                await s.execute(
                    sa.text("DELETE FROM source_scope_state WHERE scope_id=:i"), {"i": sid}
                )
                await s.execute(
                    sa.text("DELETE FROM harvest_scopes WHERE id=:i"), {"i": sid}
                )
            await s.execute(
                sa.text("DELETE FROM sources WHERE id = ANY(:srcs)"), {"srcs": srcs}
            )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _seed(factory, created, name) -> str:
    """Fuente + scope; devuelve el scope_id (str)."""

    async def go():
        async with factory() as s:
            source_id, scope_id = uuid.uuid4(), uuid.uuid4()
            created["sources"].append(source_id)
            created["scopes"].append(scope_id)
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :name, 0)"),
                {"id": source_id, "name": name},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO harvest_scopes (id, source_id, params, tier) "
                    "VALUES (:id, :src, '{}'::jsonb, 0)"
                ),
                {"id": scope_id, "src": source_id},
            )
            await s.commit()
            return str(scope_id)

    return asyncio.run(go())


def _sink(factory, scope_id, listings) -> None:
    async def go():
        async with factory() as s:
            await RawListingSink().handle(s, scope_id, tuple(listings))
            await s.commit()

    asyncio.run(go())


def _listing(ext, company=None, title=None, url=None, v=1):
    payload = {"title": title or ext, "v": v}
    if company is not None:
        payload["company_name"] = company
    return RawListing(external_id=ext, url=url or f"https://x/{ext}", payload=payload)


def _incs(factory, ext):
    """Incarnaciones del slot `ext` con su vacante, por seq."""

    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT i.id, i.seq, i.vacancy_id, i.ended_at, "
                        "(SELECT count(*) FROM source_listing_revisions r "
                        " WHERE r.incarnation_id = i.id) AS revs "
                        "FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.external_id = :ext ORDER BY i.seq"
                    ),
                    {"ext": ext},
                )
            ).all()

    return asyncio.run(go())


def _one(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def test_recycle_on_company_change(db):
    """Guard de reciclado (ADR-01 nivel exacto): contenido nuevo con EMPRESA
    distinta → cierra la incarnación y abre otra (seq+1, vacante NUEVA); el
    historial viejo queda intacto en su incarnación/vacante."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", company="ACME AG", v=1)])
    _sink(factory, scope, [_listing("j1", company="Umbrella GmbH", v=2)])

    incs = _incs(factory, "j1")
    assert [(r.seq, r.ended_at is None, r.revs) for r in incs] == [
        (1, False, 1),  # cerrada, conserva SU revisión
        (2, True, 1),  # nueva y activa, con la revisión nueva
    ]
    assert incs[0].vacancy_id != incs[1].vacancy_id  # vacante NUEVA
    # Puntero primario fijado en la vacante nueva.
    rows = _one(
        factory,
        "SELECT primary_incarnation_id FROM vacancies WHERE id = :v",
        v=incs[1].vacancy_id,
    )
    assert rows[0][0] == incs[1].id


def test_same_company_change_is_new_revision_not_recycle(db):
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", company="ACME AG", v=1)])
    _sink(factory, scope, [_listing("j1", company="Acme GmbH", v=2)])  # misma empresa
    incs = _incs(factory, "j1")
    assert [(r.seq, r.ended_at is None, r.revs) for r in incs] == [(1, True, 2)]


def test_missing_company_never_recycles(db):
    """Sin identidad completa el guard es CONSERVADOR: nueva revisión en la
    misma incarnación (no corromper por falta de datos)."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", v=1)])  # sin company_name
    _sink(factory, scope, [_listing("j1", company="ACME", v=2)])
    incs = _incs(factory, "j1")
    assert [(r.seq, r.ended_at is None, r.revs) for r in incs] == [(1, True, 2)]


def test_cross_source_attach_by_url(db):
    """Nivel 2 (cross-source fuerte): la MISMA url_normalized vigente en otra
    fuente → attach a esa vacante + link_evidence; el puntero primario NO se
    roba a la fuente original."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", url="https://x/shared")])
    _sink(factory, scope_b, [_listing("b1", url="https://x/shared/")])  # normaliza igual

    a1, b1 = _incs(factory, "a1")[0], _incs(factory, "b1")[0]
    assert b1.vacancy_id == a1.vacancy_id  # UNA vacante compartida
    rows = _one(
        factory,
        "SELECT primary_incarnation_id FROM vacancies WHERE id = :v", v=a1.vacancy_id,
    )
    assert rows[0][0] == a1.id  # primary sigue siendo el de la fuente original
    ev = _one(
        factory,
        "SELECT le.method, le.confidence FROM link_evidence le "
        "JOIN source_listings sl ON sl.id = le.source_listing_id "
        "WHERE sl.external_id = 'b1'",
    )
    assert [(r.method, float(r.confidence)) for r in ev] == [("url_normalized", 1.0)]


def test_url_alias_conflict_external_id_wins(db):
    """Conflicto external_id↔URL (ADR-01): el listing casa con el slot 'a' por
    external_id pero trae la URL de 'b' → se procesa como 'a' (gana
    external_id) y la URL queda como alias en link_evidence — sin spam en
    cosechas repetidas."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("a", url="https://x/urlA"), _listing("b", url="https://x/urlB")])
    for _ in range(2):  # dos cosechas con el conflicto: evidencia UNA sola vez
        _sink(factory, scope, [_listing("a", url="https://x/urlB", v=2)])

    a = _incs(factory, "a")
    assert [(r.seq, r.ended_at is None) for r in a] == [(1, True)]  # sin reciclar
    slot_urln = _one(
        factory,
        "SELECT url_normalized FROM source_listings WHERE external_id = 'a'",
    )
    assert slot_urln[0][0] == "https://x/urlA"  # la identidad del slot NO cambia
    b_vac = _incs(factory, "b")[0].vacancy_id
    ev = _one(
        factory,
        "SELECT le.vacancy_id, le.method FROM link_evidence le "
        "JOIN source_listings sl ON sl.id = le.source_listing_id "
        "WHERE sl.external_id = 'a'",
    )
    assert [(r.vacancy_id, r.method) for r in ev] == [(b_vac, "url_alias")]


def test_url_drift_creates_pending_candidate_never_merges(db):
    """Misma URL vigente en dos fuentes con vacantes DISTINTAS (aquí forzado
    archivando la vacante A: el attach EXCLUYE vacantes archivadas) →
    dedup_candidates pending idempotente. JAMÁS attach tardío ni fusión."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", url="https://x/shared")])
    vac_a = _incs(factory, "a1")[0].vacancy_id

    async def archive():
        async with factory() as s:
            await s.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": vac_a},
            )
            await s.commit()

    asyncio.run(archive())

    _sink(factory, scope_b, [_listing("b1", url="https://x/shared")])
    vac_b = _incs(factory, "b1")[0].vacancy_id
    assert vac_b != vac_a  # archivada: NO attach → vacante propia

    for _ in range(2):  # idempotente (par canónico único)
        _sink(factory, scope_b, [_listing("b1", url="https://x/shared")])
    cands = _one(
        factory,
        "SELECT vacancy_a, vacancy_b, similarity, state FROM dedup_candidates "
        "WHERE vacancy_a IN (:a, :b) OR vacancy_b IN (:a, :b)",
        a=vac_a, b=vac_b,
    )
    assert len(cands) == 1
    assert {cands[0].vacancy_a, cands[0].vacancy_b} == {vac_a, vac_b}
    assert (float(cands[0].similarity), cands[0].state) == (0.9, "pending")


def test_recycle_of_shared_vacancy_reassigns_primary_and_never_reattaches(db):
    """Auditoría A-05 #1 (repro del verificador): a1 (ACME) crea V; b1 de OTRA
    fuente se attachea a V; a1 cambia a Umbrella → recicla. El slot reciclado
    NO vuelve a V (ADR-01: vacante NUEVA) y V — que sigue activa por b1 — no
    puede quedar con primary apuntando a una incarnación cerrada: se reasigna
    determinista a la activa. El drift de URL deja el par como candidato."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", company="ACME AG", url="https://x/shared", v=1)])
    _sink(factory, scope_b, [_listing("b1", url="https://x/shared", v=1)])
    v_shared = _incs(factory, "a1")[0].vacancy_id
    b1_inc = _incs(factory, "b1")[0]
    assert b1_inc.vacancy_id == v_shared  # compartida vía attach

    _sink(factory, scope_a, [_listing("a1", company="Umbrella GmbH", url="https://x/shared", v=2)])

    a1 = _incs(factory, "a1")
    assert [(r.seq, r.ended_at is None) for r in a1] == [(1, False), (2, True)]
    assert a1[1].vacancy_id != v_shared  # reciclado = vacante NUEVA, sin re-attach
    # V sigue activa (b1) y su primary quedó REASIGNADO a la incarnación activa.
    rows = _one(
        factory,
        "SELECT v.primary_incarnation_id, v.archived_at, i.ended_at "
        "FROM vacancies v JOIN source_listing_incarnations i "
        "ON i.id = v.primary_incarnation_id WHERE v.id = :v",
        v=v_shared,
    )
    assert rows[0].archived_at is None
    assert rows[0].ended_at is None  # el primary apunta a una incarnación ACTIVA
    assert rows[0].primary_incarnation_id == b1_inc.id
    # La URL compartida con vacantes ya distintas → candidato pending (drift).
    cands = _one(
        factory,
        "SELECT state FROM dedup_candidates "
        "WHERE vacancy_a IN (:a, :b) AND vacancy_b IN (:a, :b)",
        a=v_shared, b=a1[1].vacancy_id,
    )
    assert [r.state for r in cands] == ["pending"]


def test_non_string_identity_degrades_conservative_never_aborts(db):
    """Auditoría A-05 #2: title/company_name no-string (número/bool/lista) del
    feed NO revienta el lote — identidad ausente: persiste normal y el guard
    no recicla."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    weird = RawListing(
        external_id="w1", url="https://x/w1", payload={"title": 42, "v": 1}
    )
    _sink(factory, scope, [weird, _listing("ok1")])  # sin excepción
    assert len(_incs(factory, "w1")) == 1
    # Guard con company no-string en la cosecha siguiente: conservador.
    _sink(factory, scope, [_listing("j2", company="ACME AG", v=1)])
    _sink(
        factory, scope,
        [RawListing(external_id="j2", url="https://x/j2",
                    payload={"title": "j2", "company_name": 7, "v": 2})],
    )
    incs = _incs(factory, "j2")
    assert [(r.seq, r.ended_at is None, r.revs) for r in incs] == [(1, True, 2)]


def test_merged_vacancy_excluded_from_attach(db):
    """Auditoría A-05 #3: una vacante FUSIONADA (merged_into) queda excluida
    del attach igual que la archivada — espejo del test de archived_at."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", url="https://x/shared")])
    vac_a = _incs(factory, "a1")[0].vacancy_id

    async def merge_away():
        async with factory() as s:
            winner = uuid.uuid4()
            created["extra_vacs"].append(winner)
            await s.execute(
                sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": winner}
            )
            await s.execute(
                sa.text("UPDATE vacancies SET merged_into = :w WHERE id = :v"),
                {"w": winner, "v": vac_a},
            )
            await s.commit()

    asyncio.run(merge_away())

    _sink(factory, scope_b, [_listing("b1", url="https://x/shared")])
    vac_b = _incs(factory, "b1")[0].vacancy_id
    assert vac_b != vac_a  # fusionada: NO attach → vacante propia
    cands = _one(
        factory,
        "SELECT state, similarity FROM dedup_candidates "
        "WHERE vacancy_a IN (:a, :b) AND vacancy_b IN (:a, :b)",
        a=vac_a, b=vac_b,
    )
    assert [(r.state, float(r.similarity)) for r in cands] == [("pending", 0.9)]


class PausingSink(RawListingSink):
    """Sink instrumentado (rev. 2ª #3): pausa en el punto EXACTO del protocolo
    para FIJAR la intercalación — nada de sleeps para sincronizar."""

    def __init__(self, pause_after: str):
        self._pause_after = pause_after
        self.hit = asyncio.Event()
        self.resume = asyncio.Event()

    async def _pause(self, point):
        if self._pause_after == point:
            self.hit.set()
            await self.resume.wait()

    async def _attach_candidates(self, *args, **kwargs):
        out = await super()._attach_candidates(*args, **kwargs)
        await self._pause("preselect")
        return out

    async def _close_incarnations(self, *args, **kwargs):
        out = await super()._close_incarnations(*args, **kwargs)
        await self._pause("close")
        return out


def test_concurrent_archive_vs_attach_revalidates_under_lock(db):
    """Rev. A-05 2ª #1/#3 (determinista): B pausa JUSTO tras la pre-selección
    (donde vio la vacante activa); el archive commitea; al reanudar, lock +
    revalidación del join expulsan la vacante → B crea la suya propia. Jamás
    contenido vivo enlazado a una vacante inactiva."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", url="https://x/shared")])
    vac_a = _incs(factory, "a1")[0].vacancy_id

    async def race():
        sink_b = PausingSink("preselect")

        async def run_b():
            async with factory() as s:
                await sink_b.handle(s, scope_b, (_listing("b1", url="https://x/shared"),))
                await s.commit()

        task = asyncio.create_task(run_b())
        await sink_b.hit.wait()  # B ya pre-seleccionó vac_a como candidata
        async with factory() as s1:
            await s1.execute(
                sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                {"v": vac_a},
            )
            await s1.commit()
        sink_b.resume.set()
        await task

    asyncio.run(race())
    assert _incs(factory, "b1")[0].vacancy_id != vac_a  # revalidado: propia


def test_attach_revalidates_relation_after_concurrent_recycle(db):
    """Rev. A-05 2ª #1 (repro determinista): B pre-selecciona la vacante de A
    por URL; A RECICLA su única incarnación (la vacante queda nominalmente
    activa pero VACÍA, con primary cerrado); al reanudar, la revalidación del
    JOIN COMPLETO (incarnación activa incluida) la expulsa → vacante propia."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", company="ACME AG", url="https://x/shared", v=1)])
    vac_a = _incs(factory, "a1")[0].vacancy_id

    async def race():
        sink_b = PausingSink("preselect")

        async def run_b():
            async with factory() as s:
                await sink_b.handle(s, scope_b, (_listing("b1", url="https://x/shared"),))
                await s.commit()

        task = asyncio.create_task(run_b())
        await sink_b.hit.wait()
        # A recicla COMPLETO (cierra su única incarnación de vac_a) y commitea.
        async with factory() as s:
            await RawListingSink().handle(
                s, scope_a,
                (_listing("a1", company="Umbrella GmbH", url="https://x/shared", v=2),),
            )
            await s.commit()
        sink_b.resume.set()
        await task

    asyncio.run(race())
    assert _incs(factory, "b1")[0].vacancy_id != vac_a  # relación rota: propia
    activas = _one(
        factory,
        "SELECT count(*) AS n FROM source_listing_incarnations "
        "WHERE vacancy_id = :v AND ended_at IS NULL",
        v=vac_a,
    )
    assert activas[0].n == 0  # la vieja quedó vacía: nadie se re-attacheó


def test_archived_shared_vacancy_concurrent_recycles_serialize(db):
    """Rev. A-05 2ª #2 (repro determinista): la vacante compartida por TRES
    fuentes está ARCHIVADA. El lock NO filtra vigencia: A pausa tras SU cierre
    con los locks en mano y B queda BLOQUEADO antes de poder cerrar (se
    afirma) — serializados, el primary termina en la única incarnación activa
    (la de C) también en la vacante archivada."""
    from jobhunt_core.harvest import identity as identity_mod

    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    scope_c = _seed(factory, created, "thirdboard")
    identity_mod.register_extractor(
        "otherboard", lambda p: (p.get("title"), p.get("company_name"))
    )
    try:
        _sink(factory, scope_a, [_listing("a1", company="ACME AG", url="https://x/shared", v=1)])
        _sink(factory, scope_b, [_listing("b1", company="ACME AG", url="https://x/shared", v=1)])
        _sink(factory, scope_c, [_listing("c1", url="https://x/shared", v=1)])
        v_shared = _incs(factory, "a1")[0].vacancy_id
        c1_inc = _incs(factory, "c1")[0]

        async def archive():
            async with factory() as s:
                await s.execute(
                    sa.text("UPDATE vacancies SET archived_at = now() WHERE id = :v"),
                    {"v": v_shared},
                )
                await s.commit()

        asyncio.run(archive())

        async def race():
            sink_a = PausingSink("close")
            sink_b = PausingSink("close")

            async def run(sink, scope_id, listing):
                async with factory() as s:
                    await sink.handle(s, scope_id, (listing,))
                    await s.commit()

            task_a = asyncio.create_task(run(
                sink_a, scope_a,
                _listing("a1", company="Umbrella GmbH", url="https://x/shared", v=2),
            ))
            await sink_a.hit.wait()  # A cerró SU incarnación, locks en mano
            task_b = asyncio.create_task(run(
                sink_b, scope_b,
                _listing("b1", company="Zombo Corp", url="https://x/shared", v=2),
            ))
            await asyncio.sleep(0.3)  # margen para que B alcance el lock
            # SERIALIZACIÓN: B no ha podido llegar a su cierre — está esperando
            # el lock de vacante que A retiene (sin el fix #2, la archivada no
            # se bloqueaba y B pasaba de largo: este assert falla).
            assert not sink_b.hit.is_set()
            sink_a.resume.set()
            await task_a
            await sink_b.hit.wait()  # ahora B tiene los locks y cerró la suya
            sink_b.resume.set()
            await task_b

        asyncio.run(race())

        rows = _one(
            factory,
            "SELECT v.primary_incarnation_id, i.ended_at "
            "FROM vacancies v JOIN source_listing_incarnations i "
            "ON i.id = v.primary_incarnation_id WHERE v.id = :v",
            v=v_shared,
        )
        assert rows[0].ended_at is None  # primary = incarnación ACTIVA
        assert rows[0].primary_incarnation_id == c1_inc.id  # la de C
    finally:
        identity_mod._EXTRACTORS.pop("otherboard", None)


def test_three_source_concurrent_recycle_repairs_primary_under_lock(db):
    """Rev. A-05 2ª #2 (repro): A y B reciclan CONCURRENTEMENTE su incarnación
    de una vacante compartida por TRES fuentes; C sigue activa. El lock por
    vacante serializa cierre+reparación: el primary final apunta a la única
    incarnación ACTIVA (la de C), nunca a un cierre sin confirmar."""
    from jobhunt_core.harvest import identity as identity_mod

    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    scope_c = _seed(factory, created, "thirdboard")
    identity_mod.register_extractor(
        "otherboard", lambda p: (p.get("title"), p.get("company_name"))
    )
    try:
        _sink(factory, scope_a, [_listing("a1", company="ACME AG", url="https://x/shared", v=1)])
        _sink(factory, scope_b, [_listing("b1", company="ACME AG", url="https://x/shared", v=1)])
        _sink(factory, scope_c, [_listing("c1", url="https://x/shared", v=1)])
        v_shared = _incs(factory, "a1")[0].vacancy_id
        c1_inc = _incs(factory, "c1")[0]
        assert c1_inc.vacancy_id == v_shared  # compartida por las tres

        async def worker(scope_id, listing):
            async with factory() as s:
                await RawListingSink().handle(s, scope_id, (listing,))
                await s.commit()

        async def race():
            await asyncio.gather(
                worker(scope_a, _listing("a1", company="Umbrella GmbH", url="https://x/shared", v=2)),
                worker(scope_b, _listing("b1", company="Zombo Corp", url="https://x/shared", v=2)),
            )

        asyncio.run(race())

        rows = _one(
            factory,
            "SELECT v.primary_incarnation_id, i.ended_at "
            "FROM vacancies v JOIN source_listing_incarnations i "
            "ON i.id = v.primary_incarnation_id WHERE v.id = :v",
            v=v_shared,
        )
        assert rows[0].ended_at is None  # primary = incarnación ACTIVA
        assert rows[0].primary_incarnation_id == c1_inc.id  # la de C
        activas = _one(
            factory,
            "SELECT count(*) AS n FROM source_listing_incarnations "
            "WHERE vacancy_id = :v AND ended_at IS NULL",
            v=v_shared,
        )
        assert activas[0].n == 1
    finally:
        identity_mod._EXTRACTORS.pop("otherboard", None)


def test_candidate_similarity_keeps_maximum_and_respects_resolution(db):
    """Rev. A-05 2ª #3: la similitud registrada no depende del orden de
    llegada — gana el MÁXIMO en el lote y GREATEST en BD, SOLO mientras el
    candidato siga pending; un candidato resuelto no se toca."""
    factory, created = db
    va, vb = uuid.uuid4(), uuid.uuid4()
    created["extra_vacs"] += [va, vb]

    async def setup():
        async with factory() as s:
            await s.execute(
                sa.text("INSERT INTO vacancies (id) VALUES (:id)"),
                [{"id": va}, {"id": vb}],
            )
            await s.commit()

    asyncio.run(setup())
    sink = RawListingSink()

    def write(pairs):
        async def go():
            async with factory() as s:
                await sink._write_dedup_candidates(s, pairs)
                await s.commit()

        asyncio.run(go())

    def sim_state():
        r = _one(
            factory,
            "SELECT similarity, state FROM dedup_candidates "
            "WHERE vacancy_a IN (:a, :b) AND vacancy_b IN (:a, :b)",
            a=va, b=vb,
        )
        return float(r[0].similarity), r[0].state

    # Lote con el MISMO par en ambos órdenes y sims distintas → gana el máximo.
    write([{"a": va, "b": vb, "sim": 0.85}, {"a": vb, "b": va, "sim": 0.9}])
    assert sim_state() == (0.9, "pending")
    write([{"a": va, "b": vb, "sim": 0.95}])  # BD: GREATEST sube
    assert sim_state() == (0.95, "pending")
    write([{"a": va, "b": vb, "sim": 0.5}])  # menor: se conserva
    assert sim_state() == (0.95, "pending")

    async def resolve():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE dedup_candidates SET state = 'confirmed', "
                    "resolved_by = 'test', resolved_at = now() "
                    "WHERE vacancy_a IN (:a, :b) AND vacancy_b IN (:a, :b)"
                ),
                {"a": va, "b": vb},
            )
            await s.commit()

    asyncio.run(resolve())
    write([{"a": va, "b": vb, "sim": 0.99}])  # resuelto: NO se toca
    assert sim_state() == (0.95, "confirmed")


def test_intra_batch_fuzzy_duplicates_become_candidates_not_one_vacancy(db):
    """Medio difuso intra-lote (PF.5): mismo título/empresa normalizados con
    URLs y external_id distintos → DOS vacantes + candidato pending. No se
    funde por semántica sola (la resolución es de Fase B)."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(
        factory, scope,
        [
            _listing("x1", title="Python Dev (m/w/d)", company="ACME AG", url="https://x/1"),
            _listing("x2", title="Senior Python Dev", company="Acme GmbH", url="https://x/2"),
        ],
    )
    v1 = _incs(factory, "x1")[0].vacancy_id
    v2 = _incs(factory, "x2")[0].vacancy_id
    assert v1 != v2  # vacantes SEPARADAS
    cands = _one(
        factory,
        "SELECT similarity, state FROM dedup_candidates "
        "WHERE vacancy_a IN (:a, :b) AND vacancy_b IN (:a, :b)",
        a=v1, b=v2,
    )
    assert [(float(r.similarity), r.state) for r in cands] == [(0.85, "pending")]
