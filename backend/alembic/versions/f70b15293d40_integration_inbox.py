"""Metadata-only durable core event receipts.

Revision ID: f70b15293d40
Revises: e6fa04182c39
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f70b15293d40"
down_revision = "e6fa04182c39"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "integration_inbox",
        sa.Column("consumer_id", sa.String(100), primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("subject_profile_id", postgresql.UUID(as_uuid=True)),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_integration_inbox_subject_profile_id",
        "integration_inbox",
        ["subject_profile_id"],
    )


def downgrade():
    op.execute("LOCK TABLE integration_inbox IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM integration_inbox) THEN
            RAISE EXCEPTION 'inbox receipts require reconciliation before downgrade';
        END IF;
    END $$""")
    op.drop_table("integration_inbox")
