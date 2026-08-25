"""Captura CDC B-01 (shadow/capture + core0008b + roles) contra Postgres real.

DoD (CONTRATOS_FASE_B §2/§7): (a) backfill consistente con la frontera
snapshot↔LSN registrada; (b) cambios post-snapshot por streaming en orden
LSN; (c) whitelist por tabla — hashed_password/email JAMÁS en staging;
(d) ack tras commit — kill −9 sin pérdida ni duplicado aplicado (re-entrega
absorbida por ON CONFLICT); (e) rol jobhunt_capture REPLICATION + GRANTs RO
enumerados y NADA más; (f) ciclo de migración head→core0008a→head;
(g) arranque en frío sin esquema legacy: reintento con backoff SIN crear el
slot; (h) healthcheck: slot presente pero INACTIVO ⇒ exit 1.

Aislamiento: BD DESECHABLE (jobhunt_cap_<hex>) con el esquema core migrado a
head y MINI-tablas fixture jobs/users/user_profiles en SU `public` (con las
columnas sensibles presentes, para probar que no pasan) — estos tests JAMÁS
tocan el `public` compartido ni el slot real `jobhunt_shadow`. Slot de test
PROPIO (nombre único) con DROP en finally SIEMPRE: un slot huérfano retiene
WAL y bloquearía el DROP DATABASE. Ejecutar vía core-migrate.
"""

import logging
import os
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa

from jobhunt_core.config import settings
from jobhunt_core.shadow.capture import DEFAULT_TABLES, ShadowCapture, health_check
from jobhunt_core.tests.alembic_runner import run_alembic

_ADMIN = os.getenv("CORE_ADMIN_DATABASE_URL")
S = settings.CORE_DB_SCHEMA


