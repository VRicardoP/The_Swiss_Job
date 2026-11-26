"""Retira `shadow_change_log` y su redacción GDPR: el staging de la CDC sombra.

D3 del plan de eficiencia del 2026-11-26, aprobado por el propietario.

`shadow_change_log` nació en `core0008b` como el buffer del capturador CDC de
la Fase B. La CDC se retiró el 2026-10-13 (`3ed6462`): ni capturador, ni
proyector, ni slot. Desde entonces la tabla no tiene escritor. Medido en
producción el 2026-11-25: 216 MB y 0 filas vivas.

La sostenía una sola cosa, el camino de borrado GDPR: `erasure.scrub_capture`
la bloqueaba y vaciaba los payloads del sujeto, y el trigger
`redact_erased_shadow_payload` (`core0046`) redactaba las capturas que llegaran
tarde. Sin capturador no llega ninguna, y con 0 filas no hay nada que vaciar.
Esta revisión retira las dos piezas a la vez que el código deja de llamarlas.
Si el código viejo corre contra la base ya migrada, el `LOCK TABLE` falla y el
borrado se reintenta (la petición del BFF sigue en cola): por eso se despliega
con `deploy_nas.sh core`, que migra y recrea en el mismo paso.

`profile_erasure_receipts.source_profile_pks` se conserva: es parte del recibo
y del inventario de restauración, y no ocupa nada.

El `downgrade()` recrea la estructura literal de `core0008b` y `core0046`, no
las filas: con 0 en producción no había ninguna.

REGLA (disciplina Alembic, env.py): revisión APLICADA = INMUTABLE.

Revision ID: core0063
Revises: core0059
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from jobhunt_core.config import settings

revision = "core0063"
down_revision = "core0060"
branch_labels = None
depends_on = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='5s'")
    # El trigger cae con la tabla; la función no, por eso va explícita.
    op.drop_table("shadow_change_log", schema=S)
    op.execute(f"DROP FUNCTION {S}.redact_erased_shadow_payload()")


def downgrade() -> None:
    # Definición LITERAL de core0008b (tabla e índice parcial) y core0046
    # (función y trigger de redacción).
    op.create_table(
        "shadow_change_log",
        sa.Column("lsn", sa.BigInteger, nullable=False),
        sa.Column("seq_in_tx", sa.Integer, nullable=False),
        sa.Column("src_table", sa.Text, nullable=False),
        sa.Column("op", sa.String(1), nullable=False),
        sa.Column("pk", sa.Text, nullable=False),
        sa.Column(
            "payload", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column(
            "received_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True)),
        sa.PrimaryKeyConstraint("lsn", "seq_in_tx", name="pk_shadow_change_log"),
        sa.CheckConstraint("op IN ('I', 'U', 'D')", name="ck_change_log_op"),
        schema=S,
    )
    op.create_index(
        "ix_shadow_change_unapplied",
        "shadow_change_log",
        ["lsn", "seq_in_tx"],
        schema=S,
        postgresql_where=sa.text("applied_at IS NULL"),
    )
    op.execute(f"""CREATE FUNCTION {S}.redact_erased_shadow_payload() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.src_table <> 'user_profiles' THEN RETURN NEW; END IF;
        -- Staging's table lock is already held: never wait staging -> fence
        -- against an eraser's fence -> staging. Capture retries the transaction.
        PERFORM 1 FROM {S}.profile_erasure_fence FOR SHARE NOWAIT;
        IF EXISTS (
            SELECT 1 FROM {S}.profile_erasure_receipts r
            JOIN {S}.consumers c ON c.id=r.consumer_id
            WHERE c.name='swissjob-shadow' AND (
                r.external_ref=NEW.payload->>'user_id' OR
                NEW.pk=ANY(r.source_profile_pks))
        ) THEN NEW.payload='{{}}'::jsonb; END IF;
        RETURN NEW;
        END $$""")
    op.execute(f"""CREATE TRIGGER redact_erased_shadow_payload
        BEFORE INSERT OR UPDATE OF payload ON {S}.shadow_change_log
        FOR EACH ROW EXECUTE FUNCTION {S}.redact_erased_shadow_payload()""")
