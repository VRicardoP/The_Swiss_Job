"""core0040 — documento léxico indexado para matching híbrido."""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0040"
down_revision: Union[str, None] = "core0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE {S}.offer_revisions
        ADD COLUMN search_document tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('simple',
                COALESCE(content->>'title', '')), 'A') ||
            setweight(to_tsvector('simple',
                COALESCE(content->>'tags', '')), 'A') ||
            setweight(to_tsvector('simple',
                COALESCE(content->>'description', '')), 'B') ||
            setweight(to_tsvector('simple',
                COALESCE(content->>'company', '')), 'C')
        ) STORED
        """
    )
    op.execute(
        f"CREATE INDEX ix_offrev_search_document "
        f"ON {S}.offer_revisions USING gin (search_document)"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {S}.ix_offrev_search_document")
    op.execute(
        f"ALTER TABLE {S}.offer_revisions "
        f"DROP COLUMN IF EXISTS search_document"
    )
