"""add search_vector tsvector column with GIN index and trigger

Revision ID: 11a4b5b5a28c
Revises: b6b766fb5c35
Create Date: 2026-02-25
"""

from alembic import op

revision = "11a4b5b5a28c"
down_revision = "b6b766fb5c35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add tsvector column
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS search_vector tsvector")

    # 2. GIN index for fast full-text search
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_search_vector "
        "ON jobs USING GIN (search_vector)"
    )

    # 3. Auto-update trigger on INSERT/UPDATE of title, description, company
    # Using 'pg_catalog.simple' (no stemming) because jobs are multilingual DE/FR/EN/IT
    op.execute(
        """
        CREATE TRIGGER tsvector_update_jobs
        BEFORE INSERT OR UPDATE OF title, description, company
        ON jobs
        FOR EACH ROW EXECUTE FUNCTION
        tsvector_update_trigger(
            search_vector, 'pg_catalog.simple', title, description, company
        )
        """
    )

    # 4. Backfill existing rows
    op.execute(
        """
        UPDATE jobs SET search_vector =
            to_tsvector('pg_catalog.simple',
                coalesce(title, '') || ' ' ||
                coalesce(description, '') || ' ' ||
                coalesce(company, ''))
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS tsvector_update_jobs ON jobs")
    op.execute("DROP INDEX IF EXISTS ix_jobs_search_vector")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS search_vector")
