"""seed education scraper compliance rows

Revision ID: a7b2c3d4e5f6
Revises: c4a1e9f0b321
Create Date: 2026-03-01 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b2c3d4e5f6"
down_revision: Union[str, None] = "c4a1e9f0b321"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO source_compliance
            (id, source_key, method, is_allowed, rate_limit_seconds,
             robots_txt_ok, tos_notes, max_requests_per_hour,
             auto_disable_on_block, consecutive_blocks, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'tes', 'scraping', true, 2.0, true,
             'TES.com education portal (Next.js SSR). ~32 CH jobs.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'schuljobs', 'scraping', true, 2.0, true,
             'SchulJobs.ch education portal (SSR + JSON-LD). ~25 jobs per load.',
             120, true, 0, now(), now())
        ON CONFLICT (source_key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM source_compliance
        WHERE source_key IN ('tes', 'schuljobs');
    """)
