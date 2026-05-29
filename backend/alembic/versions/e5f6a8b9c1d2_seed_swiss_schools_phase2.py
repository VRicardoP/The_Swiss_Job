"""seed swiss schools watchlist phase 2 scrapers in source_compliance

Revision ID: e5f6a8b9c1d2
Revises: d4e5f6a8b9c1
Create Date: 2026-05-29 15:00:00.000000

Añade 5 nuevos source_keys para los scrapers de Fase 2 y 3 de la watchlist:
- swiss_schools_zis (ZIS, Group A)
- swiss_schools_isb (ISB, Group A)
- swiss_schools_ecolint (Ecolint, Group A)
- swiss_schools_hautlac (Haut-Lac, Group B)
- swiss_schools_iscs (ISCS, Group B)
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a8b9c1d2"
down_revision: Union[str, None] = "d4e5f6a8b9c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        INSERT INTO source_compliance
            (id, source_key, method, is_allowed, rate_limit_seconds,
             robots_txt_ok, tos_notes, max_requests_per_hour,
             auto_disable_on_block, consecutive_blocks, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'swiss_schools_zis', 'scraping', true, 3.0, true,
             'Watchlist ZIS Zurich. HTML estatico Finalsite, links a SchoolSpring.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'swiss_schools_isb', 'scraping', true, 3.0, true,
             'Watchlist ISB Basel. Finalsite board, vacante esporadica.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'swiss_schools_ecolint', 'scraping', true, 3.0, true,
             'Watchlist Ecolint Geneva. Drupal nativo, ~6 jobs por pagina, paginado.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'swiss_schools_hautlac', 'scraping', true, 3.0, true,
             'Watchlist Haut-Lac. HubSpot CMS, jobs en widgets rich-text.',
             60, true, 0, now(), now()),
            (gen_random_uuid(), 'swiss_schools_iscs', 'scraping', true, 3.0, true,
             'Watchlist ISCS Zug. HTML estatico, jobs en li con strong.',
             60, true, 0, now(), now())
        ON CONFLICT (source_key) DO NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM source_compliance
        WHERE source_key IN (
            'swiss_schools_zis',
            'swiss_schools_isb',
            'swiss_schools_ecolint',
            'swiss_schools_hautlac',
            'swiss_schools_iscs'
        );
    """)
