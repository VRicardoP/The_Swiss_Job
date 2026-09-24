"""Index the existing literal title/company substring catalog search.

Uses the already-installed pg_trgm extension. No new search semantics or data.
Restored corpus benchmark: Python query 573ms/439k buffers -> 45ms/1433 buffers;
indexes 15 MiB and 7.6 MiB. NAS latency is verified after deployment separately.
"""

from alembic import op
from jobhunt_core.config import settings

revision = "core0045"
down_revision = "core0044"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        f"CREATE INDEX ix_offer_revisions_catalog_title ON {S}.offer_revisions USING gin (lower(coalesce(content->>'title','')) gin_trgm_ops)"
    )
    op.execute(
        f"CREATE INDEX ix_offer_revisions_catalog_company ON {S}.offer_revisions USING gin (lower(coalesce(content->>'company','')) gin_trgm_ops)"
    )


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP INDEX {S}.ix_offer_revisions_catalog_company")
    op.execute(f"DROP INDEX {S}.ix_offer_revisions_catalog_title")
