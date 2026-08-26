"""Re-mapeo de los job_ref del core al hash canónico (shadow/canonical_refs).

La otra mitad del PASO 7c de la canonización de identidad legacy: los scripts
reescriben `jobs.hash` y reapuntan `jobhunt.source_listings.external_id`, pero
las etiquetas del oráculo —`labeled_judgments.job_ref` y
`labeled_dedup_pairs.job_ref_a/b`— viven en ese mismo espacio de nombres, no
tienen FK y NINGÚN paso las toca. `map_job_refs_to_vacancies` deja fuera del
dict los refs sin slot SIN error, así que la ruptura es silenciosa.

Esquema legacy DESECHABLE (canon_fx_<hex>) — estos tests JAMÁS tocan `public`.
Ejecutar vía core-migrate.
"""

import asyncio
import hashlib
import os
import re
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core import profiles
from jobhunt_core.config import settings
from jobhunt_core.shadow import canonical_refs, labels
from jobhunt_core.tests import dbcleanup

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


def _hash(title, company, url) -> str:
    """BaseJobProvider.compute_hash, que es la expresión que el módulo usa
    para reconstruir el hash VIEJO desde los campos que los scripts NO tocan."""
    return hashlib.md5(
        f"{title.strip().lower()}|{company.strip().lower()}|{url}".encode()
    ).hexdigest()


@pytest.fixture()
def db():
    engine = create_async_engine(settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    created = {"consumers": [], "sets": [], "dedup_refs": [], "sources": [], "scopes": []}
    yield factory, created

    async def cleanup():
        async with factory() as s:
            await dbcleanup.purge_shadow(s, created["sets"], created["dedup_refs"])
            await dbcleanup.purge_consumer_graph(s, created["consumers"])
            await dbcleanup.purge_source_graph(s, created["sources"], created["scopes"])
            await s.commit()
        await engine.dispose()

    asyncio.run(cleanup())


@pytest.fixture()
def legacy_fx():
    """Esquema legacy desechable con las columnas que la reconstrucción del
    mapa necesita (`title`, `company`, `url`: las que los scripts NO tocan)."""
    admin_url = os.environ["CORE_ADMIN_DATABASE_URL"].replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    parts = urlsplit(admin_url)
    admin_url = urlunsplit(
        (parts.scheme, parts.netloc, urlsplit(settings.CORE_DATABASE_URL).path, "", "")
    )
    schema = f"canon_fx_{uuid.uuid4().hex[:10]}"
    core_role = urlsplit(settings.CORE_DATABASE_URL).username
    assert re.fullmatch(r"[a-z_][a-z0-9_]*", core_role)
    admin_engine = create_async_engine(admin_url, poolclass=sa.pool.NullPool)

    async def create():
        async with admin_engine.begin() as c:
            await c.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
            await c.execute(
                sa.text(
                    f"CREATE TABLE {schema}.jobs ("
                    f"hash varchar(32) PRIMARY KEY, title text NOT NULL, "
                    f"company text NOT NULL, url text NOT NULL, "
                    f"duplicate_of varchar(32), is_active boolean NOT NULL DEFAULT true)"
                )
            )
            await c.execute(sa.text(f'GRANT USAGE ON SCHEMA "{schema}" TO {core_role}'))
            await c.execute(
                sa.text(f'GRANT SELECT ON ALL TABLES IN SCHEMA "{schema}" TO {core_role}')
            )

    asyncio.run(create())
    yield admin_engine, schema

    async def drop():
        async with admin_engine.begin() as c:
            await c.execute(sa.text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()

    asyncio.run(drop())


def _run(coro):
    return asyncio.run(coro)


def _legacy_insert(admin_engine, schema, rows):
    async def go():
        async with admin_engine.begin() as c:
            for r in rows:
                await c.execute(
                    sa.text(
                        f"INSERT INTO {schema}.jobs (hash, title, company, url) "
                        f"VALUES (:h, :t, :c, :u)"
                    ),
                    r,
                )

    asyncio.run(go())


def _mk_core_corpus(factory, created, refs, registrar=True):
    """Una fuente `legacy:*` con un slot por ref y su vacante primaria."""
    src = uuid.uuid4()
    created["sources"].append(src)
    vac = {}

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text("INSERT INTO sources (id, name, tier) VALUES (:i, :n, 0)"),
                {"i": src, "n": f"legacy:canonfx{uuid.uuid4().hex[:6]}"},
            )
            for ref in refs:
                vid, lid, iid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
                await s.execute(
                    sa.text("INSERT INTO vacancies (id) VALUES (:i)"), {"i": vid}
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listings "
                        "(id, source_id, external_id, url_normalized) "
                        "VALUES (:i, :s, :e, :u)"
                    ),
                    {"i": lid, "s": src, "e": ref, "u": f"https://fx/{ref}"},
                )
                await s.execute(
                    sa.text(
                        "INSERT INTO source_listing_incarnations "
                        "(id, source_listing_id, vacancy_id, seq, url) "
                        "VALUES (:i, :l, :v, 1, :u)"
                    ),
                    {"i": iid, "l": lid, "v": vid, "u": f"https://fx/{ref}/1"},
                )
                await s.execute(
                    sa.text(
                        "UPDATE vacancies SET primary_incarnation_id = :p WHERE id = :i"
                    ),
                    {"p": iid, "i": vid},
                )
                vac[ref] = str(vid)
            await s.commit()

    _run(go())
    if registrar:
        created["dedup_refs"] += list(refs)
    return src, vac


