"""Política cross_encoder contra Postgres real (Fase 2 cierre definitivo).

Score ABSOLUTO por pareja ⇒ puede ser canónica; el almacén append-only actúa
como CACHÉ legítima (misma identidad ⇒ mismo score) y tras un cambio de
corpus solo se puntúan los misses. Un fallo del modelo deja el último feed
bueno intacto. Ejecutar vía core-migrate.
"""

import asyncio
import os

import pytest
import sqlalchemy as sa

from jobhunt_core import cross_encoder as ce
from jobhunt_core import embeddings, matching
from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.tests.test_integration_dev_eval import (  # noqa: F401
    _compute, _evaluate_shadow, _feed_actual, _judgments_file, _run_eval,
)
from jobhunt_core.tests.test_integration_matching import (  # noqa: F401
    DirectionalBackend, _evaluate, _listing, _rows, _setup, db,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

TITULOS = [
    "python backend developer", "senior python engineer",
    "data engineer python sql", "warehouse operative",
    "kubernetes platform engineer", "frontend react developer",
]


class _StubEngine:
    def __init__(self):
        self.docs_scored = 0

    def predict(self, pares, batch_size=16):
        self.docs_scored += len(pares)
        return [((hash(q + "|" + d) % 1000) - 500) / 100.0 for q, d in pares]


@pytest.fixture()
def stub():
    motor = _StubEngine()
    ce.set_engine_factory(lambda model, revision: motor)
    yield motor
    ce.set_engine_factory(None)


def _xenc_policy(factory, created, active=False):
    async def go():
        async with factory() as s:
            polid = await matching.ensure_policy(
                s, matching.XENC_POLICY_NAME, matching.XENC_POLICY_VERSION,
                weights=matching.XENC_POLICY_WEIGHTS, active=active)
            created["policies"].append(polid)
            await s.commit()
            return polid

    return asyncio.run(go())


def test_ce_es_absoluta_promocionable_y_feed_igual_a_calculo(db, stub):
    """CE puede ser canónica (pair_absolute); lo servido tras promover es
    EXACTAMENTE el cálculo directo, fila a fila, también tras un cambio de
    corpus (G2); la pareja vieja conserva su score y solo se puntúan misses."""
    factory, created = db
    pid, mid, cosine_id, vacs = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    polid = _xenc_policy(factory, created)

    async def declare(ids):
        async with factory() as s:
            await matching.declare_active_policies(s, ids)
            await s.commit()

    # promoción legal (absoluta) + materialización
    asyncio.run(declare([polid]))
    r1 = _evaluate(factory, pid, mid, polid)
    assert r1["moved_current"] is True and r1["evaluated"] == len(TITULOS)
    docs_g1 = stub.docs_scored
    assert docs_g1 == len(TITULOS)  # una consulta (title) × 6 documentos

    feed_g1 = _feed_actual(factory, pid)
    calculo = [(f["vacancy_id"], f"{f['score']:.2f}")
               for f in _compute(factory, pid, mid, polid)["rows"]]
    assert feed_g1 == calculo
    assert stub.docs_scored == docs_g1  # el cálculo usó la CACHÉ, no el modelo

    # G2: cambio de corpus — solo el miss nuevo pasa por el modelo
    async def sink_offer():
        async with factory() as s:
            await RawListingSink().handle(
                s, str(created["scopes"][0]),
                (_listing("j-ce-g2", "python developer"),))
            await s.commit()

    asyncio.run(sink_offer())
    embeddings.set_backend_factory(lambda name, version: DirectionalBackend())
    try:
        from jobhunt_core.tasks.embedding import run_pending_task
        r = run_pending_task.apply(kwargs={"limit": 100})
        assert r.successful(), r.traceback
    finally:
        embeddings.set_backend_factory(None)

    r2 = _evaluate(factory, pid, mid, polid)
    assert r2["moved_current"] is True and r2["evaluated"] == len(TITULOS) + 1
    assert stub.docs_scored == docs_g1 + 1  # SOLO el nuevo documento
    feed_g2 = _feed_actual(factory, pid)
    assert feed_g2 == [(f["vacancy_id"], f"{f['score']:.2f}")
                       for f in _compute(factory, pid, mid, polid)["rows"]]
    # la pareja vieja conserva su score exacto
    viejas = dict(feed_g1)
    for vac, score in feed_g2:
        if vac in viejas:
            assert score == viejas[vac]

    # rollback exacto
    asyncio.run(declare([cosine_id]))
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    assert {v for v, _ in _feed_actual(factory, pid)} == {
        v for v, _ in feed_g2}  # mismas vacantes vivas, scores de cosine


def test_cambio_de_identidad_repuntua_y_reusa_lo_que_corresponde(db, stub):
    """Cambiar target_roles crea otra revisión de perfil ⇒ otra identidad ⇒
    re-puntuación completa; el MISMO contenido reactivado reutiliza la caché."""
    factory, created = db
    pid, mid, _, _ = _setup(factory, created, TITULOS[:3])
    polid = _xenc_policy(factory, created)
    assert _evaluate_shadow(factory, pid, mid, polid)["evaluated"] == 3
    base = stub.docs_scored

    def set_roles(roles):
        async def go():
            async with factory() as s:
                from jobhunt_core import profiles as core_profiles
                cur = await core_profiles.current_revision(s, pid)
                contenido = dict(cur.content, target_roles=roles)
                await core_profiles.save_profile_revision(s, pid, contenido)
                await s.commit()

        asyncio.run(go())
        # cambiar target_roles NO re-embebe (mismo text_hash): el worker COPIA
        # el vector — el backend envenenado muerde si hubiera forward pass.

        class _Poison:
            def encode_batch(self, texts):
                raise AssertionError("forward pass con cambio solo de roles")

        embeddings.set_backend_factory(lambda n, v: _Poison())
        try:
            from jobhunt_core.tasks.embedding import run_pending_task
            r = run_pending_task.apply(kwargs={"limit": 100})
            assert r.successful(), r.traceback
        finally:
            embeddings.set_backend_factory(None)

    set_roles(["Data Engineer", "Backend Developer"])
    assert _evaluate_shadow(factory, pid, mid, polid)["evaluated"] == 3
    # identidad nueva: re-puntuación completa — 2 consultas (roles) × 3 docs
    assert stub.docs_scored == base + 6

    # A→B→A: el contenido ORIGINAL reactivado vuelve a su revisión ⇒ caché
    set_roles([])
    assert _evaluate_shadow(factory, pid, mid, polid)["evaluated"] == 3
    assert stub.docs_scored == base + 6  # cero inferencias nuevas


def test_fallo_del_modelo_deja_el_feed_intacto(db):
    """Error de inferencia ⇒ excepción observable, transacción abortada, el
    último feed bueno sigue sirviéndose. Sin fallback silencioso a cosine."""
    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:3])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _xenc_policy(factory, created)

    class _Roto:
        def predict(self, pares, batch_size=16):
            raise RuntimeError("OOM simulado del cross-encoder")

    ce.set_engine_factory(lambda m, r: _Roto())
    try:
        async def go():
            async with factory() as s:
                await matching.declare_active_policies(s, [polid])
                await s.commit()
            with pytest.raises(RuntimeError, match="OOM"):
                await matching.evaluate_profile(
                    factory, pid, mid, polid, move_current=True)
            async with factory() as s:  # rollback de activación explícito
                await matching.declare_active_policies(s, [cosine_id])
                await s.commit()

        asyncio.run(go())
    finally:
        ce.set_engine_factory(None)
    # el fallo del modelo (fase 2, sin BD) no persistió NADA: cosine vuelve a
    # ser canónica y el feed bueno sigue intacto
    assert _feed_actual(factory, pid) == antes
    assert _rows(
        factory,
        "SELECT count(*) AS n FROM match_evaluations WHERE "
        "scoring_policy_id = :sp", sp=polid)[0].n == 0


