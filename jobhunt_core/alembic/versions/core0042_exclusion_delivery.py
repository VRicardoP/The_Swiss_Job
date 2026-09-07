"""Invalidate matching on every exclusion change; fence versioned deliveries.

Published migrations remain unchanged. The existing generation fences both
recovery and publication, including import/rollback and direct SQL writers.
"""
from alembic import op
from jobhunt_core.config import settings

revision = "core0042"
down_revision = "core0041"
branch_labels = None
depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute(f"ALTER TABLE {S}.profiles ADD COLUMN exclusions_version bigint NOT NULL DEFAULT 0 CHECK (exclusions_version >= 0)")
    op.execute(f"""
        CREATE TRIGGER trg_corpus_generation_exclusions
        AFTER INSERT OR UPDATE OR DELETE ON {S}.profile_exclusions
        FOR EACH STATEMENT EXECUTE FUNCTION {S}.bump_corpus_generation()
    """)
    # Existing feeds may have been prepared before a rule changed under 0041.
    op.execute(f"UPDATE {S}.corpus_generation SET generation = generation + 1 WHERE id = 1")


def downgrade():
    op.execute(f"DROP TRIGGER trg_corpus_generation_exclusions ON {S}.profile_exclusions")
    op.execute(f"ALTER TABLE {S}.profiles DROP COLUMN exclusions_version")