def _wal_level() -> str | None:
    engine = sa.create_engine(_ADMIN, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as c:
            return c.execute(sa.text("SHOW wal_level")).scalar()
    finally:
        engine.dispose()


pytestmark = [
    pytest.mark.skipif(
        not _ADMIN, reason="requiere BD (ejecutar vía core-migrate)"
    ),
    pytest.mark.skipif(
        bool(_ADMIN) and _wal_level() != "logical",
        reason="requiere wal_level=logical (imagen postgres-core + reinicio, B-01)",
    ),
]

SENSITIVE_KEYS = (
    "email", "hashed_password", "gdpr_consent", "gdpr_consent_at",
    "embedding", "cv_embedding", "search_vector",
)


def _admin_autocommit():
    return sa.create_engine(
        _ADMIN, poolclass=sa.pool.NullPool, isolation_level="AUTOCOMMIT"
    )


def _alembic_url(db_url: str) -> str:
    """env.py importa jobhunt_core.database (engine ASYNC a nivel de módulo):
    la URL inyectada a alembic debe llevar el driver asyncpg aunque el resto
    del test trabaje con psycopg2."""
    return db_url.replace("postgresql://", "postgresql+asyncpg://")


def _drop_slot(slot: str) -> None:
    """DROP del slot de test, esperando a que el walsender lo suelte."""
    engine = _admin_autocommit()
    try:
        with engine.connect() as c:
            for _ in range(40):
                row = c.execute(
                    sa.text(
                        "SELECT active, active_pid FROM pg_replication_slots "
                        "WHERE slot_name = :n"
                    ),
                    {"n": slot},
                ).one_or_none()
                if row is None:
                    return
                if not row.active:
                    c.execute(
                        sa.text("SELECT pg_drop_replication_slot(:n)"), {"n": slot}
                    )
                    return
                if row.active_pid:
                    c.execute(
                        sa.text("SELECT pg_terminate_backend(:p)"),
                        {"p": row.active_pid},
                    )
                time.sleep(0.25)
        raise RuntimeError(f"slot de test {slot} sigue activo: imposible soltarlo")
    finally:
        engine.dispose()


def _wait_slot_released(slot: str, timeout: float = 10.0) -> None:
    engine = _admin_autocommit()
    try:
        deadline = time.monotonic() + timeout
        with engine.connect() as c:
            while time.monotonic() < deadline:
                active = c.execute(
                    sa.text(
                        "SELECT active FROM pg_replication_slots WHERE slot_name = :n"
                    ),
                    {"n": slot},
                ).scalar()
                if not active:
                    return
                time.sleep(0.2)
        raise RuntimeError(f"walsender del slot {slot} no se soltó en {timeout}s")
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def capture_db():
    """BD desechable: esquema core en head + mini-tablas legacy en SU public."""
    dbname = f"jobhunt_cap_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(_ADMIN)
    admin_engine = _admin_autocommit()
    with admin_engine.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    capture_pw = os.getenv("CORE_CAPTURE_PASSWORD", "jobhunt_capture_dev")
    capture_dsn = urlunsplit(
        (
            parts.scheme,
            f"jobhunt_capture:{capture_pw}@{parts.hostname}:{parts.port}",
            f"/{dbname}",
            "",
            "",
        )
    )
    engine = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
    try:
        with engine.begin() as c:
            c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{S}"'))
            # Mini-tablas fixture CON las columnas prohibidas/pesadas
            # presentes: el test (c) demuestra que jamás llegan al staging.
            c.execute(
                sa.text(
                    "CREATE TABLE public.jobs ("
                    "hash varchar(32) PRIMARY KEY, title varchar(500), "
                    "company varchar(300), description text, "
                    "url varchar(2048), source varchar(50), "
                    "tags jsonb NOT NULL DEFAULT '[]'::jsonb, "
                    "is_active boolean NOT NULL DEFAULT true, "
                    "duplicate_of varchar(32), content_hash varchar(32), "
                    "embedding vector(3))"
                )
            )
            c.execute(
                sa.text(
                    "CREATE TABLE public.users ("
                    "id uuid PRIMARY KEY, email varchar(320), "
                    "hashed_password varchar(128), "
                    "is_active boolean NOT NULL DEFAULT true, "
                    "gdpr_consent boolean NOT NULL DEFAULT false)"
                )
            )
            c.execute(
                sa.text(
                    "CREATE TABLE public.user_profiles ("
                    "id uuid PRIMARY KEY, user_id uuid, title varchar(200), "
                    "cv_text text, skills jsonb NOT NULL DEFAULT '[]'::jsonb, "
                    "updated_at timestamptz NOT NULL DEFAULT now(), "
                    "cv_embedding vector(3))"
                )
            )
        run_alembic(_alembic_url(db_url), "upgrade", "head")
        yield {"engine": engine, "core_dsn": db_url, "capture_dsn": capture_dsn}
    finally:
        engine.dispose()
        with admin_engine.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        admin_engine.dispose()


@pytest.fixture()
def capture(capture_db):
    """Factory de ShadowCapture con slot único por test; en el finally se
    cierran las instancias, se DROPea el slot SIEMPRE y se vacían staging y
    fixture (la BD del módulo queda limpia para el siguiente test)."""
    slot = f"cap_fx_{uuid.uuid4().hex[:10]}"
    instances: list[ShadowCapture] = []

    def make(**kwargs) -> ShadowCapture:
        cap = ShadowCapture(
            capture_db["capture_dsn"],
            capture_db["core_dsn"],
            slot=slot,
            tables=kwargs.pop("tables", DEFAULT_TABLES),
            **kwargs,
        )
        instances.append(cap)
        return cap

    try:
        yield make, slot, capture_db["engine"]
    finally:
        for cap in instances:
            cap.close()
        _drop_slot(slot)  # SIEMPRE: un slot vivo retiene WAL y bloquea el DROP DB
        with capture_db["engine"].begin() as c:
            c.execute(
                sa.text(f"TRUNCATE {S}.shadow_change_log, {S}.shadow_capture_state")
            )
            c.execute(sa.text("TRUNCATE public.jobs, public.users, public.user_profiles"))


def _rows(engine, sql: str, **params):
    with engine.connect() as c:
        return c.execute(sa.text(sql), params).all()


def _scalar(engine, sql: str, **params):
    with engine.connect() as c:
        return c.execute(sa.text(sql), params).scalar()


def _exec(engine, sql: str, **params) -> None:
    with engine.begin() as c:
        c.execute(sa.text(sql), params)


def _seed_job(engine, h: str, title: str = "Backend Dev", active: bool = True):
    _exec(
        engine,
        "INSERT INTO public.jobs (hash, title, company, description, url, source, "
        "tags, is_active, content_hash, embedding) VALUES "
        "(:h, :t, 'ACME AG', 'desc', :u, 'legacyfx', CAST(:tags AS jsonb), :a, "
        ":ch, '[1,2,3]'::vector)",
        h=h, t=title, u=f"https://fx/{h}", tags='["py"]', a=active, ch=f"c-{h}",
    )


def _seed_user(engine, uid, active: bool = True):
    _exec(
        engine,
        "INSERT INTO public.users (id, email, hashed_password, is_active, "
        "gdpr_consent) VALUES (:i, :e, :hp, :a, true)",
        i=uid, e=f"{uid}@example.com", hp="$2b$12$super-secreto", a=active,
    )


def _seed_profile(engine, pid, uid):
    _exec(
        engine,
        "INSERT INTO public.user_profiles (id, user_id, title, cv_text, skills, "
        "cv_embedding) VALUES (:i, :u, 'dev', 'mi cv', CAST(:s AS jsonb), "
        "'[4,5,6]'::vector)",
        i=pid, u=uid, s='["python"]',
    )


def _toast_text(kib: int = 32) -> str:
    """Texto ALEATORIO (incompresible: hex de uuid4, sin repeticiones) de
    ~kib KiB — pglz no puede dejarlo inline y Postgres lo saca out-of-line
    (TOAST real, > 8KB): la condición exacta del matiz §2/§8 en que wal2json
    OMITE del mensaje U el datum no tocado por el UPDATE. Si el valor NO
    quedara TOASTeado, wal2json lo incluiría y los asserts de _omitted
    fallarían: los tests se auto-validan."""
    return "".join(uuid.uuid4().hex for _ in range(kib * 32))  # 32 chars/uuid


def _stream_until(
    cap: ShadowCapture, engine, predicate, timeout: float = 30.0, ack: bool = True
):
    """Procesa el stream en ráfagas cortas hasta que `predicate(engine)`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(engine):
            return
        cap.stream(max_seconds=0.5, ack=ack)
    pytest.fail(f"timeout de {timeout}s esperando cambios del stream")


def _staged_count(engine, min_lsn: int | None = None) -> int:
    if min_lsn is None:
        return _scalar(engine, f"SELECT count(*) FROM {S}.shadow_change_log")
    return _scalar(
        engine,
        f"SELECT count(*) FROM {S}.shadow_change_log WHERE lsn > :l",
        l=min_lsn,
    )


# ---------------------------------------------------------------- (a) backfill


def test_backfill_consistent_with_registered_frontier(capture):
    make, slot, engine = capture
    for i in range(3):
        _seed_job(engine, f"job-a-{i}")
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    _seed_user(engine, u1)
    _seed_user(engine, u2, active=False)
    _seed_profile(engine, uuid.uuid4(), u1)

    cap = make()
    cap.start()  # bootstrap: slot + snapshot exportado + backfill + frontera

    # Conteos fixture == staging op='I' (DoD "backfill consistente").
    counts = dict(
        _rows(
            engine,
            f"SELECT src_table, count(*) FROM {S}.shadow_change_log "
            f"WHERE op = 'I' GROUP BY src_table",
        )
    )
    assert counts == {"jobs": 3, "users": 2, "user_profiles": 1}

    # Frontera snapshot↔LSN registrada en UNA fila, y todo el backfill vive
    # exactamente en ese LSN con seq incremental.
    state = _rows(engine, f"SELECT * FROM {S}.shadow_capture_state")[0]
    assert state.id == 1 and state.slot_name == slot
    assert state.snapshot_lsn > 0
    assert state.last_applied_lsn == state.snapshot_lsn
    assert state.snapshot_exported_at is not None
    lsns = {r.lsn for r in _rows(engine, f"SELECT lsn FROM {S}.shadow_change_log")}
    assert lsns == {state.snapshot_lsn}
    seqs = [
        r.seq_in_tx
        for r in _rows(
            engine, f"SELECT seq_in_tx FROM {S}.shadow_change_log ORDER BY seq_in_tx"
        )
    ]
    assert seqs == list(range(6))

    # El payload lleva las columnas contractuales (pk aparte, columna propia).
    job = _rows(
        engine,
        f"SELECT pk, payload FROM {S}.shadow_change_log "
        f"WHERE src_table = 'jobs' AND pk = 'job-a-0'",
    )[0]
    assert job.payload["title"] == "Backend Dev"
    assert job.payload["url"] == "https://fx/job-a-0"
    assert job.payload["tags"] == ["py"]
    assert "hash" not in job.payload  # la PK va en su columna, no en payload


# --------------------------------------------------------------- (b) streaming


def test_post_snapshot_changes_stream_in_lsn_order(capture):
    make, slot, engine = capture
    _seed_job(engine, "job-b-1")
    cap = make()
    cap.start()
    snap = _scalar(engine, f"SELECT snapshot_lsn FROM {S}.shadow_capture_state")

    # 4 transacciones post-snapshot; la tercera con DOS cambios (seq_in_tx).
    _seed_job(engine, "job-b-2")
    _exec(engine, "UPDATE public.jobs SET is_active = false WHERE hash = 'job-b-1'")
    with engine.begin() as c:
        for h in ("job-b-3a", "job-b-3b"):
            c.execute(
                sa.text(
                    "INSERT INTO public.jobs (hash, title, company, url, source) "
                    "VALUES (:h, 't', 'ACME AG', :u, 'legacyfx')"
                ),
                {"h": h, "u": f"https://fx/{h}"},
            )
    _exec(engine, "DELETE FROM public.jobs WHERE hash = 'job-b-2'")

    _stream_until(cap, engine, lambda e: _staged_count(e, min_lsn=snap) >= 5)
    rows = _rows(
        engine,
        f"SELECT lsn, seq_in_tx, op, pk, payload FROM {S}.shadow_change_log "
        f"WHERE lsn > :snap ORDER BY lsn, seq_in_tx",
        snap=snap,
    )
    assert [(r.op, r.pk) for r in rows] == [
        ("I", "job-b-2"),
        ("U", "job-b-1"),
        ("I", "job-b-3a"),
        ("I", "job-b-3b"),
        ("D", "job-b-2"),
    ]
    lsns = [r.lsn for r in rows]
    assert lsns == sorted(lsns) and len(set(lsns)) == len(lsns)  # orden LSN real
    # seq_in_tx: la tx de dos cambios los numera 0 y 1.
    assert [r.seq_in_tx for r in rows if r.pk.startswith("job-b-3")] == [0, 1]
    # El UPDATE trae el estado nuevo whitelisted; el DELETE, payload vacío
    # (REPLICA IDENTITY default: solo la PK, que va en su columna).
    assert rows[1].payload["is_active"] is False
    assert rows[4].payload == {}
    # El progreso avanza con el último commit aplicado.
    last_applied = _scalar(
        engine, f"SELECT last_applied_lsn FROM {S}.shadow_capture_state"
    )
    assert last_applied >= max(lsns) > snap


# --------------------------------------------------------------- (c) whitelist


def test_whitelist_blocks_sensitive_and_heavy_columns(capture):
    make, slot, engine = capture
    uid = uuid.uuid4()
    _seed_user(engine, uid)
    _seed_profile(engine, uuid.uuid4(), uid)
    _seed_job(engine, "job-c-1")
    cap = make()
    cap.start()  # backfill con las columnas sensibles delante

    # Y también por la vía de STREAMING (mensajes wal2json con TODAS las
    # columnas de la fila, sensibles incluidas).
    _exec(engine, "UPDATE public.users SET is_active = false WHERE id = :i", i=uid)
    _exec(
        engine,
        "UPDATE public.user_profiles SET cv_text = 'cv v2' WHERE user_id = :i",
        i=uid,
    )
    snap = _scalar(engine, f"SELECT snapshot_lsn FROM {S}.shadow_capture_state")
    _stream_until(cap, engine, lambda e: _staged_count(e, min_lsn=snap) >= 2)

    # hashed_password/email/gdpr_*/vectores: JAMÁS en payload alguno (§2/§8).
    leaked = _scalar(
        engine,
        f"SELECT count(*) FROM {S}.shadow_change_log "
        f"WHERE payload ?| CAST(:keys AS text[])",
        keys=list(SENSITIVE_KEYS),
    )
    assert leaked == 0
    # users: SOLO id/is_active, en backfill Y en streaming.
    for r in _rows(
        engine,
        f"SELECT payload FROM {S}.shadow_change_log WHERE src_table = 'users'",
    ):
        assert set(r.payload) <= {"id", "is_active"}
    # El resto contractual sí llega (el filtro no vació el staging).
    streamed_profile = _rows(
        engine,
        f"SELECT payload FROM {S}.shadow_change_log "
        f"WHERE src_table = 'user_profiles' AND lsn > :snap",
        snap=snap,
    )[0]
    assert streamed_profile.payload["cv_text"] == "cv v2"


# -------------------------------------------------- (c-bis) TOAST omitido (§2/§8)


def test_toast_update_user_profiles_backfills_omitted_cv_text(capture):
    """P1 TOAST: UPDATE que NO toca cv_text (>8KB, TOASTeado real) → wal2json
    lo OMITE del mensaje U (el caso real: PUT parcial de /profile o
    analyze_cv_and_autofill del legacy). El staging lo COMPLETA por
    re-lectura RO y deja constancia en _omitted/_backfilled — sin esto B-02
    confundiría la ausencia con NULL."""
    make, slot, engine = capture
    uid, pid = uuid.uuid4(), uuid.uuid4()
    _seed_user(engine, uid)
    big_cv = _toast_text(32)
    _exec(
        engine,
        "INSERT INTO public.user_profiles (id, user_id, title, cv_text, skills) "
        "VALUES (:i, :u, 'dev', :cv, CAST(:s AS jsonb))",
        i=pid, u=uid, cv=big_cv, s='["python"]',
    )
    cap = make()
    cap.start()
    snap = _scalar(engine, f"SELECT snapshot_lsn FROM {S}.shadow_capture_state")

    _exec(
        engine,
        "UPDATE public.user_profiles SET title = 'dev v2' WHERE id = :i",
        i=pid,
    )
    _stream_until(cap, engine, lambda e: _staged_count(e, min_lsn=snap) >= 1)

    row = _rows(
        engine,
        f"SELECT payload FROM {S}.shadow_change_log "
        f"WHERE src_table = 'user_profiles' AND op = 'U' AND lsn > :snap",
        snap=snap,
    )[0]
    # El payload CONSERVA el cv_text COMPLETO (re-leído por PK con la
    # conexión RO del core), no NULL ni ausente...
    assert row.payload["cv_text"] == big_cv
    assert row.payload["title"] == "dev v2"
    # ...y la meta lo refleja: omitido del mensaje wal2json Y completado.
    assert row.payload["_omitted"] == ["cv_text"]
    assert row.payload["_backfilled"] == ["cv_text"]
    # El backfill inicial (op=I) leyó la tabla directamente: sin meta y con
    # el cv_text íntegro.
    initial = _rows(
        engine,
        f"SELECT payload FROM {S}.shadow_change_log "
        f"WHERE src_table = 'user_profiles' AND op = 'I'",
    )[0]
    assert "_omitted" not in initial.payload
    assert initial.payload["cv_text"] == big_cv


def test_toast_update_jobs_records_omitted_without_reread(capture):
    """P1 TOAST, caso jobs: description >8KB TOASTeada + UPDATE solo de
    is_active → el payload va SIN description y ES CORRECTO (§2: jobs NO se
    re-lee — el pre-filtro por content_hash del sink absorbe los updates sin
    cambio de contenido y un cambio real de contenido llega con description
    completa en el upsert de cosecha); _omitted lo registra para B-02."""
    make, slot, engine = capture
    big_desc = _toast_text(32)
    _exec(
        engine,
        "INSERT INTO public.jobs (hash, title, company, description, url, source, "
        "tags, is_active, content_hash) VALUES ('job-t-1', 'Dev', 'ACME AG', :d, "
        "'https://fx/job-t-1', 'legacyfx', CAST(:tags AS jsonb), true, 'c-t1')",
        d=big_desc, tags='["py"]',
    )
    cap = make()
    cap.start()
    snap = _scalar(engine, f"SELECT snapshot_lsn FROM {S}.shadow_capture_state")

    _exec(engine, "UPDATE public.jobs SET is_active = false WHERE hash = 'job-t-1'")
    _stream_until(cap, engine, lambda e: _staged_count(e, min_lsn=snap) >= 1)

    row = _rows(
        engine,
        f"SELECT payload FROM {S}.shadow_change_log "
        f"WHERE src_table = 'jobs' AND op = 'U' AND lsn > :snap",
        snap=snap,
    )[0]
    assert row.payload["is_active"] is False
    assert "description" not in row.payload  # sin re-lectura: correcto (§2)
    assert "description" in row.payload["_omitted"]
    assert "_backfilled" not in row.payload


# --------------------------------------------------- (d) ack tras commit / kill


def test_ack_after_commit_survives_kill_without_loss_or_duplicates(capture):
    make, slot, engine = capture
    _seed_job(engine, "job-d-1")
    cap1 = make()
    cap1.start()
    state = _rows(engine, f"SELECT * FROM {S}.shadow_capture_state")[0]

    _seed_job(engine, "job-d-2")  # tx post-snapshot
    # Se APLICA (commit en staging) pero JAMÁS se manda feedback (ack=False):
    # la ventana exacta del "kill −9" entre commit y ack.
    _stream_until(
        cap1,
        engine,
        lambda e: _staged_count(e, min_lsn=state.snapshot_lsn) >= 1,
        ack=False,
    )
    before = _rows(
        engine,
        f"SELECT lsn, seq_in_tx, pk FROM {S}.shadow_change_log ORDER BY lsn, seq_in_tx",
    )
    cap1.close()  # kill −9: conexión fuera SIN confirmed_flush avanzado
    _wait_slot_released(slot)

    # Reconexión: replay desde la frontera (el mismo mecanismo del runbook de
    # §6) — el slot RE-ENTREGA la tx ya aplicada porque nunca fue ack'eada.
    cap2 = make()
    cap2.start(from_lsn=state.snapshot_lsn)
    _stream_until(cap2, engine, lambda e: cap2.rows_applied >= 1, timeout=30.0)
    assert cap2.rows_applied >= 1  # re-entrega DEMOSTRADA

    after = _rows(
        engine,
        f"SELECT lsn, seq_in_tx, pk FROM {S}.shadow_change_log ORDER BY lsn, seq_in_tx",
    )
    assert after == before  # ni pérdida ni duplicado: idempotencia por PK
    assert (
        _scalar(
            engine,
            f"SELECT count(*) FROM {S}.shadow_change_log WHERE pk = 'job-d-2'",
        )
        == 1
    )
    cap2.close()
    _wait_slot_released(slot)

    # Y la reanudación NORMAL (desde last_applied_lsn, sin from_lsn) también
    # arranca limpia y sin duplicar tras el ack.
    cap3 = make()
    cap3.start()
    cap3.stream(max_seconds=1.0)
    assert _rows(
        engine,
        f"SELECT lsn, seq_in_tx, pk FROM {S}.shadow_change_log ORDER BY lsn, seq_in_tx",
    ) == after


# ------------------------------------------- (g) readiness del esquema legacy


def test_cold_start_without_legacy_schema_retries_and_creates_no_slot(
    capture, caplog
):
    """P3 arranque en frío: las migraciones legacy son MANUALES — sin las
    tablas capturadas el bootstrap NO debe crear el slot (un slot huérfano
    retendría WAL en cada vuelta del crash-loop, §8) sino esperar
    reintentando con backoff. El test acota la espera con ready_max_retries
    pequeño; el esquema `coldstart` no existe: mismo information_schema vacío
    que una BD aún sin migrar."""
    make, slot, engine = capture
    cap = make(tables="coldstart.jobs,coldstart.users", ready_max_retries=2)
    with caplog.at_level(logging.WARNING, logger="jobhunt_core.shadow.capture"):
        with pytest.raises(RuntimeError, match="esquema legacy aún sin migrar"):
            cap.start()
    # Reintentó con backoff y log claro (no crash inmediato)...
    assert "esquema legacy aún sin migrar" in caplog.text
    assert "reintentando en" in caplog.text
    # ...y JAMÁS creó el slot ni dejó estado a medias.
    assert (
        _scalar(
            engine,
            "SELECT count(*) FROM pg_replication_slots WHERE slot_name = :n",
            n=slot,
        )
        == 0
    )
    assert _scalar(engine, f"SELECT count(*) FROM {S}.shadow_capture_state") == 0


def test_readiness_blocks_when_required_column_missing(capture):
    """REGRESIÓN P1 rev. externa integral: la readiness solo exigía tabla + PK, así que una tabla a
    la que le falta una columna CONTRACTUAL requerida (p.ej. cv_text) pasaba y el backfill la
    omitía EN SILENCIO (intersect columns & existing); añadirla luego no genera UPDATE WAL para las
    filas viejas → histórico irrecuperable. Ahora readiness exige las `required` → el slot NO se
    crea, se espera con backoff."""
    make, slot, engine = capture
    with engine.begin() as c:
        c.execute(sa.text("CREATE SCHEMA IF NOT EXISTS partialcv"))
        # user_profiles SIN cv_text (columna REQUERIDA):
        c.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS partialcv.user_profiles ("
            "id uuid PRIMARY KEY, user_id uuid, title varchar(200), "
            "skills jsonb NOT NULL DEFAULT '[]'::jsonb, "
            "updated_at timestamptz NOT NULL DEFAULT now())"
        ))
    cap = make(tables="partialcv.user_profiles", ready_max_retries=2)
    with pytest.raises(RuntimeError, match="esquema legacy aún sin migrar"):
        cap.start()
    # Sin la columna requerida NO hay bootstrap: jamás se crea el slot.
    assert _scalar(
        engine, "SELECT count(*) FROM pg_replication_slots WHERE slot_name = :n", n=slot
    ) == 0


def test_readiness_blocks_when_jobs_source_missing(capture):
    """REGRESIÓN P1 rev. externa integral (calibración): `jobs.source` es REQUERIDA — sin ella un
    job fresco no resuelve fuente y el proyector lo DESCARTA en silencio ("sin fuente resoluble").
    Readiness debe bloquear el slot si falta."""
    make, slot, engine = capture
    with engine.begin() as c:
        c.execute(sa.text("CREATE SCHEMA IF NOT EXISTS partialsrc"))
        # jobs SIN source (columna REQUERIDA):
        c.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS partialsrc.jobs ("
            "hash varchar(32) PRIMARY KEY, title varchar(500), url varchar(2048), "
            "is_active boolean NOT NULL DEFAULT true, duplicate_of varchar(32))"
        ))
    cap = make(tables="partialsrc.jobs", ready_max_retries=2)
    with pytest.raises(RuntimeError, match="esquema legacy aún sin migrar"):
        cap.start()
    assert _scalar(
        engine, "SELECT count(*) FROM pg_replication_slots WHERE slot_name = :n", n=slot
    ) == 0


# ------------------------------------------- (h) healthcheck con slot inactivo


def _wait_slot_active(engine, slot: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _scalar(
            engine,
            "SELECT active FROM pg_replication_slots WHERE slot_name = :n",
            n=slot,
        ):
            return
        time.sleep(0.2)
    raise RuntimeError(f"slot {slot} no llegó a activarse en {timeout}s")


def test_health_check_unhealthy_when_slot_exists_but_inactive(
    capture, capture_db, monkeypatch, capsys
):
    """P3 healthcheck: mismo estado, mismo slot — la ÚNICA diferencia entre
    sano y no sano es `active` (consumidor conectado o caído). El inactivo
    legítimo del backfill del bootstrap lo cubre el start_period de 300s del
    compose, no este check."""
    make, slot, engine = capture
    monkeypatch.setenv("CORE_DATABASE_URL", capture_db["core_dsn"])
    monkeypatch.setenv("CORE_CAPTURE_SLOT", slot)

    cap = make()
    cap.start()  # bootstrap: frontera registrada + START_REPLICATION
    _wait_slot_active(engine, slot)
    assert health_check() == 0  # walsender conectado: sano

    cap.close()  # consumidor caído: el slot queda presente pero INACTIVO
    _wait_slot_released(slot)
    assert health_check() == 1
    err = capsys.readouterr().err
    assert "INACTIVO" in err  # motivo visible en stderr


def test_health_check_heartbeat_liveness_not_data_progress(
    capture, capture_db, monkeypatch, capsys
):
    """Regresión P2-7 (rev. externa parte 2): la evidencia del revisor era
    core-capture unhealthy con slot ACTIVO y 3 días sin tráfico legacy —
    `updated_at` solo avanza con txs aplicadas y el healthcheck lo puntuaba.
    Ahora la liveness es `heartbeat_at` (keepalive + tx aplicada): latido
    fresco sin tráfico ⇒ HEALTHY; latido viejo (consumidor zombi) ⇒
    unhealthy. `updated_at`/`last_applied_lsn` quedan como progreso de
    DATOS."""
    make, slot, engine = capture
    monkeypatch.setenv("CORE_DATABASE_URL", capture_db["core_dsn"])
    monkeypatch.setenv("CORE_CAPTURE_SLOT", slot)

    cap = make()
    cap.start()
    _wait_slot_active(engine, slot)

    # El falso negativo del revisor: 3 días sin tráfico (updated_at viejo)
    # con latido fresco ⇒ HEALTHY (antes: unhealthy por progreso estancado).
    _exec(
        engine,
        f"UPDATE {S}.shadow_capture_state "
        "SET updated_at = now() - interval '3 days'",
    )
    assert health_check() == 0

    # Latido viejo (> umbral 26h) ⇒ unhealthy con el motivo del LATIDO.
    _exec(
        engine,
        f"UPDATE {S}.shadow_capture_state "
        "SET heartbeat_at = now() - interval '27 hours'",
    )
    assert health_check() == 1
    assert "LATIDO" in capsys.readouterr().err


def test_health_check_staging_sin_drenar_es_unhealthy(
    capture, capture_db, monkeypatch, capsys
):
    """Auditoría B-1 (2026-08-22): la observabilidad del proyector vivía en el
    beat del MISMO worker que se caía — 22 días de staging sin drenar y ni una
    alerta. El chequeo del drenado vive ahora en el healthcheck de capture
    (OTRO contenedor): cambio pendiente más viejo que el umbral ⇒ unhealthy,
    aunque el worker esté colgado o su broker roto."""
    make, slot, engine = capture
    monkeypatch.setenv("CORE_DATABASE_URL", capture_db["core_dsn"])
    monkeypatch.setenv("CORE_CAPTURE_SLOT", slot)

    cap = make()
    cap.start()
    _wait_slot_active(engine, slot)
    assert health_check() == 0  # staging vacío ⇒ sano

    # Cambio SIN aplicar más viejo que el umbral ⇒ unhealthy con motivo.
    _exec(
        engine,
        f"INSERT INTO {S}.shadow_change_log "
        "(lsn, seq_in_tx, src_table, op, pk, payload, received_at) "
        "VALUES (999999999, 0, 'jobs', 'U', 'hc-test', '{}'::jsonb, "
        "now() - interval '3 hours')",
    )
    assert health_check() == 1
    assert "SIN DRENAR" in capsys.readouterr().err

    # Aplicado ⇒ vuelve a sano (el pendiente viejo era la única causa).
    _exec(
        engine,
        f"UPDATE {S}.shadow_change_log SET applied_at = now() "
        "WHERE pk = 'hc-test'",
    )
    assert health_check() == 0


def test_health_check_slot_sin_progreso_es_unhealthy(
    capture, capture_db, monkeypatch, capsys
):
    """I-1 (auditoría externa 2026-08-23): un capture con latido fresco pero
    que NO confirma flush (bug del feedback, stream atascado) dejaba el
    healthcheck verde mientras el slot retenía WAL sin límite en la BD
    COMPARTIDA. Ahora el lag del slot (pg_wal_lsn_diff frente a
    confirmed_flush_lsn) se puntúa contra el umbral ratificado en §8 (2 GiB).
    Umbral forzado a -1: cualquier lag (incluido 0) dispara — determinista,
    sin fabricar 2 GiB de WAL reales."""
    make, slot, engine = capture
    monkeypatch.setenv("CORE_DATABASE_URL", capture_db["core_dsn"])
    monkeypatch.setenv("CORE_CAPTURE_SLOT", slot)

    cap = make()
    cap.start()
    _wait_slot_active(engine, slot)
    assert health_check() == 0  # umbral por defecto (2 GiB): sano

    monkeypatch.setenv("CORE_CAPTURE_SLOT_LAG_MAX_BYTES", "-1")
    assert health_check() == 1
    assert "SIN PROGRESO" in capsys.readouterr().err


def test_heartbeat_advances_on_keepalive_without_traffic(capture):
    """P2-7: el latido avanza con los KEEPALIVES del stream aunque no llegue
    ninguna transacción — updated_at (progreso de datos) queda quieto."""
    make, slot, engine = capture
    cap = make(status_interval=0.1)
    cap.start()
    row0 = _rows(
        engine,
        f"SELECT heartbeat_at, updated_at FROM {S}.shadow_capture_state",
    )[0]
    assert row0.heartbeat_at is not None  # el bootstrap ya deja latido
    cap.stream(max_seconds=1.0)  # sin tráfico: solo keepalives
    row1 = _rows(
        engine,
        f"SELECT heartbeat_at, updated_at FROM {S}.shadow_capture_state",
    )[0]
    assert row1.heartbeat_at > row0.heartbeat_at  # latido VIVO sin tráfico
    assert row1.updated_at == row0.updated_at  # datos: intacto


def test_empty_tx_heartbeat_is_throttled(capture):
    """Fix del BUCLE DE COLA (2026-08-22, mecanismo real de B-1): un torrente
    de tx vacías late como máximo 1/s. Antes latía POR TX — un UPDATE por tx
    consumida que generaba a su vez otra tx WAL vacía: con retraso, consumo ≈
    generación y el catch-up no convergía NUNCA (11,5 h clavado tras un burst
    de 53 MB, con el heartbeat fresco mintiendo liveness)."""
    make, slot, engine = capture
    cap = make()
    cap._ack = False  # sin feedback: aquí solo interesa la rama del latido
    beats = []
    cap._touch_heartbeat = lambda: beats.append(1)  # sin DB: contamos llamadas

    class _Msg:  # commit wal2json mínimo (solo se usa data_start)
        data_start = 1

    for _ in range(50):
        cap._flush(_Msg())  # 50 tx vacías seguidas
    assert sum(beats) == 1  # ...un solo latido
    cap._last_hb -= 2.0  # "pasa" más de 1 s
    cap._flush(_Msg())
    assert sum(beats) == 2  # y vuelve a latir


# ------------------------------------------------------------ (e) roles/grants


def test_capture_role_and_enumerated_grants_on_shared_db():
    """Contra la BD COMPARTIDA (solo lectura de catálogos): el rol de
    replicación existe con EXACTAMENTE sus atributos y jobhunt_core tiene
    SELECT en las 4 tablas enumeradas de §1 y NADA más en public."""
    engine = sa.create_engine(_ADMIN, poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as c:
            row = c.execute(
                sa.text(
                    "SELECT rolcanlogin, rolreplication, rolsuper, rolcreatedb, "
                    "rolcreaterole, rolbypassrls FROM pg_roles "
                    "WHERE rolname = 'jobhunt_capture'"
                )
            ).one_or_none()
            assert row is not None, "rol jobhunt_capture ausente (bootstrap B-01)"
            assert row.rolcanlogin and row.rolreplication
            assert not (
                row.rolsuper or row.rolcreatedb or row.rolcreaterole or row.rolbypassrls
            )

            allowed = ("jobs", "user_profiles", "users", "match_results")
            if not c.execute(
                sa.text("SELECT to_regclass('public.jobs') IS NOT NULL")
            ).scalar():
                pytest.skip("BD sin esquema legacy migrado (public.jobs ausente)")
            for table in allowed:
                grants = c.execute(
                    sa.text(
                        "SELECT has_table_privilege('jobhunt_core', "
                        "('public.' || :t)::regclass, 'SELECT') AS can_read, "
                        "has_table_privilege('jobhunt_core', "
                        "('public.' || :t)::regclass, "
                        "'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') AS can_write"
                    ),
                    {"t": table},
                ).one()
                assert grants.can_read, f"SELECT enumerado ausente en public.{table}"
                assert not grants.can_write, f"escritura concedida en public.{table}"

            # NADA más: ninguna otra relación de public con privilegio alguno.
            others = c.execute(
                sa.text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind IN ('r','p','v','m','f') "
                    "AND c.relname != ALL(CAST(:allowed AS text[])) "
                    "AND (has_table_privilege('jobhunt_core', c.oid, "
                    "'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') "
                    "OR has_any_column_privilege('jobhunt_core', c.oid, "
                    "'SELECT,INSERT,UPDATE,REFERENCES'))"
                ),
                {"allowed": list(allowed)},
            ).scalars().all()
            assert others == [], f"privilegios fuera de la whitelist: {others}"
    finally:
        engine.dispose()


# -------------------------------------------------------- (f) ciclo core0008b


def test_core0008b_downgrade_upgrade_cycle_on_disposable_db():
    """head → core0008a → head sobre BD desechable con datos en las 3 tablas
    nuevas delante del downgrade (jamás la BD compartida de la suite)."""
    dbname = f"jobhunt_b01mig_{uuid.uuid4().hex[:12]}"
    parts = urlsplit(_ADMIN)
    db_url = urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))
    admin_engine = _admin_autocommit()
    with admin_engine.connect() as c:
        c.execute(sa.text(f'CREATE DATABASE "{dbname}"'))
    try:
        engine = sa.create_engine(db_url, poolclass=sa.pool.NullPool)
        with engine.begin() as c:
            c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            c.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{S}"'))
        run_alembic(_alembic_url(db_url), "upgrade", "head")

        with engine.begin() as c:
            assert (
                c.execute(
                    sa.text(f"SELECT version_num FROM {S}.alembic_version")
                ).scalar()
                == "core0031"
            )
            c.execute(
                sa.text(
                    f"INSERT INTO {S}.shadow_capture_state "
                    "(id, slot_name, snapshot_lsn, snapshot_exported_at, "
                    "last_applied_lsn) VALUES (1, 'jobhunt_shadow', 100, now(), 100)"
                )
            )
            # core0009: inbox sombra con PK(consumer_id, event_id) delante
            # del downgrade (dato real en la tabla nueva).
            c.execute(
                sa.text(
                    f"INSERT INTO {S}.shadow_inbox (consumer_id, event_id, "
                    "payload) VALUES ('tenant-x', gen_random_uuid(), '{}'::jsonb)"
                )
            )
            # Solo el primer literal es f-string: las llaves JSON de los
            # literales planos no se interpolan.
            c.execute(
                sa.text(
                    f"INSERT INTO {S}.shadow_change_log "
                    "(lsn, seq_in_tx, src_table, op, pk, payload, applied_at) VALUES "
                    "(100, 0, 'jobs', 'I', 'j-1', '{}'::jsonb, now()), "
                    "(105, 0, 'jobs', 'U', 'j-1', '{\"is_active\": false}'::jsonb, "
                    "NULL)"
                )
            )
            c.execute(
                sa.text(
                    f"INSERT INTO {S}.shadow_projection_batches "
                    "(first_lsn, last_lsn, min_received_at, changes, revisions_new) "
                    "VALUES (100, 105, now(), 2, 1)"
                )
            )

        # Guardas del esquema: fila única (id=1), op válido, y PK(lsn, seq)
        # idempotente con DO NOTHING (el mecanismo del ack tras commit).
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as c:
                c.execute(
                    sa.text(
                        f"INSERT INTO {S}.shadow_capture_state "
                        "(id, slot_name, snapshot_lsn, snapshot_exported_at, "
                        "last_applied_lsn) VALUES (2, 'otro', 1, now(), 1)"
                    )
                )
        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as c:
                c.execute(
                    sa.text(
                        f"INSERT INTO {S}.shadow_change_log "
                        "(lsn, seq_in_tx, src_table, op, pk) "
                        "VALUES (110, 0, 'jobs', 'X', 'j-2')"
                    )
                )
        with engine.begin() as c:
            c.execute(
                sa.text(
                    f"INSERT INTO {S}.shadow_change_log "
                    "(lsn, seq_in_tx, src_table, op, pk) "
                    "VALUES (105, 0, 'jobs', 'U', 'j-1') "
                    "ON CONFLICT (lsn, seq_in_tx) DO NOTHING"
                )
            )
            assert (
                c.execute(
                    sa.text(f"SELECT count(*) FROM {S}.shadow_change_log")
                ).scalar()
                == 2
            )
            # El índice parcial del proyector cubre SOLO lo no aplicado.
            assert (
                c.execute(
                    sa.text(
                        f"SELECT count(*) FROM {S}.shadow_change_log "
                        "WHERE applied_at IS NULL"
                    )
                ).scalar()
                == 1
            )

        run_alembic(_alembic_url(db_url), "downgrade", "core0008a")
        with engine.begin() as c:
            assert (
                c.execute(
                    sa.text(f"SELECT version_num FROM {S}.alembic_version")
                ).scalar()
                == "core0008a"
            )
            remaining = c.execute(
                sa.text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = :s "
                    "AND tablename IN ('shadow_capture_state', 'shadow_change_log', "
                    "'shadow_projection_batches', 'shadow_inbox')"
                ),
                {"s": S},
            ).scalar()
            assert remaining == 0  # downgrade limpio: 0008b + 0009 fuera
            still_b03 = c.execute(
                sa.text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = :s "
                    "AND tablename = 'labeled_sets'"
                ),
                {"s": S},
            ).scalar()
            assert still_b03 == 1  # core0008a intacta

        run_alembic(_alembic_url(db_url), "upgrade", "head")
        with engine.begin() as c:
            assert (
                c.execute(
                    sa.text(f"SELECT version_num FROM {S}.alembic_version")
                ).scalar()
                == "core0031"
            )
            idx = c.execute(
                sa.text(
                    "SELECT count(*) FROM pg_indexes WHERE schemaname = :s "
                    "AND indexname = 'ix_shadow_change_unapplied'"
                ),
                {"s": S},
            ).scalar()
            assert idx == 1  # índice parcial re-creado
            # Smoke real: el esquema re-creado funciona.
            c.execute(
                sa.text(
                    f"INSERT INTO {S}.shadow_capture_state "
                    "(id, slot_name, snapshot_lsn, snapshot_exported_at, "
                    "last_applied_lsn) VALUES (1, 'jobhunt_shadow', 7, now(), 7)"
                )
            )
        engine.dispose()
    finally:
        with admin_engine.connect() as c:
            c.execute(sa.text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        admin_engine.dispose()


# ------------------------------------------------- G1: H-6 y H-14e (unitarios)


def test_cambio_contractual_sin_pk_falla_fuerte_no_confirma():
    """Regresión G1 H-6: un cambio de tabla WHITELISTED cuyo mensaje no trae la
    PK (REPLICA IDENTITY inadecuada en el origen) se descartaba con un log y la
    tx se CONFIRMABA igual al slot — pérdida confirmada al origen. Error de
    configuración → RuntimeError: run() no lo reintenta, el contenedor cae
    visible y el slot retiene el WAL (recuperable), jamás un ack de datos no
    aplicados."""
    cap = ShadowCapture.__new__(ShadowCapture)  # sin __init__: solo _change_row
    cap._tx, cap._seq = [], 0
    with pytest.raises(RuntimeError, match="SIN PK"):
        cap._change_row(
            123,
            {"table": "jobs", "action": "D", "identity": None, "columns": None},
        )
    # Tabla NO contractual: sigue descartándose en silencio (defensa en
    # profundidad — add-tables ya filtra en el servidor).
    assert cap._change_row(
        124, {"table": "otra", "action": "I", "columns": None}
    ) is None


def test_backoff_escala_cuando_stream_falla_de_inmediato():
    """Regresión G1 H-14e: el backoff se reseteaba tras start() y ANTES de
    stream() — un stream que fallara nada más entrar reintentaba cada 1 s sin
    escalar jamás. Ahora solo se resetea si el intento sobrevivió
    _BACKOFF_RESET_AFTER_S: el fallo inmediato repetido escala 1→2→4→8."""
    import psycopg2 as _psycopg2

    cap = ShadowCapture.__new__(ShadowCapture)
    cap._stop = False
    sleeps: list[float] = []

    def fake_stream():
        raise _psycopg2.OperationalError("boom inmediato")

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 4:
            cap._stop = True

    cap.start = lambda: None
    cap.stream = fake_stream
    cap.close = lambda: None
    cap._sleep = fake_sleep
    cap.run()
    assert sleeps == [1.0, 2.0, 4.0, 8.0]  # antes: [1.0, 1.0, 1.0, 1.0]
