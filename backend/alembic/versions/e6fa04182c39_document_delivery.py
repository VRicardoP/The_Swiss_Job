"""Prepared document delivery and durable operation tombstones.

Revision ID: e6fa04182c39
Revises: d5e9f3071b28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e6fa04182c39"
down_revision = "d5e9f3071b28"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_deliveries",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True)),
        sa.Column("document_id", postgresql.UUID(as_uuid=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("first_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(64)),
        sa.CheckConstraint("(delivered_at IS NULL AND document_id IS NULL AND payload IS NOT NULL) OR "
                           "(delivered_at IS NOT NULL AND document_id IS NOT NULL AND payload IS NULL)",
                           name="ck_document_delivery_receipt"),
    )
    op.create_index("ix_document_deliveries_user_id", "document_deliveries", ["user_id"])


def downgrade():
    op.execute("LOCK TABLE document_deliveries IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM document_deliveries) THEN
            RAISE EXCEPTION 'document operations require reconciliation before downgrade';
        END IF;
    END $$""")
    op.drop_table("document_deliveries")
