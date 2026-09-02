"""core0039 — captura transaccional exacta de procedencia C-4."""

from typing import Sequence, Union

from alembic import op

from jobhunt_core.config import settings

revision: str = "core0039"
down_revision: Union[str, None] = "core0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA

TABLES = (
    "applications",
    "application_status_events",
    "profile_vacancy_state",
    "saved_searches",
    "source_listings",
    "source_listing_incarnations",
    "source_listing_revisions",
    "offer_revision_sources",
    "link_evidence",
    "sources",
    "harvest_scopes",
    "vacancies",
    "offer_revisions",
    "dedup_candidates",
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {S}.trg_capture_portfolio_provenance()
        RETURNS trigger AS $$
        DECLARE
            active_run text;
            row_key text;
            row_data jsonb;
        BEGIN
            active_run := current_setting(
                'jobhunt.portfolio_provenance_run', true
            );
            IF active_run IS NULL OR active_run = '' THEN
                RETURN NEW;
            END IF;
            row_data := to_jsonb(NEW);
            row_key := CASE TG_TABLE_NAME
                WHEN 'profile_vacancy_state' THEN
                    (row_data->>'profile_id') || ':' ||
                    (row_data->>'vacancy_id')
                WHEN 'offer_revision_sources' THEN
                    (row_data->>'offer_revision_id') || ':' ||
                    (row_data->>'source_listing_revision_id')
                ELSE (row_data->>'id')
            END;
            INSERT INTO pg_temp.portfolio_provenance_log
                (table_name, row_key)
            VALUES (TG_TABLE_NAME, row_key)
            ON CONFLICT DO NOTHING;
            RETURN NEW;
        EXCEPTION WHEN undefined_table THEN
            RAISE EXCEPTION
                'captura C-4 activa sin tabla temporal de procedencia';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_portfolio_provenance
            AFTER INSERT ON {S}.{table}
            FOR EACH ROW EXECUTE FUNCTION
                {S}.trg_capture_portfolio_provenance()
            """
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(
            f"DROP TRIGGER IF EXISTS {table}_portfolio_provenance "
            f"ON {S}.{table}"
        )
    op.execute(
        f"DROP FUNCTION IF EXISTS {S}.trg_capture_portfolio_provenance()"
    )