def _aplicar_paso_7c(factory, src, old_a_new):
    """Lo que hace el PASO 7c: reapunta el slot de la sombra al hash canónico."""

    async def go():
        async with factory() as s:
            for viejo, nuevo in old_a_new.items():
                await s.execute(
                    sa.text(
                        "UPDATE source_listings SET external_id = :n "
                        "WHERE source_id = :s AND external_id = :v"
                    ),
                    {"n": nuevo, "s": src, "v": viejo},
                )
            await s.commit()

    _run(go())


def _resuelven(factory, refs):
    async def go():
        async with factory() as s:
            return await labels.map_job_refs_to_vacancies(s, sorted(refs))

    return _run(go())


def _remap(factory, schema, **kw):
    async def go():
        async with factory() as s:
            resumen = await canonical_refs.remap_canonical_refs(
                s, legacy_schema=schema, **kw
            )
            await s.commit()
            return resumen

    return _run(go())


def _corpus_canonizado(factory, created, admin_engine, schema, n=3, registrar=True):
    """Estado POSTERIOR a la maniobra legacy: `jobs.hash` ya es el canónico,
    `url` intacta (los scripts no la tocan) y el slot ya reapuntado por 7c.
    Devuelve (src, viejos, nuevos, old_a_new)."""
    viejos, nuevos, filas = [], [], []
    sal = uuid.uuid4().hex[:8]   # cada corpus es único: `jobs.hash` es PK
    for i in range(n):
        titulo, empresa = f"Ingeniera {sal}-{i}", f"ACME {sal}-{i}"
        url = f"https://arbeitnow.com/view/{sal}-x{i}-{1000 + i}"
        url_canon = f"https://arbeitnow.com/view/{sal}-x{i}"
        viejos.append(_hash(titulo, empresa, url))
        nuevos.append(_hash(titulo, empresa, url_canon))
        # OJO: la fila legacy ya lleva el hash NUEVO y la url VIEJA — es
        # exactamente lo que deja el PASO 7, y lo que hace reconstruible el mapa.
        filas.append({"h": nuevos[-1], "t": titulo, "c": empresa, "u": url})
    _legacy_insert(admin_engine, schema, filas)
    src, _vac = _mk_core_corpus(factory, created, viejos, registrar=registrar)
    old_a_new = dict(zip(viejos, nuevos))
    _aplicar_paso_7c(factory, src, old_a_new)
    if registrar:
        created["dedup_refs"] += nuevos
    return src, viejos, nuevos, old_a_new


def _mk_set_con_juicios(factory, created, refs):
    async def go():
        async with factory() as s:
            cid = await profiles.ensure_consumer(
                s, f"shadow-canon-{uuid.uuid4().hex[:8]}"
            )
            pid = await profiles.upsert_profile(s, cid, str(uuid.uuid4()))
            sid = await labels.create_set(s, pid, "ronda-canon")
            for i, ref in enumerate(refs):
                await s.execute(
                    sa.text(
                        "INSERT INTO labeled_judgments "
                        "(set_id, job_ref, relevance, source) "
                        "VALUES (:s, :r, :rel, 'manual')"
                    ),
                    {"s": sid, "r": ref, "rel": 3 - i % 4},
                )
            await s.commit()
            return cid, sid

    cid, sid = _run(go())
    created["consumers"].append(cid)
    created["sets"].append(sid)
    return sid


