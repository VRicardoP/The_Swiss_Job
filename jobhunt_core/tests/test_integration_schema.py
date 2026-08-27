"""A-02 — invariantes del manifiesto [A] contra Postgres real (rollback siempre).

Se ejecutan vía el contenedor de migración (DB accesible):

    docker compose -f docker-compose.yml -f docker-compose.dev.yml \
    run --rm core-migrate python -m pytest jobhunt_core/tests -q
(sin los dos `-f` se probaría el código de la IMAGEN, no el del árbol de trabajo)

Conectan como el ROL DEL CORE (no admin): prueban el esquema tal y como lo verá
el servicio. Cada test abre su transacción y hace rollback: sin residuos.
"""

import os
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.config import settings

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)

_SYNC_URL = settings.CORE_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture()
def conn():
    engine = sa.create_engine(_SYNC_URL, poolclass=sa.pool.NullPool)
    with engine.connect() as c:
        yield c
        c.rollback()
    engine.dispose()


def _seed_corpus(conn) -> dict:
    """Grafo mínimo: source → vacancy → incarnation → slrev → offer_revision,
    resolviendo la FK circular por el orden del contrato (insert → UPDATE)."""
    ids = {k: uuid.uuid4() for k in ("source", "listing", "vacancy", "inc", "slrev", "offrev")}
    conn.execute(
        sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :n, 1)"),
        {"id": ids["source"], "n": f"src-{ids['source'].hex[:8]}"},
    )
    conn.execute(
        sa.text(
            "INSERT INTO source_listings (id, source_id, external_id, url_normalized) "
            "VALUES (:id, :s, :e, :u)"
        ),
        {"id": ids["listing"], "s": ids["source"], "e": ids["listing"].hex, "u": f"https://x/{ids['listing'].hex}"},
    )
    conn.execute(sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": ids["vacancy"]})
    conn.execute(
        sa.text(
            "INSERT INTO source_listing_incarnations (id, source_listing_id, vacancy_id, seq, url) "
            "VALUES (:id, :l, :v, 1, 'https://x/1')"
        ),
        {"id": ids["inc"], "l": ids["listing"], "v": ids["vacancy"]},
    )
    conn.execute(
        sa.text(
            "INSERT INTO source_listing_revisions (id, incarnation_id, content_hash, raw) "
            "VALUES (:id, :i, 'ch1', '{}'::jsonb)"
        ),
        {"id": ids["slrev"], "i": ids["inc"]},
    )
    conn.execute(
        sa.text(
            "INSERT INTO offer_revisions (id, vacancy_id, content_hash, text_hash, content) "
            "VALUES (:id, :v, 'ch1', 'th1', '{}'::jsonb)"
        ),
        {"id": ids["offrev"], "v": ids["vacancy"]},
    )
    # Cierre del ciclo: UPDATE de los punteros (mismo patrón que usará A-05).
    conn.execute(
        sa.text(
            "UPDATE vacancies SET current_offer_revision_id = :o, primary_incarnation_id = :i "
            "WHERE id = :v"
        ),
        {"o": ids["offrev"], "i": ids["inc"], "v": ids["vacancy"]},
    )
    return ids


def test_circular_pointers_settle_via_update(conn):
    ids = _seed_corpus(conn)
    row = conn.execute(
        sa.text(
            "SELECT current_offer_revision_id, primary_incarnation_id FROM vacancies WHERE id=:v"
        ),
        {"v": ids["vacancy"]},
    ).one()
    assert row == (ids["offrev"], ids["inc"])


def test_only_one_active_incarnation_per_slot(conn):
    ids = _seed_corpus(conn)
    with pytest.raises(sa.exc.IntegrityError, match="uq_incarnation_active"):
        conn.execute(
            sa.text(
                "INSERT INTO source_listing_incarnations "
                "(source_listing_id, vacancy_id, seq, url) VALUES (:l, :v, 2, 'u')"
            ),
            {"l": ids["listing"], "v": ids["vacancy"]},
        )


def test_recycled_slot_opens_new_incarnation(conn):
    ids = _seed_corpus(conn)
    conn.execute(
        sa.text("UPDATE source_listing_incarnations SET ended_at = now() WHERE id = :i"),
        {"i": ids["inc"]},
    )
    v2 = uuid.uuid4()
    conn.execute(sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": v2})
    conn.execute(  # cerrada la anterior, el slot puede reabrirse hacia OTRA vacante
        sa.text(
            "INSERT INTO source_listing_incarnations "
            "(source_listing_id, vacancy_id, seq, url) VALUES (:l, :v, 2, 'u2')"
        ),
        {"l": ids["listing"], "v": v2},
    )


def test_dedup_pair_is_canonical(conn):
    ids = _seed_corpus(conn)
    v2 = uuid.uuid4()
    conn.execute(sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": v2})
    conn.execute(
        sa.text("INSERT INTO dedup_candidates (vacancy_a, vacancy_b) VALUES (:a, :b)"),
        {"a": ids["vacancy"], "b": v2},
    )
    with pytest.raises(sa.exc.IntegrityError, match="uq_dedup_pair"):
        conn.execute(  # el par ESPEJADO es el mismo candidato
            sa.text("INSERT INTO dedup_candidates (vacancy_a, vacancy_b) VALUES (:a, :b)"),
            {"a": v2, "b": ids["vacancy"]},
        )


def _seed_profile(conn) -> dict:
    ids = {k: uuid.uuid4() for k in ("consumer", "profile", "prev", "model", "policy")}
    conn.execute(
        sa.text("INSERT INTO consumers (id, name) VALUES (:id, :n)"),
        {"id": ids["consumer"], "n": f"c-{ids['consumer'].hex[:8]}"},
    )
    conn.execute(
        sa.text("INSERT INTO profiles (id, consumer_id, external_ref) VALUES (:id, :c, 'u1')"),
        {"id": ids["profile"], "c": ids["consumer"]},
    )
    conn.execute(
        sa.text(
            "INSERT INTO profile_revisions (id, profile_id, content, content_hash, text_hash) "
            "VALUES (:id, :p, '{}'::jsonb, 'ch1', 'th1')"
        ),
        {"id": ids["prev"], "p": ids["profile"]},
    )
    conn.execute(
        sa.text(
            "INSERT INTO embedding_models (id, name, version, dim) VALUES (:id, :n, '1', 384)"
        ),
        {"id": ids["model"], "n": f"m-{ids['model'].hex[:8]}"},
    )
    conn.execute(
        sa.text(
            "INSERT INTO scoring_policies (id, name, prompt_version) VALUES (:id, :n, 'v1')"
        ),
        {"id": ids["policy"], "n": f"p-{ids['policy'].hex[:8]}"},
    )
    return ids


def _insert_eval(conn, c, p, eval_id=None) -> uuid.UUID:
    eval_id = eval_id or uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO match_evaluations (id, profile_id, vacancy_id, offer_revision_id, "
            "profile_revision_id, model_id, scoring_policy_id, eval_key, score_final, scores) "
            "VALUES (:id, :pr, :v, :orv, :prev, :m, :pol, :key, 80.5, '{}'::jsonb)"
        ),
        {
            "id": eval_id, "pr": p["profile"], "v": c["vacancy"], "orv": c["offrev"],
            "prev": p["prev"], "m": p["model"], "pol": p["policy"], "key": eval_id.hex,
        },
    )
    return eval_id


def test_composite_fk_rejects_foreign_revision(conn):
    """Una evaluación NO puede citar la offer_revision de OTRA vacante (rev. #3)."""
    c1, p = _seed_corpus(conn), _seed_profile(conn)
    other_vacancy = uuid.uuid4()
    conn.execute(sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": other_vacancy})
    with pytest.raises(sa.exc.IntegrityError, match="fk_eval_offrev_same_vacancy"):
        conn.execute(
            sa.text(
                "INSERT INTO match_evaluations (profile_id, vacancy_id, offer_revision_id, "
                "profile_revision_id, model_id, scoring_policy_id, eval_key, score_final, scores) "
                "VALUES (:pr, :v, :orv, :prev, :m, :pol, 'k1', 1, '{}'::jsonb)"
            ),
            {
                "pr": p["profile"], "v": other_vacancy, "orv": c1["offrev"],  # ← de la vacante 1
                "prev": p["prev"], "m": p["model"], "pol": p["policy"],
            },
        )


def test_current_eval_restrict_enforces_adr03(conn):
    """La evaluación VIGENTE no puede borrarse (RESTRICT impone el ADR-03)."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    eval_id = _insert_eval(conn, c, p)
    conn.execute(
        sa.text(
            "INSERT INTO profile_vacancy_state (profile_id, vacancy_id, current_eval_id) "
            "VALUES (:pr, :v, :e)"
        ),
        {"pr": p["profile"], "v": c["vacancy"], "e": eval_id},
    )
    with pytest.raises(sa.exc.IntegrityError, match="fk_pvs_current_eval_same_pair"):
        conn.execute(sa.text("DELETE FROM match_evaluations WHERE id = :e"), {"e": eval_id})


def test_ors_trigger_rejects_foreign_raw(conn):
    """El trigger impide enlazar raw de OTRA vacante a una offer_revision."""
    c1 = _seed_corpus(conn)
    c2 = _seed_corpus(conn)  # segundo grafo independiente
    with pytest.raises(sa.exc.DBAPIError, match="pertenece a otra vacante"):
        conn.execute(
            sa.text(
                "INSERT INTO offer_revision_sources "
                "(offer_revision_id, source_listing_revision_id, vacancy_id) "
                "VALUES (:o, :sr, :v)"
            ),
            {"o": c1["offrev"], "sr": c2["slrev"], "v": c1["vacancy"]},  # raw ajeno
        )


def _create_model_partition(conn, model_id) -> None:
    """Regla operativa del contrato: cada modelo registra SU partición (A-06+)."""
    conn.execute(
        sa.text(
            f"CREATE TABLE offer_embeddings_{model_id.hex[:8]} "
            f"PARTITION OF offer_embeddings FOR VALUES IN ('{model_id}')"
        )
    )


def test_offer_embedding_vector_and_operator(conn):
    """vector(384) + operador <=> operativos en offer_embeddings (A-02→A-06)."""
    _seed_corpus(conn)
    p = _seed_profile(conn)
    _create_model_partition(conn, p["model"])
    vec = "[" + ",".join(["0.1"] * 384) + "]"
    conn.execute(
        sa.text(
            "INSERT INTO offer_embeddings (text_hash, model_id, vector) VALUES ('th1', :m, :v)"
        ),
        {"m": p["model"], "v": vec},
    )
    dist = conn.execute(
        sa.text("SELECT vector <=> :v FROM offer_embeddings WHERE text_hash = 'th1'"),
        {"v": vec},
    ).scalar()
    assert dist == 0.0
    # UNIQUE (text_hash, model_id): un solo vector por texto/modelo (ADR-02).
    # (En la tabla particionada el PK se materializa por partición con nombre
    # autogenerado offer_embeddings_<hex>_pkey.)
    with pytest.raises(
        sa.exc.IntegrityError, match=r"pk_offer_embeddings|offer_embeddings_\w+_pkey"
    ):
        conn.execute(
            sa.text(
                "INSERT INTO offer_embeddings (text_hash, model_id, vector) VALUES ('th1', :m, :v)"
            ),
            {"m": p["model"], "v": vec},
        )


# ---------- Auditoría A-02: invariantes contractuales adicionales ----------


def test_eval_unique_per_profile_vacancy_key(conn):
    """UNIQUE(profile, vacancy, eval_key): el invariante append-only de A-08."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    e1 = _insert_eval(conn, c, p)
    dup = uuid.uuid4()
    with pytest.raises(sa.exc.IntegrityError, match="uq_eval_profile_vacancy_key"):
        conn.execute(
            sa.text(
                "INSERT INTO match_evaluations (id, profile_id, vacancy_id, offer_revision_id, "
                "profile_revision_id, model_id, scoring_policy_id, eval_key, score_final, scores) "
                "VALUES (:id, :pr, :v, :orv, :prev, :m, :pol, :key, 10, '{}'::jsonb)"
            ),
            {
                "id": dup, "pr": p["profile"], "v": c["vacancy"], "orv": c["offrev"],
                "prev": p["prev"], "m": p["model"], "pol": p["policy"], "key": e1.hex,
            },
        )


def test_profile_embedding_rejects_foreign_profile_revision(conn):
    """fk_pemb_rev_same_profile: un embedding no cita la revisión de OTRO perfil."""
    p1, p2 = _seed_profile(conn), _seed_profile(conn)
    vec = "[" + ",".join(["0.1"] * 384) + "]"
    with pytest.raises(sa.exc.IntegrityError, match="fk_pemb_rev_same_profile"):
        conn.execute(
            sa.text(
                "INSERT INTO profile_embeddings (profile_revision_id, profile_id, model_id, vector) "
                "VALUES (:rev, :p, :m, :v)"
            ),
            {"rev": p1["prev"], "p": p2["profile"], "m": p1["model"], "v": vec},  # rev ajena
        )


def test_eval_rejects_foreign_profile_revision(conn):
    """fk_eval_profrev_same_profile: el lado PERFIL de la integridad de propietario."""
    c, p1, p2 = _seed_corpus(conn), _seed_profile(conn), _seed_profile(conn)
    with pytest.raises(sa.exc.IntegrityError, match="fk_eval_profrev_same_profile"):
        conn.execute(
            sa.text(
                "INSERT INTO match_evaluations (profile_id, vacancy_id, offer_revision_id, "
                "profile_revision_id, model_id, scoring_policy_id, eval_key, score_final, scores) "
                "VALUES (:pr, :v, :orv, :prev, :m, :pol, 'k2', 1, '{}'::jsonb)"
            ),
            {
                "pr": p2["profile"], "v": c["vacancy"], "orv": c["offrev"],
                "prev": p1["prev"],  # ← revisión del perfil 1 con profile_id del 2
                "m": p1["model"], "pol": p1["policy"],
            },
        )


def test_pvs_rejects_current_eval_of_other_pair(conn):
    """fk_pvs_current_eval_same_pair: la eval vigente debe ser del MISMO par."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    eval_id = _insert_eval(conn, c, p)
    other_vacancy = uuid.uuid4()
    conn.execute(sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": other_vacancy})
    with pytest.raises(sa.exc.IntegrityError, match="fk_pvs_current_eval_same_pair"):
        conn.execute(
            sa.text(
                "INSERT INTO profile_vacancy_state (profile_id, vacancy_id, current_eval_id) "
                "VALUES (:pr, :v, :e)"
            ),
            {"pr": p["profile"], "v": other_vacancy, "e": eval_id},  # eval de OTRO par
        )


def test_vacancy_pointer_rejects_foreign_revision(conn):
    """fk_vacancy_current_offrev: el puntero no acepta la revisión de OTRA vacante."""
    c1, c2 = _seed_corpus(conn), _seed_corpus(conn)
    with pytest.raises(sa.exc.IntegrityError, match="fk_vacancy_current_offrev"):
        conn.execute(
            sa.text("UPDATE vacancies SET current_offer_revision_id = :o WHERE id = :v"),
            {"o": c2["offrev"], "v": c1["vacancy"]},  # revisión de la vacante 2
        )


def test_pointer_set_null_only_nulls_pointer(conn):
    """ON DELETE SET NULL (col) por-columna: borrar la revisión apuntada nulifica
    SOLO el puntero, no la PK (fix de la auditoría A-02)."""
    c = _seed_corpus(conn)
    # Quitar el otro enlace del grafo para poder borrar la offer_revision.
    conn.execute(sa.text("DELETE FROM offer_revisions WHERE id = :o"), {"o": c["offrev"]})
    row = conn.execute(
        sa.text("SELECT id, current_offer_revision_id FROM vacancies WHERE id = :v"),
        {"v": c["vacancy"]},
    ).one()
    assert row.id == c["vacancy"]  # la PK sobrevive
    assert row.current_offer_revision_id is None  # el puntero se nulifica


# -------- Rev. externa A-02: inmutabilidad de bindings + HNSW por modelo --------


def test_slr_incarnation_id_is_immutable(conn):
    """Mutar el binding revisión→incarnación tras el insert rompería la
    coherencia ORS en silencio → prohibido a nivel de BD (rev. A-02 #1)."""
    c1, c2 = _seed_corpus(conn), _seed_corpus(conn)
    with pytest.raises(sa.exc.DBAPIError, match="columna inmutable"):
        conn.execute(
            sa.text("UPDATE source_listing_revisions SET incarnation_id = :i WHERE id = :r"),
            {"i": c2["inc"], "r": c1["slrev"]},
        )


def test_incarnation_vacancy_id_is_immutable(conn):
    """ADR-04: una incarnación CONSERVA su vacante (merge=merged_into;
    reciclado=nueva incarnación) — el re-apunte directo está prohibido."""
    c1, c2 = _seed_corpus(conn), _seed_corpus(conn)
    with pytest.raises(sa.exc.DBAPIError, match="columna inmutable"):
        conn.execute(
            sa.text("UPDATE source_listing_incarnations SET vacancy_id = :v WHERE id = :i"),
            {"v": c2["vacancy"], "i": c1["inc"]},
        )


def test_offer_revision_vacancy_id_is_immutable(conn):
    """offer_revisions es inmutable (ADR-02): su vacancy_id no se re-apunta."""
    c1, c2 = _seed_corpus(conn), _seed_corpus(conn)
    # Sin dependientes la FK no lo bloquearía: crea una revisión suelta.
    loose = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO offer_revisions (id, vacancy_id, content_hash, text_hash, content) "
            "VALUES (:id, :v, 'ch2', 'th2', '{}'::jsonb)"
        ),
        {"id": loose, "v": c1["vacancy"]},
    )
    with pytest.raises(sa.exc.DBAPIError, match="columna inmutable"):
        conn.execute(
            sa.text("UPDATE offer_revisions SET vacancy_id = :v WHERE id = :o"),
            {"v": c2["vacancy"], "o": loose},
        )


def test_primary_incarnation_rejects_foreign_and_sets_null(conn):
    """fk_vacancy_primary_incarnation: rechaza incarnación de OTRA vacante; y su
    SET NULL por-columna nulifica SOLO el puntero al borrar la apuntada."""
    c1, c2 = _seed_corpus(conn), _seed_corpus(conn)
    with pytest.raises(sa.exc.IntegrityError, match="fk_vacancy_primary_incarnation"):
        conn.execute(
            sa.text("UPDATE vacancies SET primary_incarnation_id = :i WHERE id = :v"),
            {"i": c2["inc"], "v": c1["vacancy"]},
        )


def test_primary_incarnation_set_null_only_nulls_pointer(conn):
    c = _seed_corpus(conn)
    # Incarnación fresca sin revisiones (borrable): cerrar la activa y abrir otra.
    conn.execute(
        sa.text("UPDATE source_listing_incarnations SET ended_at = now() WHERE id = :i"),
        {"i": c["inc"]},
    )
    fresh = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO source_listing_incarnations (id, source_listing_id, vacancy_id, seq, url) "
            "VALUES (:id, :l, :v, 2, 'u2')"
        ),
        {"id": fresh, "l": c["listing"], "v": c["vacancy"]},
    )
    conn.execute(
        sa.text("UPDATE vacancies SET primary_incarnation_id = :i WHERE id = :v"),
        {"i": fresh, "v": c["vacancy"]},
    )
    conn.execute(sa.text("DELETE FROM source_listing_incarnations WHERE id = :i"), {"i": fresh})
    row = conn.execute(
        sa.text("SELECT id, primary_incarnation_id FROM vacancies WHERE id = :v"),
        {"v": c["vacancy"]},
    ).one()
    assert row.id == c["vacancy"] and row.primary_incarnation_id is None


