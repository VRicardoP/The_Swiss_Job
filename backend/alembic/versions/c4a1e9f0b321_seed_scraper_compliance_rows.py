"""seed scraper compliance rows

Revision ID: c4a1e9f0b321
Revises: 18521f132e85
Create Date: 2026-02-28 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a1e9f0b321"
down_revision: Union[str, None] = "18521f132e85"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO source_compliance
            (id, source_key, method, is_allowed, rate_limit_seconds,
             robots_txt_ok, tos_notes, max_requests_per_hour,
             auto_disable_on_block, consecutive_blocks, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'stelle_admin', 'scraping', true, 3.0, true,
             'Public government portal (SECO). JS SPA requires Playwright.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'medjobs', 'scraping', true, 3.0, true,
             'Healthcare portal. Verify robots.txt. 403 observed with bot UA.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'gastrojob', 'scraping', true, 2.0, true,
             'Hospitality portal (TYPO3). TOS to be verified.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'financejobs', 'scraping', true, 2.0, true,
             'Finance portal (Next.js SSR with embedded JSON).',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'myscience', 'scraping', true, 2.0, true,
             'Academic/science portal (SSR).',
             120, true, 0, now(), now())
        ON CONFLICT (source_key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM source_compliance
        WHERE source_key IN (
            'stelle_admin', 'medjobs', 'gastrojob', 'financejobs', 'myscience'
        );
    """)