def _mk_pares(factory, created, parejas, cohorte):
    async def go():
        async with factory() as s:
            for a, b in parejas:
                await s.execute(
                    sa.text(
                        "INSERT INTO labeled_dedup_pairs "
                        "(job_ref_a, job_ref_b, verdict, source) "
                        "VALUES (LEAST(:a,:b), GREATEST(:a,:b), 'duplicate', :src)"
                    ),
                    {"a": a, "b": b, "src": cohorte},
                )
            await s.commit()

    _run(go())


def _pares(factory, cohorte):
    async def go():
        async with factory() as s:
            return (
                await s.execute(
                    sa.text(
                        "SELECT job_ref_a, job_ref_b FROM labeled_dedup_pairs "
                        "WHERE source = :s ORDER BY 1, 2"
                    ),
                    {"s": cohorte},
                )
            ).all()

    return _run(go())


def test_sin_remapeo_las_etiquetas_dejan_de_resolver_y_con_el_vuelven(
    db, legacy_fx
):
    """MORDIDA: el PASO 7c arregla la CDC y rompe las ETIQUETAS.

    Tras la maniobra el slot de la sombra lleva el hash canónico y las
    etiquetas siguen con el viejo, así que `map_job_refs_to_vacancies` —que
    por contrato deja fuera del dict los refs sin slot— devuelve MENOS filas
    sin un solo error: ni excepción, ni log, ni métrica en rojo. Medido en
    producción el 2026-08-26: 10 de 91 juicios y 1 de 260 pares mapeables.

    El re-mapeo los devuelve enteros, y la reconstrucción del mapa no duplica
    la lógica de canonización de URL de los scripts: sale de que los scripts
    NO tocan `jobs.url`, así que el hash viejo es `md5(title|company|url)` y
    el nuevo es el `jobs.hash` que quedó."""
    factory, created = db
    admin_engine, schema = legacy_fx
    _src, viejos, nuevos, _m = _corpus_canonizado(factory, created, admin_engine, schema)
    sid = _mk_set_con_juicios(factory, created, viejos)
    cohorte = f"canon-{uuid.uuid4().hex[:8]}"
    _mk_pares(factory, created, [(viejos[0], viejos[1])], cohorte)

    # (1) Tras 7c y ANTES del re-mapeo: NINGUNA etiqueta resuelve.
    assert _resuelven(factory, viejos) == {}, (
        "el PASO 7c reapuntó los slots y las etiquetas se quedaron con el "
        "hash viejo: deberían haber dejado de resolver"
    )

    # (2) El re-mapeo las devuelve.
    resumen = _remap(factory, schema)
    assert resumen["juicios_remapeados"] == len(viejos), resumen
    assert resumen["pares_remapeados"] == 1, resumen
    de_vuelta = _resuelven(factory, nuevos)
    assert sorted(de_vuelta) == sorted(nuevos), (
        f"tras el re-mapeo deberían resolver los {len(nuevos)} refs canónicos"
    )
    assert _resuelven(factory, viejos) == {}

    # (3) Los juicios son los MISMOS juicios: solo cambió la clave.
    filas = _run(_juicios(factory, sid))
    assert len(filas) == len(viejos)
    assert sorted(r.job_ref for r in filas) == sorted(nuevos)
    assert sorted(r.relevance for r in filas) == sorted(3 - i % 4 for i in range(3))
    # Re-normalizado a LEAST/GREATEST, como lo guarda `seed_dedup_pairs`.
    assert _pares(factory, cohorte) == [
        (min(nuevos[0], nuevos[1]), max(nuevos[0], nuevos[1]))
    ]

    # (4) IDEMPOTENTE: re-ejecutarlo no toca nada (los refs ya canónicos no
    # están en el mapa, que se construye de `hash <> md5(...)`).
    otra = _remap(factory, schema)
    assert (otra["juicios_remapeados"], otra["pares_remapeados"]) == (0, 0), otra
    assert sorted(_resuelven(factory, nuevos)) == sorted(nuevos)


async def _juicios(factory, sid):
    async with factory() as s:
        return (
            await s.execute(
                sa.text(
                    "SELECT job_ref, relevance FROM labeled_judgments "
                    "WHERE set_id = :s"
                ),
                {"s": sid},
            )
        ).all()