def test_receta_ce_manipulada_no_evalua(db, stub):
    factory, created = db
    pid, mid, _, _ = _setup(factory, created, TITULOS[:2])

    async def go():
        async with factory() as s:
            mala = dict(matching.XENC_POLICY_WEIGHTS, input="v9")
            polid = await matching.ensure_policy(
                s, matching.XENC_POLICY_NAME, "v99", weights=mala,
                active=False)
            created["policies"].append(polid)
            await s.commit()
        with pytest.raises(ValueError, match="input"):
            await matching.evaluate_profile(
                factory, pid, mid, polid, move_current=False)

    asyncio.run(go())


def test_escritura_progresa_durante_inferencia_y_lo_rancio_no_se_publica(db):
    """P1-3 revisión 2026-09-03: la inferencia (horas en el NAS) corría con la
    transacción abierta y el perfil FOR UPDATE — una edición del perfil
    esperaba horas. Trifásico: preparar (txn corta) → inferir SIN BD →
    revalidar+persistir (txn corta). Durante la inferencia una escritura de
    perfil PROGRESA; al reanudar, el resultado RANCIO (revisión derivada) se
    descarta sin publicar nada."""
    import threading

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:3])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _xenc_policy(factory, created)

    dentro = threading.Event()
    barrera = threading.Event()

    class _Lento:
        def predict(self, pares, batch_size=16):
            dentro.set()
            assert barrera.wait(timeout=60), "la barrera no se liberó"
            return [0.0] * len(pares)

    ce.set_engine_factory(lambda m, r: _Lento())
    resultado = {}

    def evaluar():
        async def run():
            return await matching.evaluate_profile(
                factory, pid, mid, polid, move_current=False)

        resultado["r"] = asyncio.run(run())

    try:
        hilo = threading.Thread(target=evaluar)
        hilo.start()
        assert dentro.wait(timeout=60), "la inferencia no arrancó"

        # Con la inferencia EN CURSO, una escritura del perfil progresa
        # (lock_timeout corto: en el padre moría esperando el FOR UPDATE).
        async def escribir():
            async with factory() as s:
                await s.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
                from jobhunt_core import profiles as core_profiles
                cur = await core_profiles.current_revision(s, pid)
                rid = await core_profiles.save_profile_revision(
                    s, pid, dict(cur.content, target_roles=["QA Lead"]))
                await s.commit()
                return rid

        assert asyncio.run(escribir()) is not None
    finally:
        barrera.set()
        hilo.join(timeout=120)
        ce.set_engine_factory(None)

    # lo RANCIO no se publica: la revisión derivó durante la inferencia
    assert resultado["r"]["status"] == "descartado_por_deriva"
    assert _rows(
        factory,
        "SELECT count(*) AS n FROM match_evaluations "
        "WHERE scoring_policy_id = :sp", sp=polid)[0].n == 0
    assert _feed_actual(factory, pid) == antes


