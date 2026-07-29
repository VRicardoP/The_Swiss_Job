"""C-ESQ — seguimiento de candidaturas y búsquedas guardadas (Fase C).

Adelanto MÍNIMO de esquema no-[A] (registrado como tal en CONTRATOS_FASE_C
v1.1, ticket C-ESQ): crea las 4 tablas que C-1/C-4/C-API-W exigen, ciñéndose
LITERALMENTE a CONTRATOS_FASE_A §1 donde §1 define columnas, y al PLAN §4
para idempotency_records. NO crea generated_documents ni tablas de colegios
(Fase E).

- `applications`: UNIQUE(profile_id, vacancy_id); puntero NULLABLE a la
  incarnación con FK COMPUESTA (source_listing_incarnation_id, vacancy_id) →
  source_listing_incarnations(id, vacancy_id) — integridad de propietario
  (la incarnación citada es de ESA vacante, §1 #2) — con SET NULL POR-COLUMNA
  (PG15+, mismo fix que la auditoría A-02: un SET NULL plano nulificaría
  también vacancy_id NOT NULL); snapshot JSONB; status con start=applied.
- ENUM `application_status` = unión de los dos legados que migran a él
  (SwissJob Fase D ⊇ portfolio Fase C): saved/applied/phone_screen/technical/
  interview/offer/rejected/withdrawn. `saved` existe por la regla C-4:
  bookmark CON follow_up_date migra ADEMÁS como application.
- `application_status_events`: §1 no define columnas (solo la nombra) →
  mínimo: id, application_id, status, created_at. Sin data/notes (el contrato
  no los define). ON DELETE CASCADE: los eventos son hijos del dato de usuario
  (mismo patrón que erase_acks/deliveries; el erase GDPR de applications
  arrastra su historial).
- `saved_searches`: §1 solo la nombra → columnas del modelo legado de
  SwissJob (fuente que migra en Fase D y superconjunto del portfolio),
  con profile_id→profiles en vez de user_id. ENUM `notify_frequency`
  (realtime/daily/weekly, default daily).
- `idempotency_records` (PLAN §4; la activa C-API-W): PK NATURAL
  (consumer_id, key, route) — la fila ES el candado anti-repetición, sin
  surrogate id (el PLAN no lo lista). response NULLABLE: la reserva se
  inserta EN VUELO antes de conocer la respuesta. Índice por expires_at
  para la purga futura.

Revision ID: core0011
Revises: core0010
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID

from jobhunt_core.config import settings

revision: str = "core0011"
down_revision: Union[str, None] = "core0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA  # "jobhunt"
UUID_PK = dict(
    primary_key=True, server_default=sa.text("gen_random_uuid()"), nullable=False
)
NOW = sa.text("now()")

# Enums del contrato (nativos de Postgres, en el esquema del core).
# create_type=False: los crea/borra ESTA migración explícitamente (disciplina
# core0002 — sin el flag, create_table intentaría crearlos otra vez).
application_status = PGEnum(
    "saved", "applied", "phone_screen", "technical", "interview",
    "offer", "rejected", "withdrawn",
    name="application_status", schema=S, create_type=False,
)
notify_frequency = PGEnum(
    "realtime", "daily", "weekly",
    name="notify_frequency", schema=S, create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    application_status.create(bind, checkfirst=True)
    notify_frequency.create(bind, checkfirst=True)

    # ---------- Candidaturas (§1) ----------
    op.create_table(
        "applications",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("vacancy_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.vacancies.id"), nullable=False),
        sa.Column("source_listing_incarnation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("snapshot", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", application_status, nullable=False, server_default="applied"),
        sa.Column("notes", sa.Text),
        sa.Column("follow_up_date", sa.Date),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        sa.UniqueConstraint("profile_id", "vacancy_id", name="uq_application_profile_vacancy"),
        schema=S,
    )
    # FK compuesta con SET NULL POR-COLUMNA (PG15+): borrar la incarnación
    # apuntada nulifica SOLO el puntero, jamás el dato de usuario (PF.3).
    # op.create_foreign_key no expone la sublista → ALTER en crudo (core0002).
    op.execute(
        f"ALTER TABLE {S}.applications "
        f"ADD CONSTRAINT fk_application_incarnation_same_vacancy "
        f"FOREIGN KEY (source_listing_incarnation_id, vacancy_id) "
        f"REFERENCES {S}.source_listing_incarnations (id, vacancy_id) "
        f"ON DELETE SET NULL (source_listing_incarnation_id)"
    )
    op.create_table(
        "application_status_events",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column(
            "application_id",
            UUID(as_uuid=True),
            sa.ForeignKey(f"{S}.applications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", application_status, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )

    # ---------- Búsquedas guardadas ----------
    op.create_table(
        "saved_searches",
        sa.Column("id", UUID(as_uuid=True), **UUID_PK),
        sa.Column("profile_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.profiles.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("filters", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("min_score", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("notify_frequency", notify_frequency, nullable=False, server_default="daily"),
        sa.Column("notify_push", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("total_matches", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=NOW),
        schema=S,
    )
    # Camino de lectura del runner de alertas: búsquedas de UN perfil.
    op.create_index("ix_saved_searches_profile", "saved_searches", ["profile_id"], schema=S)

    # ---------- Idempotencia HTTP (PLAN §4, C-API-W) ----------
    op.create_table(
        "idempotency_records",
        sa.Column("consumer_id", UUID(as_uuid=True), sa.ForeignKey(f"{S}.consumers.id"), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("route", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response", JSONB, nullable=True),  # NULL = petición en vuelo
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("consumer_id", "key", "route", name="pk_idempotency_records"),
        schema=S,
    )
    # Purga futura por expiración (barrido secuencial por fecha).
    op.create_index("ix_idem_expires_at", "idempotency_records", ["expires_at"], schema=S)


def downgrade() -> None:
    op.drop_table("idempotency_records", schema=S)
    op.drop_table("saved_searches", schema=S)
    op.drop_table("application_status_events", schema=S)
    op.drop_table("applications", schema=S)
    bind = op.get_bind()
    notify_frequency.drop(bind, checkfirst=True)
    application_status.drop(bind, checkfirst=True)