def test_ors_composite_fk_rejects_mismatched_revision(conn):
    """fk_ors_offrev_same_vacancy: aunque el trigger pase (raw coherente con
    NEW.vacancy_id), la FK compuesta exige que la offer_revision sea de ESA vacante."""
    c1, c2 = _seed_corpus(conn), _seed_corpus(conn)
    with pytest.raises(sa.exc.IntegrityError, match="fk_ors_offrev_same_vacancy"):
        conn.execute(
            sa.text(
                "INSERT INTO offer_revision_sources "
                "(offer_revision_id, source_listing_revision_id, vacancy_id) "
                "VALUES (:o, :sr, :v)"
            ),
            # raw y vacancy_id coherentes (c2) → el trigger pasa; la revisión es de c1.
            {"o": c1["offrev"], "sr": c2["slrev"], "v": c2["vacancy"]},
        )


def test_offer_embeddings_partitioned_hnsw_per_model(conn):
    """Contrato 'HNSW por model': tabla PARTICIONADA por model_id; índice padre
    HNSW+coseno; cada partición hereda SU índice; vector(384) exacto."""
    relkind = conn.execute(
        sa.text(
            "SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'jobhunt' AND c.relname = 'offer_embeddings'"
        )
    ).scalar()
    assert relkind == "p"  # partitioned table

    am, opc = conn.execute(
        sa.text(
            "SELECT am.amname, opc.opcname FROM pg_index i "
            "JOIN pg_class ic ON ic.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = ic.relam "
            "JOIN pg_opclass opc ON opc.oid = i.indclass[0] "
            "WHERE ic.relname = 'ix_offemb_vector_hnsw'"
        )
    ).one()
    assert (am, opc) == ("hnsw", "vector_cosine_ops")

    coltype = conn.execute(
        sa.text(
            "SELECT format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'jobhunt' AND c.relname = 'offer_embeddings' "
            "AND a.attname = 'vector'"
        )
    ).scalar()
    assert coltype == "vector(384)"

    # Una partición nueva hereda automáticamente su propio índice HNSW.
    p = _seed_profile(conn)
    _create_model_partition(conn, p["model"])
    inherited = conn.execute(
        sa.text(
            "SELECT count(*) FROM pg_index i "
            "JOIN pg_class ic ON ic.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = ic.relam "
            "JOIN pg_class tc ON tc.oid = i.indrelid "
            "WHERE tc.relname = :part AND am.amname = 'hnsw'"
        ),
        {"part": f"offer_embeddings_{p['model'].hex[:8]}"},
    ).scalar()
    assert inherited == 1


