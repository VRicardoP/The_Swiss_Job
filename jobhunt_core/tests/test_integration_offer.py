"""Revisión canónica + embeddings por text_hash (A-06) contra Postgres real.

DoD: embedding por text_hash con concurrencia optimista; cambiar salario/
location NO re-embebe; el puntero vigente sigue al contenido del PRIMARY;
las demás fuentes se agregan sin mover el puntero. Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import jobhunt_core.harvest.providers  # noqa: F401 — registra extractor/normalizador
from jobhunt_core import embeddings
from jobhunt_core.config import settings
from jobhunt_core.harvest.normalize import offer_text_hash
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


class FakeBackend:
    """Backend determinista sin ML: 384 dims, vector distinto por texto."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def encode_batch(self, texts):
        self.calls.append(list(texts))
        return [[((hash(t) % 97) / 97.0)] * embeddings.EMBED_DIM for t in texts]


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"sources": [], "scopes": [], "models": []}
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
            if vac_ids:
                await s.execute(
                    sa.text(
                        "DELETE FROM dedup_candidates "
                        "WHERE vacancy_a = ANY(:v) OR vacancy_b = ANY(:v)"
                    ),
                    {"v": vac_ids},
                )
                # El puntero FK-compuesto bloquea el borrado de revisiones.
                await s.execute(
                    sa.text(
                        "UPDATE vacancies SET current_offer_revision_id = NULL "
                        "WHERE id = ANY(:v)"
                    ),
                    {"v": vac_ids},
                )
                await s.execute(
                    sa.text("DELETE FROM offer_revision_sources WHERE vacancy_id = ANY(:v)"),
                    {"v": vac_ids},
                )
                await s.execute(
                    sa.text("DELETE FROM offer_revisions WHERE vacancy_id = ANY(:v)"),
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
            for sid in created["scopes"]:
                await s.execute(
                    sa.text("DELETE FROM source_scope_state WHERE scope_id=:i"), {"i": sid}
                )
                await s.execute(sa.text("DELETE FROM harvest_scopes WHERE id=:i"), {"i": sid})
            await s.execute(
                sa.text("DELETE FROM sources WHERE id = ANY(:srcs)"), {"srcs": srcs}
            )
            for mid in created["models"]:
                await s.execute(
                    sa.text("DELETE FROM offer_embeddings WHERE model_id = :m"), {"m": mid}
                )
                await s.execute(
                    sa.text(
                        f"DROP TABLE IF EXISTS {settings.CORE_DB_SCHEMA}."
                        f"offer_embeddings_{mid.hex[:16]}"
                    )
                )
                await s.execute(
                    sa.text("DELETE FROM embedding_models WHERE id = :m"), {"m": mid}
                )
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


def _seed(factory, created, name) -> str:
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


def _listing(ext, title=None, company="ACME AG", desc="backend dev", tags=None,
             location="Zurich", url=None):
    payload = {
        "title": title or ext, "company_name": company, "description": desc,
        "tags": tags or ["python"], "location": location,
    }
    return RawListing(external_id=ext, url=url or f"https://x/{ext}", payload=payload)


def _rows(factory, sql, **params):
    async def go():
        async with factory() as s:
            return (await s.execute(sa.text(sql), params)).all()

    return asyncio.run(go())


def _vacancy_state(factory, ext):
    return _rows(
        factory,
        "SELECT v.id AS vac, v.current_offer_revision_id AS cur, o.content_hash, "
        "o.text_hash, o.content "
        "FROM source_listings l "
        "JOIN source_listing_incarnations i ON i.source_listing_id = l.id "
        " AND i.ended_at IS NULL "
        "JOIN vacancies v ON v.id = i.vacancy_id "
        "LEFT JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
        "WHERE l.external_id = :ext",
        ext=ext,
    )[0]


def _register(factory, created, name="modelo-test", version="1"):
    async def go():
        async with factory() as s:
            mid = await embeddings.register_model(s, name, version)
            await s.commit()
            return mid

    mid = asyncio.run(go())
    if mid not in created["models"]:
        created["models"].append(mid)
    return mid


def test_primary_revision_creates_canonical_and_pointer(db):
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", title="Python Dev", tags=["py", "sql"])])

    st = _vacancy_state(factory, "j1")
    assert st.cur is not None
    assert st.content["title"] == "Python Dev"
    assert st.content["company"] == "ACME AG"
    assert st.content["tags"] == ["py", "sql"]
    assert st.text_hash == offer_text_hash(st.content)
    # content_hash de la canónica = el del raw del primary.
    raw_ch = _rows(
        factory,
        "SELECT r.content_hash FROM source_listing_revisions r "
        "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.external_id = 'j1'",
    )
    assert st.content_hash == raw_ch[0][0]
    # La revisión raw quedó agregada como fuente de la canónica.
    srcs = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_revision_sources WHERE offer_revision_id = :o",
        o=st.cur,
    )
    assert srcs[0].n == 1


