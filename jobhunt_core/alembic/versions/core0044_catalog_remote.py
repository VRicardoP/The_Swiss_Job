"""Avoid deserializing historical descriptions to count remote catalog offers.

Measured on the restored NAS corpus: 1.3s/226k buffers -> 16ms/719 buffers;
index size 1.5 MiB. No data rewrite or changed filtering semantics.
"""
from alembic import op
from jobhunt_core.config import settings

revision = "core0044"
down_revision = "core0043"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    # Atomic DDL: a failed build cannot leave an invalid concurrent index.
    # The deployment must allow this short bounded write lock on revisions.
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"CREATE INDEX ix_offer_revisions_catalog_remote ON {S}.offer_revisions (id) WHERE (content->>'remote')::boolean=true")


def downgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"DROP INDEX {S}.ix_offer_revisions_catalog_remote")