def test_erase_ack_unique_per_consumer(conn):
    """pk_erase_acks: un solo ack por (erase, consumidor) — GDPR ADR-07."""
    p = _seed_profile(conn)
    erase_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO erase_requests (id, subject_profile_id, required_consumers) "
            "VALUES (:id, :p, '[]'::jsonb)"
        ),
        {"id": erase_id, "p": p["profile"]},
    )
    conn.execute(
        sa.text("INSERT INTO erase_acks (erase_id, consumer_id) VALUES (:e, :c)"),
        {"e": erase_id, "c": p["consumer"]},
    )
    with pytest.raises(sa.exc.IntegrityError, match="pk_erase_acks"):
        conn.execute(
            sa.text("INSERT INTO erase_acks (erase_id, consumer_id) VALUES (:e, :c)"),
            {"e": erase_id, "c": p["consumer"]},
        )


# ---------- C-ESQ (core0011): candidaturas, búsquedas guardadas, idempotencia ----------


def _insert_application(conn, c, p, app_id=None, incarnation=None) -> uuid.UUID:
    app_id = app_id or uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO applications "
            "(id, profile_id, vacancy_id, source_listing_incarnation_id, snapshot) "
            "VALUES (:id, :pr, :v, :i, '{}'::jsonb)"
        ),
        {"id": app_id, "pr": p["profile"], "v": c["vacancy"], "i": incarnation},
    )
    return app_id


