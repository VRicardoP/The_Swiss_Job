"""Preserve canonical core vacancy references in existing documents.

Revision ID: a91c06e3df72
Revises: f70b15293d40
"""

from alembic import op
import sqlalchemy as sa

revision = "a91c06e3df72"
down_revision = "f70b15293d40"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.alter_column("generated_documents", "job_hash", existing_type=sa.String(32), type_=sa.String(36))


def downgrade():
    op.execute("LOCK TABLE generated_documents IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM generated_documents WHERE length(job_hash) > 32) THEN
            RAISE EXCEPTION 'core document references require reconciliation before downgrade';
        END IF;
    END $$""")
    op.alter_column("generated_documents", "job_hash", existing_type=sa.String(36), type_=sa.String(32))
