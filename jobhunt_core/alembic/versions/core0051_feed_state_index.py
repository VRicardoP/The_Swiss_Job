"""Partial index for counting a profile's feed.

Only 15,4 % of profile_vacancy_state rows carry a current_eval_id (production,
2026-09-22: 5.400 of 35.092), yet counting one feed scanned the whole table —
604 ms, `Rows Removed by Filter: 24712`. Same shape as the existing
ix_pvs_saved_feed_keyset, and small enough that the write path barely notices:
it only indexes rows that are actually in someone's feed.

Revision ID: core0051
Revises: core0050
"""
from alembic import op

revision = "core0051"
down_revision = "core0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_pvs_feed_current_eval "
        "ON jobhunt.profile_vacancy_state (profile_id, vacancy_id) "
        "WHERE current_eval_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS jobhunt.ix_pvs_feed_current_eval")
