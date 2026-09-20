"""Fence durable profile snapshots against obsolete BFF/CDC writers."""

from alembic import op
from jobhunt_core.config import settings

revision = "core0049"
down_revision = "core0048"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"""ALTER TABLE {S}.profiles
        ADD COLUMN projection_version bigint NOT NULL DEFAULT 0,
        ADD COLUMN projection_hash text,
        ADD COLUMN projection_active boolean NOT NULL DEFAULT true,
        ADD CONSTRAINT ck_profile_projection CHECK (
          (projection_version=0 AND projection_hash IS NULL AND projection_active)
          OR (projection_version>0 AND projection_hash IS NOT NULL
              AND projection_hash ~ '^[0-9a-f]{{64}}$'))""")


def downgrade():
    op.execute(f"LOCK TABLE {S}.profiles IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {S}.profiles WHERE projection_version>0) THEN
            RAISE EXCEPTION 'profile projection authority requires explicit reconciliation';
        END IF;
    END $$""")
    op.execute(
        f"ALTER TABLE {S}.profiles DROP CONSTRAINT ck_profile_projection, "
        "DROP COLUMN projection_active, DROP COLUMN projection_hash, "
        "DROP COLUMN projection_version"
    )
