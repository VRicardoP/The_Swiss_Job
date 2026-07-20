"""seed irishjobs compliance row

Revision ID: c9f2a7b41e30
Revises: e6f8a0b2c4d3
Create Date: 2026-07-20 12:00:00.000000

IrishJobs.ie + Jobs.ie comparten la plataforma StepStone y se cosechan como un
único scraper (SOURCE_NAME=irishjobs). BaseScraper._pre_check consulta
source_compliance y can_scrape() devuelve False si no existe fila → sin este
seed el scraper quedaría bloqueado en silencio (nunca cosecharía).
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f2a7b41e30"
down_revision: Union[str, None] = "e6f8a0b2c4d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO source_compliance
            (id, source_key, method, is_allowed, rate_limit_seconds,
             robots_txt_ok, tos_notes, max_requests_per_hour,
             auto_disable_on_block, consecutive_blocks, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'irishjobs', 'scraping', true, 2.0, true,
             'StepStone Ireland (IrishJobs.ie + Jobs.ie). Public SSR listing '
             '(__PRELOADED_STATE__). No per-job request. Verify TOS.',
             120, true, 0, now(), now())
        ON CONFLICT (source_key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM source_compliance WHERE source_key = 'irishjobs';
    """)
