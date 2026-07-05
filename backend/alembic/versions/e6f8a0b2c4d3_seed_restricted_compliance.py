"""seed restricted-source compliance (jobcloud/linkedin/indeed/glassdoor/xing)

Revision ID: e6f8a0b2c4d3
Revises: d4e6f8a0b2c1
Create Date: 2026-07-04 00:05:00.000000

Registra las fuentes RESTRINGIDAS en source_compliance con is_allowed=false. Esto
las modela como "restricted_ready": existen en el catálogo pero no se consultan sin
credencial de partner (el gate real está en providers/__init__.py _KEY_REQUIREMENTS).
NO scraping público de estos portales. Ver a.txt §6-7 y providers/restricted.py.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e6f8a0b2c4d3"
down_revision: Union[str, None] = "d4e6f8a0b2c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO source_compliance
            (id, source_key, method, is_allowed, rate_limit_seconds,
             robots_txt_ok, tos_notes, max_requests_per_hour,
             auto_disable_on_block, consecutive_blocks, created_at, updated_at)
        VALUES
            (gen_random_uuid(), 'jobcloud_partner', 'api', false, 2.0, false,
             'RESTRICTED (jobs.ch/jobup.ch): solo vía JobCloud partner/API/XML oficial o import del usuario. NO scraping público.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'linkedin_authorized', 'api', false, 2.0, false,
             'RESTRICTED (LinkedIn): solo vía Talent Solutions/Job Posting API o import de alertas. NO scraping autenticado.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'indeed_partner', 'api', false, 2.0, false,
             'RESTRICTED (Indeed): solo vía Partner APIs/Job Sync/feed aprobado. NO endpoints internos.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'glassdoor_partner', 'api', false, 2.0, false,
             'RESTRICTED (Glassdoor): solo vía API partner aprobada (posible canal Indeed). Sin reviews/salarios por HTML.',
             120, true, 0, now(), now()),
            (gen_random_uuid(), 'xing_partner', 'api', false, 2.0, false,
             'RESTRICTED (XING): solo vía e-recruiting feed/API con partner approval.',
             120, true, 0, now(), now())
        ON CONFLICT (source_key) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM source_compliance
        WHERE source_key IN (
            'jobcloud_partner', 'linkedin_authorized', 'indeed_partner',
            'glassdoor_partner', 'xing_partner'
        );
        """
    )