def test_tier_ordena_viables_antes_que_incompatibles_demostradas(db):
    """P2-1 revisión 2026-09-03: el CE puro ordena por afinidad temática — una
    oferta restringida a EE. UU. con mayor logit va primero aunque el código
    YA sabe detectar la incompatibilidad. cross_encoder_tier: primero
    viable/desconocida, después incompatible_demostrada; dentro de cada nivel
    manda ce_prob. Absoluto por pareja ⇒ promovible."""
    factory, created = db
    pid, mid, cosine_id, vacs = _setup(
        factory, created,
        ["bilingual content specialist", "bilingual content expert"],
        profile_content={
            "title": "bilingual content specialist", "skills": ["content"],
            "languages": ["English", "Spanish"],
            "locations": ["Remote", "Spain"], "remote_pref": "remote_only",
        })

    # la oferta 'expert' se ancla a EE. UU. (remota restringida)
    async def anclar():
        async with factory() as s:
            await s.execute(sa.text(
                "UPDATE offer_revisions SET content = content || "
                "CAST('{\"location\": \"Texas (USA)\", \"remote\": true}' AS jsonb) "
                "WHERE content->>'title' = 'bilingual content expert'"))
            await s.execute(sa.text(
                "UPDATE offer_revisions SET content = content || "
                "CAST('{\"location\": \"Remote\", \"remote\": true}' AS jsonb) "
                "WHERE content->>'title' = 'bilingual content specialist'"))
            await s.commit()

    asyncio.run(anclar())

    class _Tematico:
        def predict(self, pares, batch_size=16):
            # la ANCLADA es temáticamente "mejor" para el modelo
            return [3.0 if "expert" in d else 1.0 for _, d in pares]

    ce.set_engine_factory(lambda m, r: _Tematico())
    try:
        async def go():
            async with factory() as s:
                puro = await matching.ensure_policy(
                    s, matching.XENC_POLICY_NAME, "v2",
                    weights=matching.XENC2_POLICY_WEIGHTS, active=False)
                tier = await matching.ensure_policy(
                    s, matching.XENC_TIER_POLICY_NAME,
                    matching.XENC_TIER_POLICY_VERSION,
                    weights=matching.XENC_TIER_POLICY_WEIGHTS, active=False)
                created["policies"] += [puro, tier]
                await s.commit()
            async with factory() as s:
                fp = await matching.compute_policy_feed(s, pid, mid, puro)
                ft = await matching.compute_policy_feed(s, pid, mid, tier)
                return fp["rows"], ft["rows"]

        rows_puro, rows_tier = asyncio.run(go())
    finally:
        ce.set_engine_factory(None)

    anclada = vacs["bilingual content expert"]
    viable = vacs["bilingual content specialist"]
    # el CE puro pone primero la anclada (defecto que motiva P2-1)…
    assert rows_puro[0]["vacancy_id"] == anclada
    # …y el tier pone primero la VIABLE, con la anclada degradada al nivel 0
    assert rows_tier[0]["vacancy_id"] == viable
    assert rows_tier[0]["score"] > 50 > rows_tier[1]["score"]
    assert rows_tier[0]["score_parts"]["tier"] == 1
    assert rows_tier[1]["score_parts"]["tier"] == 0
    assert rows_tier[1]["score_parts"]["compat"]["inc_loc"] is True
    # dentro del nivel manda ce_prob; desconocido/ambiguo NO castiga
    assert matching._is_pair_absolute(matching.XENC_TIER_POLICY_WEIGHTS)