def test_el_dry_run_mide_y_no_escribe(db, legacy_fx):
    """El operador tiene que poder ver el tamaño de la maniobra antes de
    autorizarla: `--dry-run` cuenta lo mismo y deja los refs intactos."""
    factory, created = db
    admin_engine, schema = legacy_fx
    _src, viejos, _n, _m = _corpus_canonizado(factory, created, admin_engine, schema)
    _mk_set_con_juicios(factory, created, viejos)

    resumen = _remap(factory, schema, dry_run=True)
    assert resumen["dry_run"] is True
    assert resumen["juicios_remapeados"] == len(viejos)
    assert _resuelven(factory, viejos) == {}, "el dry-run no puede haber escrito"


def test_una_cohorte_sellada_solo_bloquea_si_TIENE_pares_a_remapear(db, legacy_fx):
    """El sello de core0025 hace inmutables los pares de la cohorte, así que
    el UPDATE moriría a mitad por el trigger: se comprueba ANTES y se NOMBRA.

    Pero el filtro es «cohortes AFECTADAS», no «cohortes selladas». El plan es
    sellar el holdout, y un guard global convertiría el PRIMER sello en un
    veto permanente sobre cualquier re-mapeo futuro aunque esa cohorte no
    tuviera un solo ref canonizable — que es exactamente el caso de una
    cohorte sellada sobre otras fuentes. Se comprueban las dos direcciones.

    Un sello es IRREVERSIBLE (core0026 prohíbe UPDATE y DELETE), así que las
    cohortes de este test se quedan en la BD desechable de la suite: por eso
    sus refs son únicos y no pueden interferir con ningún otro test."""
    factory, created = db
    admin_engine, schema = legacy_fx
    _src, viejos, _n, _m = _corpus_canonizado(factory, created, admin_engine, schema)
    _mk_set_con_juicios(factory, created, viejos)

    # Los pares de las cohortes SELLADAS no se registran para la limpieza: el
    # trigger de core0025 prohíbe borrarlos, así que se quedan en la BD
    # DESECHABLE de la suite. Sus refs son únicos y no tocan a nadie más.
    # (a) sellada pero SIN pares en el mapa: no bloquea.
    ajena = f"canon-sellada-ajena-{uuid.uuid4().hex[:8]}"
    otro_a, otro_b = uuid.uuid4().hex, uuid.uuid4().hex
    _mk_core_corpus(factory, created, [otro_a, otro_b], registrar=False)
    _mk_pares(factory, created, [(otro_a, otro_b)], ajena)
    _sellar(factory, ajena)
    resumen = _remap(factory, schema)
    assert resumen["juicios_remapeados"] == len(viejos), resumen

    # (b) sellada CON pares en el mapa: aborta nombrándola, sin tocar nada.
    _src2, viejos2, _n2, _m2 = _corpus_canonizado(
        factory, created, admin_engine, schema, n=2, registrar=False
    )
    propia = f"canon-sellada-propia-{uuid.uuid4().hex[:8]}"
    _mk_pares(factory, created, [(viejos2[0], viejos2[1])], propia)
    _sellar(factory, propia)
    with pytest.raises(canonical_refs.FrozenCohortError) as exc:
        _remap(factory, schema)
    assert propia in str(exc.value)
    assert ajena not in str(exc.value)
    assert _resuelven(factory, viejos2) == {}


def _sellar(factory, cohorte):
    """Sello REAL: statement_timestamp() + manifest objeto no vacío (core0026).
    IRREVERSIBLE por diseño — el trigger prohíbe UPDATE y DELETE."""

    async def go():
        async with factory() as s:
            await s.execute(
                sa.text(
                    "INSERT INTO labeled_dedup_cohorts (source, frozen_at, manifest) "
                    "VALUES (:s, statement_timestamp(), CAST(:m AS jsonb))"
                ),
                {"s": cohorte, "m": '{"sha256": "fx"}'},
            )
            await s.commit()

    _run(go())


def test_una_colision_de_juicios_aborta_y_nombra(db, legacy_fx):
    """Si el set YA juzga el hash canónico, el re-mapeo violaría
    UNIQUE(set_id, job_ref) — y «perder» ese juicio en un ON CONFLICT sería
    exactamente el silencio que este módulo existe para cerrar."""
    factory, created = db
    admin_engine, schema = legacy_fx
    _src, viejos, nuevos, _m = _corpus_canonizado(factory, created, admin_engine, schema)
    # El set juzga el viejo Y el canónico del MISMO trabajo.
    _mk_set_con_juicios(factory, created, [viejos[0], nuevos[0]])

    with pytest.raises(ValueError) as exc:
        _remap(factory, schema)
    assert "UNIQUE(set_id, job_ref)" in str(exc.value)
    assert _resuelven(factory, [viejos[0]]) == {}