def test_application_unique_per_profile_vacancy(conn):
    """UNIQUE(profile_id, vacancy_id): una sola candidatura por par (§1)."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    _insert_application(conn, c, p)
    with pytest.raises(sa.exc.IntegrityError, match="uq_application_profile_vacancy"):
        _insert_application(conn, c, p)


def test_application_status_enum_starts_applied(conn):
    """status[start=applied] (§1): default del ENUM + valores exactos del contrato."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    app_id = _insert_application(conn, c, p)
    status = conn.execute(
        sa.text("SELECT status FROM applications WHERE id = :id"), {"id": app_id}
    ).scalar()
    assert status == "applied"
    labels = conn.execute(
        sa.text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "JOIN pg_namespace n ON n.oid = t.typnamespace "
            "WHERE t.typname = 'application_status' AND n.nspname = :s "
            "ORDER BY e.enumsortorder"
        ),
        {"s": settings.CORE_DB_SCHEMA},
    ).scalars().all()
    assert labels == [
        "saved", "applied", "phone_screen", "technical",
        "interview", "offer", "rejected", "withdrawn",
    ]


def test_application_rejects_status_outside_enum(conn):
    c, p = _seed_corpus(conn), _seed_profile(conn)
    with pytest.raises(sa.exc.DataError, match="application_status"):
        conn.execute(
            sa.text(
                "INSERT INTO applications (profile_id, vacancy_id, status) "
                "VALUES (:pr, :v, 'ghosted')"
            ),
            {"pr": p["profile"], "v": c["vacancy"]},
        )