def test_worker_lento_no_restaura_un_feed_mas_nuevo(db):
    """Revisión 2026-09-04 P1: A prepara sobre la generación G1 y se queda en
    inferencia; el corpus avanza (G2) y B evalúa y publica el feed nuevo; A
    termina DESPUÉS. Sin revalidar corpus_generation, A borraba el puntero de
    la vacante nueva (UPDATE ... current_eval_id = NULL para lo que no está
    en SU conjunto) y restauraba el feed viejo. A debe descartarse."""
    import threading

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:3])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    polid = _xenc_policy(factory, created)

    async def declare():
        async with factory() as s:
            await matching.declare_active_policies(s, [polid])
            await s.commit()

    asyncio.run(declare())

    dentro = threading.Event()
    barrera = threading.Event()
    primera = threading.Event()

    class _Motor:
        def predict(self, pares, batch_size=16):
            if not primera.is_set():  # SOLO la primera inferencia se bloquea
                primera.set()
                dentro.set()
                assert barrera.wait(timeout=60), "la barrera no se liberó"
            return [((hash(q + "|" + d) % 1000) - 500) / 100.0
                    for q, d in pares]

    ce.set_engine_factory(lambda m, r: _Motor())
    resultado = {}

    def evaluar_a():
        async def run():
            return await matching.evaluate_profile(factory, pid, mid, polid)

        resultado["A"] = asyncio.run(run())

    try:
        hilo = threading.Thread(target=evaluar_a)
        hilo.start()
        assert dentro.wait(timeout=60), "la inferencia de A no arrancó"

        # El corpus AVANZA durante la inferencia de A (G1 → G2).
        async def sink_offer():
            async with factory() as s:
                await RawListingSink().handle(
                    s, str(created["scopes"][0]),
                    (_listing("j-race-g2", "python developer"),))
                await s.commit()

        asyncio.run(sink_offer())
        embeddings.set_backend_factory(
            lambda name, version: DirectionalBackend())
        try:
            from jobhunt_core.tasks.embedding import run_pending_task
            r = run_pending_task.apply(kwargs={"limit": 100})
            assert r.successful(), r.traceback
        finally:
            embeddings.set_backend_factory(None)

        # B evalúa el corpus NUEVO y publica el feed de G2.
        resultado["B"] = _evaluate(factory, pid, mid, polid)
        assert resultado["B"]["moved_current"] is True
        feed_b = _feed_actual(factory, pid)
        assert len(feed_b) == len(TITULOS[:3]) + 1
    finally:
        barrera.set()
        hilo.join(timeout=120)
        ce.set_engine_factory(None)

    # El lento se descarta y el feed de B queda EXACTAMENTE intacto.
    assert resultado["A"]["status"] == "descartado_por_deriva"
    assert _feed_actual(factory, pid) == feed_b


