"""seed swiss schools watchlist scraper compliance rows

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-05-29 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a8"
down_revision: Union[str, None] = "a1b2c3d4e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO source_compliance
            (id, source_key, method, is_allowed, rate_limit_seconds,
             robots_txt_ok, tos_notes, max_requests_per_hour,
             auto_disable_on_block, consecutive_blocks, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'swiss_schools_nae', 'scraping', true, 2.0, true,
             'Watchlist NAE central (Champittet, Beau Soleil, LCIS Aubonne). HTML estatico, jobs2web/Lumesse.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'swiss_schools_isp', 'scraping', true, 1.0, true,
             'Watchlist ISP Workday (Mosaic Geneva). API JSON publica de Workday.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'swiss_schools_inspired', 'scraping', true, 2.0, true,
             'Watchlist Inspired SuccessFactors (GES Versoix, St Georges Montreux). HTML estatico, plantilla Lumesse.',
             60, true, 0, now(), now())
        ON CONFLICT (source_key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM source_compliance
        WHERE source_key IN ('swiss_schools_nae', 'swiss_schools_isp', 'swiss_schools_inspired');
    """)
