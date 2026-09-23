"""Account-independent erasure requests survive deletion and worker restarts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "b46e1230a901"
down_revision = "a91c06e3df72"
branch_labels = depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.create_table(
        "profile_erasures",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("core_profile_id", UUID(as_uuid=True)),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("core_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
    )


def downgrade():
    op.execute("LOCK TABLE profile_erasures IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM profile_erasures) THEN
            RAISE EXCEPTION 'erasure requests must be preserved';
        END IF;
    END $$""")
    op.drop_table("profile_erasures")