def test_modelo_desactivado_durante_la_inferencia_no_publica(db):
    """Revisión 2026-09-04 P1: la fase final protegía la política canónica
    pero NO el modelo — un modelo desactivado durante una inferencia lenta
    aún podía mover el feed. Debe descartarse sin escribir nada."""
    import threading

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:2])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _xenc_policy(factory, created)

    async def declare():
        async with factory() as s:
            await matching.declare_active_policies(s, [polid])
            await s.commit()

    asyncio.run(declare())

    dentro = threading.Event()
    barrera = threading.Event()

    class _Lento:
        def predict(self, pares, batch_size=16):
            dentro.set()
            assert barrera.wait(timeout=60), "la barrera no se liberó"
            return [0.0] * len(pares)

    ce.set_engine_factory(lambda m, r: _Lento())
    resultado = {}

    def evaluar():
        async def run():
            return await matching.evaluate_profile(factory, pid, mid, polid)

        resultado["r"] = asyncio.run(run())

    try:
        hilo = threading.Thread(target=evaluar)
        hilo.start()
        assert dentro.wait(timeout=60), "la inferencia no arrancó"

        async def desactivar():
            async with factory() as s:
                await s.execute(sa.text(
                    "UPDATE embedding_models SET active = false "
                    "WHERE id = :m"), {"m": mid})
                await s.commit()

        asyncio.run(desactivar())
    finally:
        barrera.set()
        hilo.join(timeout=120)
        ce.set_engine_factory(None)

    assert resultado["r"]["status"] == "descartado_por_deriva"
    assert _feed_actual(factory, pid) == antes
    assert _rows(
        factory,
        "SELECT count(*) AS n FROM match_evaluations "
        "WHERE scoring_policy_id = :sp", sp=polid)[0].n == 0


