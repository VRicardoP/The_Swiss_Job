"""core0038 — atestaciones bloqueantes y paradas declaradas auditables.

La racha de siete ciclos no basta por sí sola: el cierre exige un ensayo
rollback/replay para la release y la ratificación de los umbrales para el
oráculo medido. Las atestaciones son inmutables. Las paradas declaradas pasan
a exigir autor y huella de evidencia; las filas antiguas quedan visibles pero
no son computables por el gate.
"""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0038"
down_revision: Union[str, None] = "core0037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {S}.shadow_declared_downtime
          ADD COLUMN declared_by text,
          ADD COLUMN evidence_sha256 text;
        UPDATE {S}.shadow_declared_downtime
           SET declared_by = 'legacy-unverified',
               evidence_sha256 = repeat('0', 64)
         WHERE declared_by IS NULL;
        ALTER TABLE {S}.shadow_declared_downtime
          ALTER COLUMN declared_by SET NOT NULL,
          ALTER COLUMN evidence_sha256 SET NOT NULL,
          ADD CONSTRAINT ck_downtime_declared_by_no_vacio
            CHECK (btrim(declared_by) <> ''),
          ADD CONSTRAINT ck_downtime_evidence_sha256
            CHECK (evidence_sha256 ~ '^[0-9a-f]{{64}}$');

        CREATE FUNCTION {S}.trg_immutable_gate_evidence() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% es evidencia inmutable', TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER shadow_declared_downtime_immutable
        BEFORE UPDATE OR DELETE ON {S}.shadow_declared_downtime
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_immutable_gate_evidence();
        ALTER TABLE {S}.shadow_declared_downtime
          ENABLE ALWAYS TRIGGER shadow_declared_downtime_immutable;

        CREATE TABLE {S}.shadow_gate_attestations (
            kind text NOT NULL,
            release_sha text NOT NULL,
            oracle_fingerprint text NOT NULL DEFAULT '',
            evidence_sha256 text NOT NULL,
            attested_by text NOT NULL,
            attested_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            details jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            PRIMARY KEY (kind, release_sha, oracle_fingerprint),
            CONSTRAINT ck_gate_attestation_kind CHECK (
              kind IN ('rollback_replay', 'thresholds_ratified')
            ),
            CONSTRAINT ck_gate_attestation_release CHECK (
              btrim(release_sha) <> '' AND release_sha <> 'unknown'
            ),
            CONSTRAINT ck_gate_attestation_oracle CHECK (
              (kind = 'rollback_replay' AND oracle_fingerprint = '') OR
              (kind = 'thresholds_ratified' AND
               oracle_fingerprint ~ '^[0-9a-f]{{64}}$')
            ),
            CONSTRAINT ck_gate_attestation_evidence CHECK (
              evidence_sha256 ~ '^[0-9a-f]{{64}}$'
            ),
            CONSTRAINT ck_gate_attestation_operator CHECK (
              btrim(attested_by) <> ''
            ),
            CONSTRAINT ck_gate_attestation_details CHECK (
              jsonb_typeof(details) = 'object'
            )
        );
        CREATE TRIGGER shadow_gate_attestations_immutable
        BEFORE UPDATE OR DELETE ON {S}.shadow_gate_attestations
        FOR EACH ROW EXECUTE FUNCTION {S}.trg_immutable_gate_evidence();
        ALTER TABLE {S}.shadow_gate_attestations
          ENABLE ALWAYS TRIGGER shadow_gate_attestations_immutable;
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DROP TRIGGER IF EXISTS shadow_gate_attestations_immutable
          ON {S}.shadow_gate_attestations;
        DROP TABLE IF EXISTS {S}.shadow_gate_attestations;
        DROP TRIGGER IF EXISTS shadow_declared_downtime_immutable
          ON {S}.shadow_declared_downtime;
        ALTER TABLE {S}.shadow_declared_downtime
          DROP CONSTRAINT IF EXISTS ck_downtime_evidence_sha256,
          DROP CONSTRAINT IF EXISTS ck_downtime_declared_by_no_vacio,
          DROP COLUMN IF EXISTS evidence_sha256,
          DROP COLUMN IF EXISTS declared_by;
        DROP FUNCTION IF EXISTS {S}.trg_immutable_gate_evidence();
        """
    )