def test_location_change_new_revision_same_text_hash(db):
    """ADR-02: cambiar salario/location da OTRA revisión (content_hash nuevo)
    con el MISMO text_hash → el embedding se reutiliza (no re-embebe)."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", location="Zurich")])
    st1 = _vacancy_state(factory, "j1")
    _sink(factory, scope, [_listing("j1", location="Ginebra")])
    st2 = _vacancy_state(factory, "j1")
    assert st2.cur != st1.cur  # revisión NUEVA (puntero movido)
    assert st2.text_hash == st1.text_hash  # MISMO texto embebible
    n = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_revisions WHERE vacancy_id = :v", v=st1.vac,
    )
    assert n[0].n == 2


def test_revert_to_previous_content_repoints_without_new_revision(db):
    """Auto-reparador: contenido que REVIERTE a un hash histórico re-apunta a
    la revisión existente sin crear otra."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", title="v1")])
    st1 = _vacancy_state(factory, "j1")
    _sink(factory, scope, [_listing("j1", title="v2")])
    _sink(factory, scope, [_listing("j1", title="v1")])  # vuelve al contenido v1
    st3 = _vacancy_state(factory, "j1")
    assert st3.cur == st1.cur  # re-apuntada a la revisión ORIGINAL
    n = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_revisions WHERE vacancy_id = :v", v=st1.vac,
    )
    assert n[0].n == 2  # v1 y v2, sin terceras copias


def test_non_primary_source_aggregates_without_moving_pointer(db):
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    _sink(factory, scope_a, [_listing("a1", url="https://x/shared")])
    st = _vacancy_state(factory, "a1")
    _sink(factory, scope_b, [_listing("b1", url="https://x/shared")])  # attach

    st_after = _vacancy_state(factory, "a1")
    assert st_after.cur == st.cur  # el puntero NO se mueve por otra fuente
    # La revisión raw de b1 quedó agregada a la canónica vigente.
    rows = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_revision_sources ors "
        "JOIN source_listing_revisions r ON r.id = ors.source_listing_revision_id "
        "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE ors.offer_revision_id = :o AND l.external_id = 'b1'",
        o=st.cur,
    )
    assert rows[0].n == 1
    # b1 (no primario, fuente sin normalizador) no creó revisión canónica.
    n = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_revisions WHERE vacancy_id = :v", v=st.vac,
    )
    assert n[0].n == 1


def test_register_model_idempotent_partition_and_dim_guard(db):
    factory, created = db
    mid1 = _register(factory, created)
    mid2 = _register(factory, created)
    assert mid1 == mid2  # idempotente
    part = _rows(
        factory,
        "SELECT count(*) AS n FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = :s AND c.relname = :p",
        s=settings.CORE_DB_SCHEMA, p=f"offer_embeddings_{mid1.hex[:16]}",
    )
    assert part[0].n == 1  # la partición del modelo existe

    async def bad_dim():
        async with factory() as s:
            await embeddings.register_model(s, "otro", "1", dim=512)

    with pytest.raises(ValueError, match="expand/contract"):
        asyncio.run(bad_dim())