def test_generacion_protegida_hasta_el_commit(db):
    """Revisión 2026-09-04 1A: A revalida en F3 (lee la generación FINAL) y
    se pausa ANTES de escribir; B intenta una mutación de elegibilidad
    (UPDATE de offer_embeddings ⇒ trigger sobre la fila única de
    corpus_generation). Con un SELECT ordinario B cometía G2 y A publicaba
    candidatos de G1 debajo; con FOR SHARE mantenido hasta el commit, B
    ESPERA (lock_timeout muerde) y solo procede tras la publicación de A."""
    import threading

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:2])

    dentro = threading.Event()
    barrera = threading.Event()

    async def hook():
        dentro.set()
        await asyncio.to_thread(barrera.wait, 60)

    matching.set_after_revalidation_hook(hook)
    resultado = {}

    def evaluar():
        async def run():
            return await matching.evaluate_profile(
                factory, pid, mid, cosine_id)

        resultado["r"] = asyncio.run(run())

    bloqueado = {}
    try:
        hilo = threading.Thread(target=evaluar)
        hilo.start()
        assert dentro.wait(timeout=60), "A no llegó a la revalidación"

        # B: mutación de elegibilidad con lock_timeout corto — DEBE esperar
        # a A (el trigger actualiza la fila que A tiene FOR SHARE).
        async def mutar():
            async with factory() as s:
                await s.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
                try:
                    # UPDATE válido que dispara el trigger de sentencia
                    await s.execute(sa.text(
                        "UPDATE offer_embeddings SET vector = vector "
                        "WHERE model_id = :m"), {"m": mid})
                    await s.commit()
                    return "cometio"
                except Exception as e:
                    await s.rollback()
                    orig = getattr(e, "orig", e)
                    return f"{type(e).__name__}:{type(orig).__name__}:{orig}"

        bloqueado["b"] = asyncio.run(mutar())
    finally:
        barrera.set()
        hilo.join(timeout=120)
        matching.set_after_revalidation_hook(None)

    # B fue BLOQUEADO por el LOCK (no por otro error) mientras A publicaba:
    # jamás corpus G2 + feed G1 sin señal pendiente.
    assert "LockNotAvailable" in bloqueado["b"], bloqueado
    assert resultado["r"]["status"] == "ok"
    assert resultado["r"]["moved_current"] is True


def test_activar_un_modelo_anterior_durante_la_inferencia_descarta(db):
    """Revisión 2026-09-04 1B: A infiere con el modelo Z (canónico); durante
    la inferencia la autoridad activa un modelo ANTERIOR en el orden, ya
    embebido. Z sigue ACTIVO — la comprobación booleana dejaba publicar a A
    aunque ya no fuera el canónico. La valla compara el id EXACTO
    (canonical_model_id) y descarta; el feed previo queda byte-equivalente."""
    import threading

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:2])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _xenc_policy(factory, created)

    async def declare():
        async with factory() as s:
            await matching.declare_active_policies(s, [polid])
            await s.commit()

    asyncio.run(declare())

    # Modelo "aa-…" (anterior en el orden name,version) YA EMBEBIDO pero
    # inactivo: copia de vectores del modelo vigente (identidad directa).
    async def preparar_anterior():
        async with factory() as s:
            m2 = await embeddings.register_model(
                s, "aa-modelo-anterior", "a" * 40, active=False)
            created["models"].append(m2)
            await s.commit()
            await s.execute(sa.text(
                "INSERT INTO offer_embeddings (text_hash, model_id, vector) "
                "SELECT text_hash, :m2, vector FROM offer_embeddings "
                "WHERE model_id = :m1"), {"m2": m2, "m1": mid})
            await s.execute(sa.text(
                "INSERT INTO profile_embeddings "
                "(profile_id, profile_revision_id, model_id, vector) "
                "SELECT profile_id, profile_revision_id, :m2, vector "
                "FROM profile_embeddings WHERE model_id = :m1"),
                {"m2": m2, "m1": mid})
            await s.commit()
            return m2

    m2 = asyncio.run(preparar_anterior())

    dentro = threading.Event()
    barrera = threading.Event()

    class _Lento:
        def predict(self, pares, batch_size=16):
            dentro.set()
            assert barrera.wait(timeout=60), "la barrera no se liberó"
            return [0.0] * len(pares)

    ce.set_engine_factory(lambda m, r: _Lento())
    resultado = {}

    def evaluar():
        async def run():
            return await matching.evaluate_profile(
                factory, pid, mid, polid)

        resultado["r"] = asyncio.run(run())

    try:
        hilo = threading.Thread(target=evaluar)
        hilo.start()
        assert dentro.wait(timeout=60), "la inferencia no arrancó"

        # AUTORIDAD: activa el anterior (Z SIGUE activo) durante la inferencia
        async def activar_anterior():
            async with factory() as s:
                await embeddings.declare_active_models(s, [m2, mid])
                await s.commit()

        asyncio.run(activar_anterior())
    finally:
        barrera.set()
        hilo.join(timeout=120)
        ce.set_engine_factory(None)

    assert resultado["r"]["status"] == "descartado_por_deriva"
    assert _feed_actual(factory, pid) == antes  # byte-equivalente
    assert _rows(
        factory,
        "SELECT count(*) AS n FROM match_evaluations "
        "WHERE scoring_policy_id = :sp", sp=polid)[0].n == 0