def test_application_composite_fk_rejects_foreign_incarnation(conn):
    """fk_application_incarnation_same_vacancy: la incarnación citada debe ser
    de ESA vacante — la mezcla se rechaza (§1 #2)."""
    c1, c2, p = _seed_corpus(conn), _seed_corpus(conn), _seed_profile(conn)
    with pytest.raises(
        sa.exc.IntegrityError, match="fk_application_incarnation_same_vacancy"
    ):
        _insert_application(conn, c1, p, incarnation=c2["inc"])  # inc AJENA


def test_application_incarnation_set_null_only_nulls_pointer(conn):
    """SET NULL por-columna: borrar la incarnación apuntada nulifica SOLO el
    puntero — la candidatura (dato de usuario) sobrevive intacta (PF.3)."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    # Incarnación fresca sin revisiones (borrable), patrón del test de vacancies.
    conn.execute(
        sa.text("UPDATE source_listing_incarnations SET ended_at = now() WHERE id = :i"),
        {"i": c["inc"]},
    )
    fresh = uuid.uuid4()
    conn.execute(
        sa.text(
            "INSERT INTO source_listing_incarnations (id, source_listing_id, vacancy_id, seq, url) "
            "VALUES (:id, :l, :v, 2, 'u2')"
        ),
        {"id": fresh, "l": c["listing"], "v": c["vacancy"]},
    )
    app_id = _insert_application(conn, c, p, incarnation=fresh)
    conn.execute(
        sa.text("DELETE FROM source_listing_incarnations WHERE id = :i"), {"i": fresh}
    )
    row = conn.execute(
        sa.text(
            "SELECT vacancy_id, source_listing_incarnation_id FROM applications "
            "WHERE id = :id"
        ),
        {"id": app_id},
    ).one()
    assert row.vacancy_id == c["vacancy"]  # la columna NOT NULL sobrevive
    assert row.source_listing_incarnation_id is None  # el puntero se nulifica


def test_status_event_requires_application_and_cascades(conn):
    """application_status_events cuelga de applications: FK obligatoria y el
    borrado del padre (erase GDPR) arrastra su historial (CASCADE).

    El raises va AL FINAL: tras un IntegrityError la transacción del test
    queda abortada (misma disciplina que el resto del fichero)."""
    c, p = _seed_corpus(conn), _seed_profile(conn)
    app_id = _insert_application(conn, c, p)
    conn.execute(
        sa.text(
            "INSERT INTO application_status_events (application_id, status) "
            "VALUES (:a, 'phone_screen')"
        ),
        {"a": app_id},
    )
    conn.execute(sa.text("DELETE FROM applications WHERE id = :a"), {"a": app_id})
    left = conn.execute(
        sa.text(
            "SELECT count(*) FROM application_status_events WHERE application_id = :a"
        ),
        {"a": app_id},
    ).scalar()
    assert left == 0
    with pytest.raises(sa.exc.IntegrityError, match="application_status_events"):
        conn.execute(
            sa.text(
                "INSERT INTO application_status_events (application_id, status) "
                "VALUES (:a, 'applied')"
            ),
            {"a": uuid.uuid4()},  # candidatura inexistente
        )


def test_saved_search_defaults_and_profile_fk(conn):
    """saved_searches: defaults del modelo origen (filters {}, min_score 0,
    daily, push, activa, 0 matches) + FK a profiles obligatoria (raises AL
    FINAL — tras el IntegrityError la transacción queda abortada)."""
    p = _seed_profile(conn)
    sid = uuid.uuid4()
    conn.execute(
        sa.text("INSERT INTO saved_searches (id, profile_id, name) VALUES (:id, :p, 'remote py')"),
        {"id": sid, "p": p["profile"]},
    )
    row = conn.execute(
        sa.text(
            "SELECT filters::text, min_score, notify_frequency, notify_push, "
            "is_active, last_run_at, total_matches FROM saved_searches WHERE id = :id"
        ),
        {"id": sid},
    ).one()
    assert row == ("{}", 0, "daily", True, True, None, 0)
    with pytest.raises(sa.exc.IntegrityError, match="saved_searches"):
        conn.execute(
            sa.text("INSERT INTO saved_searches (profile_id, name) VALUES (:p, 'x')"),
            {"p": uuid.uuid4()},  # perfil inexistente
        )


def test_idempotency_pk_blocks_replay(conn):
    """pk_idempotency_records (consumer_id, key, route): el reintento con la
    misma key sobre la misma ruta NO crea segunda fila; otra ruta u otro
    consumer sí son registros distintos (PLAN §4 / §15bis)."""
    p1 = _seed_profile(conn)
    ins = sa.text(
        "INSERT INTO idempotency_records "
        "(consumer_id, key, route, request_hash, expires_at) "
        "VALUES (:c, :k, :r, 'h1', now() + interval '1 day')"
    )
    conn.execute(ins, {"c": p1["consumer"], "k": "k1", "r": "PUT /v1/profiles/x"})
    # Mismo candado exacto → bloqueado.
    with pytest.raises(sa.exc.IntegrityError, match="pk_idempotency_records"):
        conn.execute(ins, {"c": p1["consumer"], "k": "k1", "r": "PUT /v1/profiles/x"})


def test_idempotency_scoped_by_consumer_and_route(conn):
    p1, p2 = _seed_profile(conn), _seed_profile(conn)
    ins = sa.text(
        "INSERT INTO idempotency_records "
        "(consumer_id, key, route, request_hash, expires_at) "
        "VALUES (:c, :k, :r, 'h1', now() + interval '1 day')"
    )
    conn.execute(ins, {"c": p1["consumer"], "k": "k1", "r": "PUT /v1/profiles/x"})
    conn.execute(ins, {"c": p1["consumer"], "k": "k1", "r": "POST /v1/applications"})
    conn.execute(ins, {"c": p2["consumer"], "k": "k1", "r": "PUT /v1/profiles/x"})
    # Índice de purga por expiración presente (DoD C-ESQ).
    idx = conn.execute(
        sa.text(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = :s "
            "AND indexname = 'ix_idem_expires_at'"
        ),
        {"s": settings.CORE_DB_SCHEMA},
    ).scalar()
    assert idx == 1


def test_las_guardas_del_oraculo_disparan_tambien_en_modo_replica(conn):
    """REGRESIÓN auditoría G10 P2-3 (migración core0034).

    Los `CREATE TRIGGER` nacen con `tgenabled = 'O'` (*origin*), que es el default, y un
    `SET session_replication_role = 'replica'` —sentencia de SESIÓN, no DDL— los deja
    inertes SIN tocar el catálogo: no deja rastro alguno, a diferencia del
    `ALTER TABLE … DISABLE TRIGGER` que las propias migraciones documentan como vía de
    desmontaje legítima («DDL del owner, con rastro»). Verificado en base desechable: con
    las cuatro en 'O', un `SET session_replication_role='replica'; TRUNCATE
    labeled_dedup_pairs;` vaciaba el oráculo SELLADO dejando el sello intacto — y con los
    pares a cero las métricas de dedup salen con el centinela `no_data` y el gate se pone
    rojo sin causa visible, que es literalmente lo que core0033 vino a impedir.

    Solo un superusuario puede hacerlo (`jobhunt_core` recibe `permission denied to set
    parameter`), pero `swissjob` —el rol con el que se opera esta caja y el de los
    runbooks— lo es, y el modelo de amenaza de la guarda es justamente el borrado masivo
    accidental del operador. Por eso el test mira el CATÁLOGO: el rol de la suite no puede
    montar el ataque, pero sí comprobar que la guarda está en el modo que lo bloquea.
    """
    filas = conn.execute(
        sa.text(
            "SELECT c.relname || '.' || t.tgname AS guarda, t.tgenabled "
            "FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND NOT t.tgisinternal "
            "AND c.relname IN ('labeled_dedup_pairs', 'labeled_dedup_cohorts') "
            "ORDER BY 1"
        ),
        {"s": settings.CORE_DB_SCHEMA},
    ).all()
    assert [f.guarda for f in filas] == [
        "labeled_dedup_cohorts.labeled_dedup_cohorts_frozen_guard",
        "labeled_dedup_cohorts.labeled_dedup_cohorts_truncate_guard",
        "labeled_dedup_pairs.labeled_dedup_pairs_frozen_guard",
        "labeled_dedup_pairs.labeled_dedup_pairs_truncate_guard",
    ]
    assert [f.tgenabled for f in filas] == ["A"] * 4, filas  # 'A' = ENABLE ALWAYS


def test_las_guardas_de_inmutabilidad_disparan_tambien_en_modo_replica(conn):
    """auditoría G11 P2-3 (migración core0035): lo que a core0034 le sobraba por acotar.

    core0034 dejó las otras diez guardas en 'O' con un motivo FALSO: `pg_restore
    --disable-triggers` no usa `session_replication_role`, sino `ALTER TABLE … DISABLE
    TRIGGER ALL` (verificado sobre el `pg_dump` 16.14 del clúster real), que desactiva
    también las 'A' — o sea que `ENABLE ALWAYS` nunca le habría estorbado. Estas tres
    guardan columnas INMUTABLES por ADR-04 y disparan SOLO en UPDATE, que es algo que un
    restore data-only (`COPY`/`INSERT`) no hace jamás: no rozan ninguna maniobra legítima
    y cierran la vía de la sesión en réplica, donde un UPDATE masivo de `vacancy_id`
    reasignaba revisiones e incarnaciones sin que nada lo impidiera ni dejara rastro.
    """
    filas = conn.execute(
        sa.text(
            "SELECT t.tgname, t.tgenabled FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND NOT t.tgisinternal "
            "AND t.tgname IN ('trg_offrev_vacancy_immutable', "
            "'trg_incarnation_vacancy_immutable', 'trg_slr_incarnation_immutable')"
        ),
        {"s": settings.CORE_DB_SCHEMA},
    ).all()
    assert {f.tgname: f.tgenabled for f in filas} == {
        "trg_offrev_vacancy_immutable": "A",
        "trg_incarnation_vacancy_immutable": "A",
        "trg_slr_incarnation_immutable": "A",
    }