def test_embedding_task_by_text_hash_no_reembed_and_optimistic(db):
    """DoD A-06: embedding por text_hash; cambiar location NO re-embebe;
    escritura optimista (doble store → una fila)."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1"), _listing("j2", title="Data Eng")])
    mid = _register(factory, created)

    fake = FakeBackend()
    embeddings.set_backend_factory(lambda name, version: fake)
    try:
        r1 = run_pending_task.apply(kwargs={"limit": 50})
        assert r1.successful()
        assert r1.result["embedded"]["modelo-test/1"] == 2

        # location cambia → revisión nueva, MISMO text_hash → nada pendiente.
        _sink(factory, scope, [_listing("j1", location="Basilea")])
        r2 = run_pending_task.apply(kwargs={"limit": 50})
        assert r2.result["embedded"]["modelo-test/1"] == 0

        # título cambia → text_hash nuevo → un embedding más.
        _sink(factory, scope, [_listing("j2", title="Data Engineer Sr")])
        r3 = run_pending_task.apply(kwargs={"limit": 50})
        assert r3.result["embedded"]["modelo-test/1"] == 1
    finally:
        embeddings.set_backend_factory(None)

    # Optimista: el MISMO (text_hash, model) dos veces → una sola fila.
    th = _vacancy_state(factory, "j1").text_hash

    async def double_store():
        async with factory() as s:
            n1 = await embeddings.store_offer_embeddings(
                s, mid, [{"text_hash": th, "vector": [0.5] * embeddings.EMBED_DIM}]
            )
            await s.commit()
            return n1

    assert asyncio.run(double_store()) == 0  # ya existía (la insertó la tarea)
    rows = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_embeddings WHERE text_hash = :t AND model_id = :m",
        t=th, m=mid,
    )
    assert rows[0].n == 1


def test_self_repaired_canonical_links_its_source(db):
    """Auditoría A-06 #1 (repro): la normalización falla en el run 1 (raw
    persistido, sin canónica) y se corrige antes del run 2 CON EL MISMO raw —
    el auto-reparador crea la canónica y debe enlazar TAMBIÉN su revisión raw
    (no-fresh) en offer_revision_sources; sin el fix quedaba sin fuente para
    siempre."""
    from jobhunt_core.harvest import normalize as normalize_mod

    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    real = normalize_mod._NORMALIZERS["arbeitnow"]
    normalize_mod.register_normalizer("arbeitnow", lambda raw: {"title": None})
    try:
        _sink(factory, scope, [_listing("j1")])  # run 1: normaliza a None
        st1 = _vacancy_state(factory, "j1")
        assert st1.cur is None  # sin canónica, puntero intacto (sin reventar)
    finally:
        normalize_mod.register_normalizer("arbeitnow", real)

    _sink(factory, scope, [_listing("j1")])  # run 2: MISMO raw, normalizador ok
    st2 = _vacancy_state(factory, "j1")
    assert st2.cur is not None  # auto-reparado
    srcs = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_revision_sources WHERE offer_revision_id = :o",
        o=st2.cur,
    )
    assert srcs[0].n == 1  # la revisión raw del run 1 quedó ENLAZADA


def test_failed_primary_normalization_does_not_poison_batch(db):
    """Auditoría A-06 #2: un PRIMARY no-normalizable (sin título) dentro de un
    lote con válidos NO aborta nada — el válido obtiene su canónica y puntero;
    el fallido queda con puntero NULL y su revisión raw persistida."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    sin_titulo = RawListing(
        external_id="malo", url="https://x/malo",
        payload={"company_name": "ACME", "description": "sin title"},
    )
    _sink(factory, scope, [_listing("bueno"), sin_titulo])  # sin excepción

    assert _vacancy_state(factory, "bueno").cur is not None
    st_malo = _vacancy_state(factory, "malo")
    assert st_malo.cur is None  # sin canónica: puntero intacto
    raws = _rows(
        factory,
        "SELECT count(*) AS n FROM source_listing_revisions r "
        "JOIN source_listing_incarnations i ON i.id = r.incarnation_id "
        "JOIN source_listings l ON l.id = i.source_listing_id "
        "WHERE l.external_id = 'malo'",
    )
    assert raws[0].n == 1  # el raw SÍ se persistió (raw antes de normalizar)