def test_materializacion_por_watermark_presupuesto_y_publicacion(db):
    """P7-b: la materialización puntúa por lotes REANUDABLES dentro del
    presupuesto; con backlog NO publica (feed intacto, señal); al quedar al
    día la evaluación publica la fotografía completa con CERO inferencias
    nuevas y los eventos del outbox se emiten UNA sola vez por eval_key."""
    from jobhunt_core.tasks.materialize import _impl as materializar

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS)
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _xenc_policy(factory, created)

    async def declare():
        async with factory() as s:
            await matching.declare_active_policies(s, [polid])
            await s.commit()

    asyncio.run(declare())

    contador = {"docs": 0}

    class _Motor:
        def predict(self, pares, batch_size=16):
            contador["docs"] += len(pares)
            return [((hash(q + "|" + d) % 1000) - 500) / 100.0
                    for q, d in pares]

    ce.set_engine_factory(lambda m, r: _Motor())
    try:
        # Presupuesto 0: primer ciclo entra ya agotado ⇒ backlog, 0 publicado
        r0 = asyncio.run(materializar(
            str(pid), str(polid), 0.0, session_factory=factory))
        assert r0["status"] == "backlog" and r0["remaining"] == len(TITULOS)
        assert "evaluacion" not in r0
        assert _feed_actual(factory, pid) == antes  # fotografía previa intacta

        # Presupuesto holgado: materializa TODO, publica y el feed cambia
        r1 = asyncio.run(materializar(
            str(pid), str(polid), 60.0, session_factory=factory))
        assert r1["status"] == "ok" and r1["scored"] == len(TITULOS)
        assert r1["evaluacion"]["moved_current"] is True
        docs_materializados = contador["docs"]
        assert docs_materializados == len(TITULOS)
        feed_ce = _feed_actual(factory, pid)
        assert feed_ce != antes and len(feed_ce) == len(TITULOS)

        # Reanudable/idempotente: repetir NO re-puntúa ni duplica eventos
        r2 = asyncio.run(materializar(
            str(pid), str(polid), 60.0, session_factory=factory))
        assert r2["status"] == "ok" and r2["scored"] == 0
        assert contador["docs"] == docs_materializados  # CERO inferencias
        eventos = _rows(
            factory,
            "SELECT count(*) AS n, count(DISTINCT event_id) AS d "
            "FROM integration_outbox WHERE type = 'match.evaluated' "
            "AND subject_profile_id = :p", p=pid)[0]
        assert eventos.n == eventos.d  # un evento por eval_key, sin duplicar
    finally:
        ce.set_engine_factory(None)


