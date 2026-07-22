"""A-02 — Migración [A] del manifiesto (CONTRATOS_FASE_A.md §1, RATIFICADO).

Crea las 30 tablas [A] de la vertical mínima, escritas A MANO para respetar el
contrato al pie de la letra:
- FKs COMPUESTAS de integridad de propietario (misma vacante / mismo perfil).
- FK circular vacancies ↔ offer_revisions/incarnations: punteros NULLABLE y la
  FK se añade con ALTER tras crear ambas tablas (sin ciclo irresoluble).
- UNIQUE parcial (una incarnación activa por slot) y UNIQUE por expresión
  (par canónico LEAST/GREATEST en dedup_candidates).
- pgvector vector(384) + índice HNSW (coseno) para el modelo activo; otra
  dimensión = expand/contract (ADR-02).
- Trigger de coherencia en offer_revision_sources (la revisión raw debe
  pertenecer a la MISMA vacante).
- ON DELETE: nunca CASCADE sobre dato de usuario (PF.3/contrato); punteros
  internos SET NULL; current_eval_id RESTRICT (impone el ADR-03: la evaluación
  vigente no se poda).

Revision ID: core0002
Revises: core0001
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision: str = "core0002"
down_revision: Union[str, None] = "core0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"
UUID_PK = dict(
    primary_key=True, server_default=sa.text("gen_random_uuid()"), nullable=False
)
NOW = sa.text("now()")

# Enums del contrato (nativos de Postgres, en el esquema del core).
# create_type=False: los crea/borra ESTA migración explícitamente; sin el flag,
# create_table intentaría crearlos otra vez (DuplicateObject).
dedup_state = PGEnum(
    "pending", "confirmed", "rejected",
    name="dedup_candidate_state", schema=S, create_type=False,
)
delivery_state = PGEnum(
    "pending", "inflight", "delivered", "dead",
    name="delivery_state", schema=S, create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    dedup_state.create(bind, checkfirst=True)
    delivery_state.create(bind, checkfirst=True)

    # ---------- Identidad / auth ----------
    op.create_table(
        "consumers",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        schema=S,
    )
    op.create_table(
        "consumer_credentials",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("consumer_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.consumers.id"), nullable=False),
        sa.Column("key_id", sa.String(64), nullable=False, unique=True),
        sa.Column("hash", sa.String(128), nullable=False),
        sa.Column("scopes", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
        schema=S,
    )
    op.create_table(
        "profiles",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("consumer_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.consumers.id"), nullable=False),
        sa.Column("external_ref", sa.String(100), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("consumer_id", "external_ref", name="uq_profiles_consumer_ref"),
        schema=S,
    )
    op.create_table(
        "profile_revisions",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("profile_id", "content_hash", name="uq_profrev_profile_hash"),
        # Soporte de la FK compuesta (mismo perfil) desde embeddings/evaluations.
        sa.UniqueConstraint("id", "profile_id", name="uq_profrev_id_profile"),
        schema=S,
    )

    # ---------- Cosecha ----------
    op.create_table(
        "sources",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("is_restricted", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("authorized_route", sa.String(200)),
        sa.Column("rate_limit", sa.Integer),
        sa.Column("robots_ok", sa.Boolean),
        schema=S,
    )
    op.create_table(
        "harvest_scopes",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.sources.id"), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("tier", sa.SmallInteger, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        schema=S,
    )
    op.create_table(
        "source_scope_state",
        sa.Column("scope_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.harvest_scopes.id"), primary_key=True),
        sa.Column("cursor", JSONB),
        sa.Column("last_complete_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default=sa.text("0")),
        schema=S,
    )

    # ---------- Corpus ----------
    op.create_table(
        "embedding_models",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("version", sa.String(40), nullable=False),
        sa.Column("dim", sa.Integer, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("name", "version", name="uq_embmodel_name_version"),
        schema=S,
    )
    # vacancies: los punteros circulares nacen NULLABLE y SIN FK; las FKs se
    # añaden por ALTER al final (sin ciclo irresoluble, DoD A-02).
    op.create_table(
        "vacancies",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("current_offer_revision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("primary_incarnation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("merged_into", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_index("ix_vacancies_archived_at", "vacancies", ["archived_at"], schema=S)
    op.create_table(
        "source_listings",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("source_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.sources.id"), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("url_normalized", sa.String(1000), nullable=False),
        sa.UniqueConstraint("source_id", "external_id", name="uq_listing_source_external"),
        sa.UniqueConstraint("source_id", "url_normalized", name="uq_listing_source_url"),
        schema=S,
    )
    op.create_table(
        "source_listing_incarnations",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("source_listing_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.source_listings.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("apply_url", sa.String(1000)),
        sa.Column("first_seen_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("source_listing_id", "seq", name="uq_incarnation_slot_seq"),
        # Soporte de las FKs compuestas (misma vacante) desde vacancies/applications.
        sa.UniqueConstraint("id", "vacancy_id", name="uq_incarnation_id_vacancy"),
        schema=S,
    )
    # Una sola incarnación ACTIVA por slot (UNIQUE parcial, ADR-01).
    op.create_index(
        "uq_incarnation_active", "source_listing_incarnations", ["source_listing_id"],
        unique=True, postgresql_where=sa.text("ended_at IS NULL"), schema=S,
    )
    op.create_table(
        "source_listing_revisions",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("incarnation_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.source_listing_incarnations.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw", JSONB, nullable=False),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("incarnation_id", "content_hash", name="uq_slrev_incarnation_hash"),
        schema=S,
    )
    op.create_table(
        "offer_revisions",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("vacancy_id", "content_hash", name="uq_offrev_vacancy_hash"),
        sa.UniqueConstraint("id", "vacancy_id", name="uq_offrev_id_vacancy"),
        schema=S,
    )
    op.create_index("ix_offrev_text_hash", "offer_revisions", ["text_hash"], schema=S)
    op.create_table(
        "offer_revision_sources",
        sa.Column("offer_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_listing_revision_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.source_listing_revisions.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), nullable=False),
        sa.PrimaryKeyConstraint("offer_revision_id", "source_listing_revision_id", name="pk_offer_revision_sources"),
        sa.ForeignKeyConstraint(
            ["offer_revision_id", "vacancy_id"],
            [f"{S}.offer_revisions.id", f"{S}.offer_revisions.vacancy_id"],
            name="fk_ors_offrev_same_vacancy",
        ),
        schema=S,
    )
    # Embeddings clavados en text_hash (ADR-02): dos revisiones con el mismo
    # texto comparten vector; cambiar salario/logo NO re-embebe.
    # PARTICIONADA POR model_id (contrato "HNSW por model", rev. externa A-02 #2):
    # cada modelo = su partición = su PROPIO índice HNSW (un solo espacio
    # vectorial por índice; sin mezclar modelos ni degradar recall). El índice
    # del padre es particionado: cada partición hereda el suyo automáticamente.
    # REGLA OPERATIVA: al registrar un modelo (A-06+) se crea su partición:
    #   CREATE TABLE jobhunt.offer_embeddings_<hex> PARTITION OF
    #   jobhunt.offer_embeddings FOR VALUES IN ('<model_id>');
    op.execute(
        f"""
        CREATE TABLE {S}.offer_embeddings (
            text_hash varchar(64) NOT NULL,
            model_id uuid NOT NULL REFERENCES {S}.embedding_models (id),
            vector vector(384) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_offer_embeddings PRIMARY KEY (text_hash, model_id)
        ) PARTITION BY LIST (model_id)
        """
    )
    op.execute(
        f"CREATE INDEX ix_offemb_vector_hnsw ON {S}.offer_embeddings "
        f"USING hnsw (vector vector_cosine_ops)"
    )
    op.create_table(
        "profile_embeddings",
        sa.Column("profile_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.embedding_models.id"), nullable=False),
        sa.Column("vector", Vector(384), nullable=False),
        sa.PrimaryKeyConstraint("profile_revision_id", "model_id", name="pk_profile_embeddings"),
        # FK compuesta: la revisión embebida pertenece a ESE perfil (contrato §1).
        sa.ForeignKeyConstraint(
            ["profile_revision_id", "profile_id"],
            [f"{S}.profile_revisions.id", f"{S}.profile_revisions.profile_id"],
            name="fk_pemb_rev_same_profile",
        ),
        schema=S,
    )
    op.create_table(
        "link_evidence",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("source_listing_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.source_listings.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "merge_log",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("winner_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("loser_id", UUID(as_uuid=True), nullable=False),
        sa.Column("evidence", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("confidence", sa.Numeric(4, 3)),
        sa.Column("actor", sa.String(60), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "merge_transfers",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("merge_log_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.merge_log.id"), nullable=False),
        sa.Column("entity", sa.String(40), nullable=False),
        sa.Column("row_key", sa.String(200), nullable=False),
        sa.Column("before", JSONB, nullable=False),
        sa.Column("after", JSONB, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_table(
        "dedup_candidates",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("vacancy_a", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("vacancy_b", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("similarity", sa.Numeric(4, 3)),
        sa.Column("state", dedup_state, nullable=False, server_default="pending"),
        sa.Column("resolved_by", sa.String(60)),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("merge_log_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.merge_log.id"), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    # Par canónico: (a,b) y (b,a) son el MISMO candidato (UNIQUE por expresión).
    op.create_index(
        "uq_dedup_pair", "dedup_candidates",
        [sa.text("LEAST(vacancy_a, vacancy_b)"), sa.text("GREATEST(vacancy_a, vacancy_b)")],
        unique=True, schema=S,
    )

    # ---------- Matching / estado ----------
    op.create_table(
        "scoring_policies",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("weights", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("name", "prompt_version", name="uq_policy_name_version"),
        schema=S,
    )
    op.create_table(
        "match_evaluations",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("offer_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("profile_revision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.embedding_models.id"), nullable=False),
        sa.Column("scoring_policy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.scoring_policies.id"), nullable=False),
        sa.Column("eval_key", sa.String(64), nullable=False),
        sa.Column("score_final", sa.Numeric(6, 2), nullable=False),
        sa.Column("scores", JSONB, nullable=False),
        sa.Column("explanation", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("profile_id", "vacancy_id", "eval_key", name="uq_eval_profile_vacancy_key"),
        # Soporte de la FK compuesta del feed (current_eval del MISMO par).
        sa.UniqueConstraint("id", "profile_id", "vacancy_id", name="uq_eval_id_profile_vacancy"),
        # FKs compuestas de integridad de propietario (contrato §1, rev. #3):
        sa.ForeignKeyConstraint(
            ["offer_revision_id", "vacancy_id"],
            [f"{S}.offer_revisions.id", f"{S}.offer_revisions.vacancy_id"],
            name="fk_eval_offrev_same_vacancy",
        ),
        sa.ForeignKeyConstraint(
            ["profile_revision_id", "profile_id"],
            [f"{S}.profile_revisions.id", f"{S}.profile_revisions.profile_id"],
            name="fk_eval_profrev_same_profile",
        ),
        schema=S,
    )
    # Keyset pagination del feed (contrato §2).
    op.create_index(
        "ix_eval_feed_keyset", "match_evaluations",
        ["profile_id", sa.text("score_final DESC"), "vacancy_id"], schema=S,
    )
    op.create_table(
        "profile_vacancy_state",
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("feedback", sa.String(20)),
        sa.Column("dismissed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("saved_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("notes", sa.Text),
        sa.Column("current_eval_id", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("profile_id", "vacancy_id", name="pk_profile_vacancy_state"),
        # FK compuesta: la eval vigente pertenece al MISMO (perfil, vacante).
        # RESTRICT explícito (no SET NULL: nulificaría también las columnas PK;
        # no NO ACTION: RESTRICT no es diferible) — impone el ADR-03: la
        # evaluación vigente no puede podarse.
        sa.ForeignKeyConstraint(
            ["current_eval_id", "profile_id", "vacancy_id"],
            [
                f"{S}.match_evaluations.id",
                f"{S}.match_evaluations.profile_id",
                f"{S}.match_evaluations.vacancy_id",
            ],
            name="fk_pvs_current_eval_same_pair",
            ondelete="RESTRICT",
        ),
        schema=S,
    )
    op.create_table(
        "profile_vacancy_events",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )

    # ---------- Orquestación / entrega ----------
    op.create_table(
        "harvest_runs",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        schema=S,
    )
    op.create_table(
        "source_harvest_runs",
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.harvest_runs.id"), nullable=False),
        sa.Column("scope_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.harvest_scopes.id"), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.PrimaryKeyConstraint("run_id", "scope_id", name="pk_source_harvest_runs"),
        schema=S,
    )
    op.create_table(
        "integration_outbox",
        sa.Column("event_id", UUID(as_uuid=True), primary_key=True),  # determinista (uuid5, ADR-05)
        sa.Column("aggregate", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.String(100), nullable=False),
        sa.Column("subject_profile_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.BigInteger, nullable=False),
        sa.Column("type", sa.String(60), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    op.create_index("ix_outbox_subject", "integration_outbox", ["subject_profile_id"], schema=S)
    op.create_table(
        "integration_outbox_deliveries",
        sa.Column("event_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.integration_outbox.event_id", ondelete="CASCADE"), nullable=False),
        sa.Column("destination", sa.String(60), nullable=False),
        sa.Column("state", delivery_state, nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("lease", sa.TIMESTAMP(timezone=True)),
        sa.Column("ack_at", sa.TIMESTAMP(timezone=True)),
        sa.PrimaryKeyConstraint("event_id", "destination", name="pk_outbox_deliveries"),
        schema=S,
    )
    op.create_table(
        "erase_requests",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("subject_profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column("required_consumers", JSONB, nullable=False),  # congelado al crear (ADR-07)
        sa.Column("requested_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        schema=S,
    )
    op.create_table(
        "erase_acks",
        sa.Column("erase_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.erase_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("consumer_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.consumers.id"), nullable=False),
        sa.Column("acked_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("erase_id", "consumer_id", name="pk_erase_acks"),
        schema=S,
    )

    # ---------- FKs circulares (por ALTER, sin ciclo irresoluble) ----------
    # SET NULL POR-COLUMNA (PG15+): un SET NULL plano nulificaría TODAS las
    # columnas locales de la FK compuesta, incluida la PK id (NOT NULL) →
    # 23502. La lista de columnas nulifica SOLO el puntero (auditoría A-02).
    # op.create_foreign_key no expone la sublista → ALTER en crudo.
    op.execute(
        f"ALTER TABLE {S}.vacancies ADD CONSTRAINT fk_vacancy_current_offrev "
        f"FOREIGN KEY (current_offer_revision_id, id) "
        f"REFERENCES {S}.offer_revisions (id, vacancy_id) "
        f"ON DELETE SET NULL (current_offer_revision_id)"
    )
    op.execute(
        f"ALTER TABLE {S}.vacancies ADD CONSTRAINT fk_vacancy_primary_incarnation "
        f"FOREIGN KEY (primary_incarnation_id, id) "
        f"REFERENCES {S}.source_listing_incarnations (id, vacancy_id) "
        f"ON DELETE SET NULL (primary_incarnation_id)"
    )

    # ---------- Trigger de coherencia (offer_revision_sources) ----------
    op.execute(
        f"""
        CREATE FUNCTION {S}.check_ors_same_vacancy() RETURNS trigger AS $$
        BEGIN
          IF (SELECT i.vacancy_id
              FROM {S}.source_listing_revisions r
              JOIN {S}.source_listing_incarnations i ON i.id = r.incarnation_id
              WHERE r.id = NEW.source_listing_revision_id)
             IS DISTINCT FROM NEW.vacancy_id THEN
            RAISE EXCEPTION
              'offer_revision_sources: la revisión raw pertenece a otra vacante';
          END IF;
          RETURN NEW;
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_ors_same_vacancy BEFORE INSERT OR UPDATE "
        f"ON {S}.offer_revision_sources FOR EACH ROW "
        f"EXECUTE FUNCTION {S}.check_ors_same_vacancy()"
    )

    # ---------- Inmutabilidad de bindings (rev. externa A-02 #1) ----------
    # El trigger ORS valida al INSERTAR; sin esto, mutar después el binding
    # (slr.incarnation_id / incarnation.vacancy_id / offer_revision.vacancy_id)
    # rompería la coherencia en silencio. ADR-04 (v4) lo fija: revisiones e
    # incarnaciones CONSERVAN su vacancy_id — merge = resolver merged_into en
    # lectura; reciclado = cerrar y abrir OTRA incarnación. Estas columnas son
    # INMUTABLES a nivel de BD.
    op.execute(
        f"""
        CREATE FUNCTION {S}.forbid_immutable_update() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION
            '%.%: columna inmutable (ADR-04: merge=merged_into; reciclado=nueva incarnación)',
            TG_TABLE_NAME, TG_ARGV[0];
        END $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"CREATE TRIGGER trg_slr_incarnation_immutable BEFORE UPDATE "
        f"ON {S}.source_listing_revisions FOR EACH ROW "
        f"WHEN (OLD.incarnation_id IS DISTINCT FROM NEW.incarnation_id) "
        f"EXECUTE FUNCTION {S}.forbid_immutable_update('incarnation_id')"
    )
    op.execute(
        f"CREATE TRIGGER trg_incarnation_vacancy_immutable BEFORE UPDATE "
        f"ON {S}.source_listing_incarnations FOR EACH ROW "
        f"WHEN (OLD.vacancy_id IS DISTINCT FROM NEW.vacancy_id) "
        f"EXECUTE FUNCTION {S}.forbid_immutable_update('vacancy_id')"
    )
    op.execute(
        f"CREATE TRIGGER trg_offrev_vacancy_immutable BEFORE UPDATE "
        f"ON {S}.offer_revisions FOR EACH ROW "
        f"WHEN (OLD.vacancy_id IS DISTINCT FROM NEW.vacancy_id) "
        f"EXECUTE FUNCTION {S}.forbid_immutable_update('vacancy_id')"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS trg_offrev_vacancy_immutable ON {S}.offer_revisions")
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_incarnation_vacancy_immutable "
        f"ON {S}.source_listing_incarnations"
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_slr_incarnation_immutable "
        f"ON {S}.source_listing_revisions"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {S}.forbid_immutable_update()")
    op.execute(f"DROP TRIGGER IF EXISTS trg_ors_same_vacancy ON {S}.offer_revision_sources")
    op.execute(f"DROP FUNCTION IF EXISTS {S}.check_ors_same_vacancy()")
    op.drop_constraint("fk_vacancy_primary_incarnation", "vacancies", schema=S, type_="foreignkey")
    op.drop_constraint("fk_vacancy_current_offrev", "vacancies", schema=S, type_="foreignkey")
    for table in (
        "erase_acks", "erase_requests", "integration_outbox_deliveries",
        "integration_outbox", "source_harvest_runs", "harvest_runs",
        "profile_vacancy_events", "profile_vacancy_state", "match_evaluations",
        "scoring_policies", "dedup_candidates", "merge_transfers", "merge_log",
        "link_evidence", "profile_embeddings", "offer_embeddings",
        "offer_revision_sources", "offer_revisions", "source_listing_revisions",
        "source_listing_incarnations", "source_listings", "vacancies",
        "embedding_models", "source_scope_state", "harvest_scopes", "sources",
        "profile_revisions", "profiles", "consumer_credentials", "consumers",
    ):
        op.drop_table(table, schema=S)
    bind = op.get_bind()
    delivery_state.drop(bind, checkfirst=True)
    dedup_state.drop(bind, checkfirst=True)