def test_task_skips_model_with_wrong_dimension(db):
    """Auditoría A-06 #3: un modelo activo con dim!=384 colado por otra vía se
    SALTA con error logueado — solo embebe el modelo 384, sin excepción."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1")])
    _register(factory, created)  # modelo 384 legítimo

    async def rogue_model():
        async with factory() as s:
            mid = uuid.uuid4()
            created["models"].append(mid)
            await s.execute(
                sa.text(
                    "INSERT INTO embedding_models (id, name, version, dim, active) "
                    "VALUES (:id, 'rogue', '1', 512, TRUE)"
                ),
                {"id": mid},
            )
            await s.commit()

    asyncio.run(rogue_model())
    fake = FakeBackend()
    embeddings.set_backend_factory(lambda name, version: fake)
    try:
        r = run_pending_task.apply(kwargs={"limit": 50})
        assert r.successful()  # sin excepción
        assert r.result["embedded"] == {"modelo-test/1": 1}  # rogue SALTADO
    finally:
        embeddings.set_backend_factory(None)


def test_recycled_shared_primary_rebuilds_canonical_from_new_primary(db):
    """Rev. A-06 2ª #1 (repro): V compartida (A primary + B attach, ambos con
    normalizador); A recicla → el primary pasa a B y la CANÓNICA se
    reconstruye desde el último raw de B (con el normalizador de SU fuente) —
    jamás queda sirviéndose el contenido de A."""
    from jobhunt_core.harvest import identity as identity_mod
    from jobhunt_core.harvest import normalize as normalize_mod

    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")
    identity_mod.register_extractor(
        "otherboard", lambda p: (p.get("title"), p.get("company_name"))
    )
    normalize_mod.register_normalizer(
        "otherboard",
        lambda raw: {
            "title": raw.get("title"), "company": raw.get("company_name"),
            "description": raw.get("description"), "tags": raw.get("tags"),
            "location": raw.get("location"), "remote": None, "salary": None,
        },
    )
    try:
        _sink(factory, scope_a, [_listing("a1", title="Contenido de A", url="https://x/shared")])
        _sink(factory, scope_b, [_listing("b1", title="Contenido de B", url="https://x/shared")])
        v_shared = _vacancy_state(factory, "b1").vac
        # A recicla (empresa distinta): el primary pasa a B.
        _sink(factory, scope_a, [_listing("a1", title="Otra", company="Umbrella GmbH", url="https://x/shared")])
        row = _rows(
            factory,
            "SELECT o.content->>'title' AS title, i.ended_at "
            "FROM vacancies v "
            "LEFT JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
            "LEFT JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
            "WHERE v.id = :v", v=v_shared,
        )[0]
        assert row.ended_at is None  # primary ACTIVO (el de B)
        assert row.title == "Contenido de B"  # canónica RECONSTRUIDA desde B
    finally:
        identity_mod._EXTRACTORS.pop("otherboard", None)
        normalize_mod._NORMALIZERS.pop("otherboard", None)


def test_recycled_shared_primary_without_normalizer_nulls_pointer(db):
    """Rev. A-06 2ª #1 variante: el NUEVO primary (B) no tiene normalizador →
    puntero NULL — nunca el contenido del primary anterior."""
    factory, created = db
    scope_a = _seed(factory, created, "arbeitnow")
    scope_b = _seed(factory, created, "otherboard")  # SIN normalizador
    _sink(factory, scope_a, [_listing("a1", title="Contenido de A", url="https://x/shared")])
    _sink(factory, scope_b, [_listing("b1", title="B crudo", url="https://x/shared")])
    v_shared = _vacancy_state(factory, "b1").vac
    _sink(factory, scope_a, [_listing("a1", title="Otra", company="Umbrella GmbH", url="https://x/shared")])
    row = _rows(
        factory,
        "SELECT current_offer_revision_id AS cur FROM vacancies WHERE id = :v",
        v=v_shared,
    )[0]
    assert row.cur is None  # sin normalizador para B: NULL


def test_primary_content_turns_invalid_then_valid_pointer_follows(db):
    """Rev. A-06 2ª #2 (repro): válido → contenido NUEVO sin título (raw
    persistido, canónica imposible) → puntero NULL (la anterior no puede
    seguir sirviéndose mientras last_seen se refresca); → revert al contenido
    válido → puntero RESTAURADO (auto-reparador)."""
    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1", title="Válido")])
    st1 = _vacancy_state(factory, "j1")
    assert st1.cur is not None
    sin_titulo = RawListing(
        external_id="j1", url="https://x/j1",
        payload={"company_name": "ACME AG", "description": "sin title", "v": 2},
    )
    _sink(factory, scope, [sin_titulo])
    st2 = _vacancy_state(factory, "j1")
    assert st2.cur is None  # CAS a NULL condicionado al primary
    _sink(factory, scope, [_listing("j1", title="Válido")])  # revert
    st3 = _vacancy_state(factory, "j1")
    assert st3.cur == st1.cur  # restaurado a la revisión original


def test_two_active_models_use_distinct_backends(db):
    """Rev. A-06 2ª #3 (repro): dos modelos activos → CADA uno resuelve su
    backend por (name, version) y almacena SU vector — jamás un encoder
    global compartido bajo model_ids que dicen ser modelos distintos."""
    from jobhunt_core.tasks.embedding import run_pending_task

    factory, created = db
    scope = _seed(factory, created, "arbeitnow")
    _sink(factory, scope, [_listing("j1")])
    _register(factory, created, name="modelo-uno", version="1")
    _register(factory, created, name="modelo-dos", version="7")

    seen: list[tuple[str, str]] = []

    class PerModelBackend:
        def __init__(self, name, version):
            self._fill = 0.1 if name == "modelo-uno" else 0.9

        def encode_batch(self, texts):
            return [[self._fill] * embeddings.EMBED_DIM for _ in texts]

    def factory_fn(name, version):
        seen.append((name, version))
        return PerModelBackend(name, version)

    embeddings.set_backend_factory(factory_fn)
    try:
        r = run_pending_task.apply(kwargs={"limit": 50})
        assert r.successful()
        assert r.result["embedded"] == {"modelo-uno/1": 1, "modelo-dos/7": 1}
    finally:
        embeddings.set_backend_factory(None)
    assert sorted(seen) == [("modelo-dos", "7"), ("modelo-uno", "1")]
    vecs = _rows(
        factory,
        "SELECT DISTINCT vector::text AS v FROM offer_embeddings "
        "WHERE model_id = ANY(:ms)", ms=created["models"][-2:],
    )
    assert len(vecs) == 2  # vectores DISTINTOS por modelo


def test_register_model_rejects_existing_dim_mismatch_and_updates_active(db):
    """Rev. A-06 2ª #4: tras el DO NOTHING se valida la fila EXISTENTE — una
    fila previa de 512 dims jamás da éxito; `active` SÍ se actualiza al
    re-declarar (registro = declaración operativa idempotente)."""
    factory, created = db

    async def rogue():
        async with factory() as s:
            mid = uuid.uuid4()
            created["models"].append(mid)
            await s.execute(
                sa.text(
                    "INSERT INTO embedding_models (id, name, version, dim, active) "
                    "VALUES (:id, 'legacy512', '1', 512, TRUE)"
                ),
                {"id": mid},
            )
            await s.commit()

    asyncio.run(rogue())

    async def re_register():
        async with factory() as s:
            await embeddings.register_model(s, "legacy512", "1", dim=384)

    with pytest.raises(ValueError, match="INMUTABLE"):
        asyncio.run(re_register())

    mid = _register(factory, created)  # modelo-test, active=True

    async def deactivate():
        async with factory() as s:
            await embeddings.register_model(s, "modelo-test", "1", active=False)
            await s.commit()

    asyncio.run(deactivate())
    row = _rows(factory, "SELECT active FROM embedding_models WHERE id = :m", m=mid)
    assert row[0].active is False


def test_concurrent_stores_same_hash_single_row(db):
    """Concurrencia optimista real: dos sesiones insertan el mismo
    (text_hash, model) a la vez → exactamente una fila, sin error."""
    factory, created = db
    _seed(factory, created, "arbeitnow")
    mid = _register(factory, created)
    th = "f" * 64

    async def store():
        async with factory() as s:
            await embeddings.store_offer_embeddings(
                s, mid, [{"text_hash": th, "vector": [0.25] * embeddings.EMBED_DIM}]
            )
            await s.commit()

    async def race():
        await asyncio.gather(store(), store())

    asyncio.run(race())
    rows = _rows(
        factory,
        "SELECT count(*) AS n FROM offer_embeddings WHERE text_hash = :t AND model_id = :m",
        t=th, m=mid,
    )
    assert rows[0].n == 1