def test_materializacion_en_sombra_no_mueve_ni_descarta(db):
    """P7-b: con la política CE INACTIVA (pre-promoción), la tarea materializa
    y evalúa EN SOMBRA — filas append-only registradas, feed intacto, y jamás
    un descarte por la valla de canonicidad."""
    from jobhunt_core.tasks.materialize import _impl as materializar

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:3])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True
    antes = _feed_actual(factory, pid)
    polid = _xenc_policy(factory, created, active=False)  # SOMBRA

    ce.set_engine_factory(lambda m, r: _StubEngine())
    try:
        r = asyncio.run(materializar(
            str(pid), str(polid), 60.0, session_factory=factory))
    finally:
        ce.set_engine_factory(None)

    assert r["status"] == "ok"
    assert r["evaluacion"]["status"] == "ok"          # ni descartado
    assert r["evaluacion"]["moved_current"] is False  # ni movido
    assert _feed_actual(factory, pid) == antes
    n = _rows(factory, "SELECT count(*) AS n FROM match_evaluations "
              "WHERE scoring_policy_id = :sp", sp=polid)[0].n
    assert n == len(TITULOS[:3])  # sombra registrada append-only


def test_materialize_all_solo_actua_sobre_ce_activas(db):
    """Beat P7-b: sin políticas CE activas = no-op; con la CE canónica activa
    materializa y publica para los perfiles existentes."""
    from jobhunt_core.tasks.materialize import _all_impl

    factory, created = db
    pid, mid, cosine_id, _ = _setup(factory, created, TITULOS[:2])
    assert _evaluate(factory, pid, mid, cosine_id)["moved_current"] is True

    # Solo cosine activa ⇒ no-op
    r0 = asyncio.run(_all_impl(session_factory=factory))
    assert r0["politicas_ce"] == 0

    polid = _xenc_policy(factory, created)

    async def declare():
        async with factory() as s:
            await matching.declare_active_policies(s, [polid])
            await s.commit()

    asyncio.run(declare())
    # Contrato de COORDINADOR (revisión externa 2026-09-07, P1-2): encola un
    # trabajo por (perfil, política) y NO materializa en línea — así ningún
    # ciclo concede presupuestos encadenados bajo el reloj de Celery.
    encoladas = []
    from jobhunt_core.tasks import materialize as _mat

    class _Espia:
        def apply_async(self, *a, **k):
            encoladas.append((a, k))

    orig = _mat.materialize_ce_task
    _mat.materialize_ce_task = _Espia()
    try:
        r1 = asyncio.run(_all_impl(session_factory=factory))
    finally:
        _mat.materialize_ce_task = orig
    assert r1["politicas_ce"] == 1
    assert any(str(pid) in str((a, k)) for a, k in encoladas)
    assert all(k.get("queue") == "core.matching" for _, k in encoladas)


def test_exclusion_dismissed_tambien_en_el_camino_CE(db, stub):
    """Revisión externa 2026-09-07 (P1-1, 2ª parte): el camino CE retornaba
    `ok_prep` ANTES del filtro de descartadas, así que una vacante descartada
    con score cacheado entraba igualmente en el cálculo. Con la frontera en
    SQL, la descartada no se recupera siquiera."""
    factory, created = db
    pid, mid, _, vacs = _setup(factory, created, TITULOS[:3])
    polid = _xenc_policy(factory, created)
    descartada = vacs[TITULOS[0]]

    async def descartar():
        async with factory() as s:
            await s.execute(sa.text(
                "INSERT INTO profile_vacancy_state "
                "(profile_id, vacancy_id, dismissed_at) VALUES (:p, :v, now())"),
                {"p": pid, "v": descartada})
            await s.commit()

    asyncio.run(descartar())

    async def calcular():
        async with factory() as s:
            return await matching.compute_policy_feed(
                s, pid, mid, polid, limit=100, exclude_dismissed=True,
                ce_inference=False)

    r = asyncio.run(calcular())
    assert r["status"] == "ok_prep"
    candidatos = {str(c.vacancy_id) for c in r["prep"]["candidates"]}
    assert str(descartada) not in candidatos, (
        "la descartada llegó a la preparación del CE")
    assert len(candidatos) == len(TITULOS[:3]) - 1
