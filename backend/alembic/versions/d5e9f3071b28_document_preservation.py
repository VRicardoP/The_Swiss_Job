"""Preserve generated documents and their context when a job disappears.

Revision ID: d5e9f3071b28
Revises: c4d8e2f60a17
"""

from alembic import op
import sqlalchemy as sa

revision = "d5e9f3071b28"
down_revision = "c4d8e2f60a17"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generated_documents", sa.Column("job_title", sa.String(500)))
    op.add_column("generated_documents", sa.Column("job_company", sa.String(300)))
    op.execute("""
        UPDATE generated_documents d SET job_title=j.title, job_company=j.company
        FROM jobs j WHERE j.hash=d.job_hash
    """)
    # Keep the opaque source reference; it is not an ownership/lifecycle FK.
    constraints = [fk for fk in sa.inspect(op.get_bind()).get_foreign_keys("generated_documents")
                   if fk["constrained_columns"] == ["job_hash"]]
    if len(constraints) != 1:
        raise RuntimeError("expected exactly one generated_documents job FK")
    op.drop_constraint(constraints[0]["name"], "generated_documents", type_="foreignkey")


def downgrade():
    # Serialize with writers before checking; never discard an orphan or snapshot.
    op.execute("LOCK TABLE generated_documents, jobs IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
        IF EXISTS (
            SELECT 1 FROM generated_documents d LEFT JOIN jobs j ON j.hash=d.job_hash
            WHERE j.hash IS NULL OR d.job_title IS DISTINCT FROM j.title
                                OR d.job_company IS DISTINCT FROM j.company
        ) THEN
            RAISE EXCEPTION 'document snapshots require reconciliation before downgrade';
        END IF;
    END $$""")
    op.create_foreign_key("generated_documents_job_hash_fkey", "generated_documents", "jobs",
                          ["job_hash"], ["hash"], ondelete="CASCADE")
    op.drop_column("generated_documents", "job_company")
    op.drop_column("generated_documents", "job_title")
