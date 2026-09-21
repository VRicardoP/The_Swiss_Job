"""Explicit saved-search execution authority and transactional observation ledger."""

from alembic import op
from jobhunt_core.config import settings

revision = "core0050"
down_revision = "core0049"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(f"""CREATE TABLE {S}.saved_search_execution (
        saved_search_id uuid PRIMARY KEY REFERENCES {S}.saved_searches(id) ON DELETE CASCADE,
        contract text NOT NULL CHECK (contract = 'swissjob-v1'),
        notify_since timestamptz NOT NULL,
        enabled boolean NOT NULL DEFAULT false,
        run_number bigint NOT NULL DEFAULT 0 CHECK (run_number >= 0),
        last_attempt_at timestamptz
    )""")
    # A consumed identity is a tombstone, not a pointer to live corpus state.
    # It survives corpus deletion/merge and never adds a harvest/profile lock
    # inversion. Owner deletion still removes it through the search aggregate.
    op.execute(f"""CREATE TABLE {S}.saved_search_observations (
        saved_search_id uuid NOT NULL REFERENCES {S}.saved_search_execution(saved_search_id) ON DELETE CASCADE,
        vacancy_id uuid NOT NULL,
        matched boolean NOT NULL,
        PRIMARY KEY (saved_search_id, vacancy_id)
    )""")


def downgrade():
    op.execute(f"LOCK TABLE {S}.saved_search_execution IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {S}.saved_search_execution) THEN
            RAISE EXCEPTION 'saved search authority requires explicit reconciliation before downgrade';
        END IF;
    END $$""")
    op.execute(f"DROP TABLE {S}.saved_search_observations")
    op.execute(f"DROP TABLE {S}.saved_search_execution")