def test_un_par_que_colapsaria_a_a_igual_b_aborta_y_nombra(db, legacy_fx):
    """Dos lados que canonizan al MISMO hash: `a = b` viola el CHECK. Se
    aborta nombrando el par, nunca se «arregla» borrándolo."""
    factory, created = db
    admin_engine, schema = legacy_fx
    titulo, empresa = "Ingeniera dup", "ACME dup"
    url_a = "https://arbeitnow.com/view/dup-1"
    canon = "https://arbeitnow.com/view/dup"
    viejo_a = _hash(titulo, empresa, url_a)
    nuevo = _hash(titulo, empresa, canon)
    # Un par que nombra el hash VIEJO de una fila y su propio CANÓNICO: es lo
    # que deja un `duplicate_of` sembrado cuando uno de los dos lados ya
    # estaba canonizado. Tras el re-mapeo los dos lados serían el mismo.
    _legacy_insert(admin_engine, schema,
                   [{"h": nuevo, "t": titulo, "c": empresa, "u": url_a}])
    src, _vac = _mk_core_corpus(factory, created, [viejo_a, nuevo])
    _aplicar_paso_7c(factory, src, {})
    cohorte = f"canon-colapso-{uuid.uuid4().hex[:8]}"
    _mk_pares(factory, created, [(viejo_a, nuevo)], cohorte)

    with pytest.raises(ValueError) as exc:
        _remap(factory, schema)
    assert "CHECK" in str(exc.value)
    assert _pares(factory, cohorte) == [
        (min(viejo_a, nuevo), max(viejo_a, nuevo))
    ], "el par no puede haberse tocado"


def _dos_que_invierten_el_orden():
    """Busca dos trabajos cuyo par CAMBIE de orden al canonizar: LEAST(viejos)
    y LEAST(nuevos) tienen que caer en lados distintos. Con md5 pasa la mitad
    de las veces; la búsqueda es determinista (sal incremental) para que el
    test no sea intermitente."""
    for k in range(200):
        filas = []
        for i in (0, 1):
            titulo, empresa = f"Orden {k}-{i}", f"ACME {k}-{i}"
            url = f"https://arbeitnow.com/view/o{k}-{i}-{500 + i}"
            canon = f"https://arbeitnow.com/view/o{k}-{i}"
            filas.append((titulo, empresa, url, _hash(titulo, empresa, url),
                          _hash(titulo, empresa, canon)))
        (_t0, _c0, _u0, v0, n0), (_t1, _c1, _u1, v1, n1) = filas
        if (v0 < v1) != (n0 < n1):
            return filas
    raise AssertionError("no se encontró un par que invierta el orden")


def test_el_remapeo_renormaliza_el_orden_canonico_del_par(db, legacy_fx):
    """`seed_dedup_pairs` guarda SIEMPRE el menor primero (LEAST/GREATEST) y
    el índice de unicidad es de expresión, así que un re-mapeo que dejara
    `a > b` no rompería nada visible — y por eso mismo nadie lo notaría. El
    par canónico no cambia al reordenar, pero la convención del acta sí.

    El corpus es ADVERSARIAL a propósito: se BUSCAN dos trabajos cuyo orden se
    invierta al canonizar. Con un par cualquiera el orden se conserva la mitad
    de las veces y la sonda no probaría nada."""
    factory, created = db
    admin_engine, schema = legacy_fx
    filas = _dos_que_invierten_el_orden()
    _legacy_insert(admin_engine, schema,
                   [{"h": n, "t": t, "c": c, "u": u} for t, c, u, _v, n in filas])
    viejos = [v for _t, _c, _u, v, _n in filas]
    nuevos = [n for _t, _c, _u, _v, n in filas]
    src, _vac = _mk_core_corpus(factory, created, viejos)
    _aplicar_paso_7c(factory, src, dict(zip(viejos, nuevos)))
    created["dedup_refs"] += nuevos
    cohorte = f"canon-orden-{uuid.uuid4().hex[:8]}"
    _mk_pares(factory, created, [(viejos[0], viejos[1])], cohorte)
    assert _pares(factory, cohorte) == [(min(viejos), max(viejos))]

    _remap(factory, schema)
    assert _pares(factory, cohorte) == [(min(nuevos), max(nuevos))], (
        "el re-mapeo dejó el par sin re-normalizar: `job_ref_a` ya no es el "
        "menor, que es como lo guarda `seed_dedup_pairs`"
    )
