"""E.15: shared schools, tenant-owned monitors and profile-owned school state.

School job details only extend a corpus vacancy. Unlinkable observations are
explicitly quarantined rather than presented as successfully imported vacancies.
Contact/configuration belongs to the monitor, never the shared school identity.
"""

from alembic import op

from jobhunt_core.config import settings

revision = "core0047"
down_revision = "core0046"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        f"""CREATE TABLE {S}.schools (
        id uuid PRIMARY KEY,
        school_key text NOT NULL UNIQUE CHECK (length(school_key) BETWEEN 1 AND 103),
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        country text NOT NULL CHECK (length(country)=2),
        created_at timestamptz NOT NULL DEFAULT now()
    )"""
    )
    op.execute(
        f"""CREATE TABLE {S}.school_monitors (
        id uuid PRIMARY KEY,
        consumer_id uuid NOT NULL REFERENCES {S}.consumers(id) ON DELETE CASCADE,
        school_id uuid NOT NULL REFERENCES {S}.schools(id),
        external_ref text NOT NULL CHECK (length(external_ref) BETWEEN 1 AND 100),
        settings jsonb NOT NULL CHECK (jsonb_typeof(settings)='object'),
        version bigint NOT NULL DEFAULT 1 CHECK (version>0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (consumer_id, external_ref), UNIQUE (id, consumer_id)
    )"""
    )
    op.execute(
        f"CREATE INDEX ix_school_monitors_school ON {S}.school_monitors(school_id)"
    )
    op.execute(
        f"""CREATE TABLE {S}.school_job_details (
        id uuid PRIMARY KEY,
        monitor_id uuid NOT NULL,
        consumer_id uuid NOT NULL,
        source_ref text NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 300),
        vacancy_id uuid REFERENCES {S}.vacancies(id),
        quarantine_reason text,
        metadata jsonb NOT NULL CHECK (jsonb_typeof(metadata)='object'),
        notified_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (monitor_id,consumer_id) REFERENCES {S}.school_monitors(id,consumer_id),
        CHECK ((vacancy_id IS NOT NULL AND quarantine_reason IS NULL)
            OR (vacancy_id IS NULL AND quarantine_reason IS NOT NULL AND length(quarantine_reason)>0)),
        UNIQUE (monitor_id,source_ref), UNIQUE (id,monitor_id,consumer_id)
    )"""
    )
    op.execute(
        f"CREATE INDEX ix_school_details_vacancy ON {S}.school_job_details(vacancy_id)"
    )
    op.execute(
        f"CREATE INDEX ix_school_details_consumer_created ON {S}.school_job_details(consumer_id,created_at DESC,id DESC)"
    )
    # Composite ownership is enforced by the database as well as by the API.
    op.execute(
        f"ALTER TABLE {S}.profiles ADD CONSTRAINT uq_profiles_id_consumer UNIQUE (id,consumer_id)"
    )
    op.execute(
        f"""CREATE TABLE {S}.school_profile_preferences (
        profile_id uuid PRIMARY KEY REFERENCES {S}.profiles(id) ON DELETE CASCADE,
        enabled boolean NOT NULL DEFAULT false,
        updated_at timestamptz NOT NULL DEFAULT now()
    )"""
    )
    op.execute(
        f"""CREATE TABLE {S}.school_applications (
        id uuid PRIMARY KEY,
        profile_id uuid NOT NULL,
        consumer_id uuid NOT NULL,
        monitor_id uuid NOT NULL,
        school_job_id uuid,
        source_ref text NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 300),
        status text NOT NULL CHECK (status IN (
            'detected','reviewed','drafted','interview','awaiting','followup_due',
            'draft_ready','sent','awaiting_response','follow_up_due',
            'interview_scheduled','closed_positive','closed_negative')),
        draft_content text,
        context jsonb NOT NULL DEFAULT '{{}}'::jsonb CHECK (jsonb_typeof(context)='object'),
        version bigint NOT NULL DEFAULT 1 CHECK (version>0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (profile_id,consumer_id) REFERENCES {S}.profiles(id,consumer_id) ON DELETE CASCADE,
        FOREIGN KEY (monitor_id,consumer_id) REFERENCES {S}.school_monitors(id,consumer_id),
        FOREIGN KEY (school_job_id,monitor_id,consumer_id) REFERENCES {S}.school_job_details(id,monitor_id,consumer_id),
        UNIQUE (profile_id,source_ref)
    )"""
    )
    op.execute(
        f"CREATE INDEX ix_school_apps_monitor ON {S}.school_applications(monitor_id)"
    )
    op.execute(
        f"CREATE INDEX ix_school_apps_job ON {S}.school_applications(school_job_id)"
    )


def downgrade():
    # Migration rollback is not data rollback. Never silently drop school state.
    tables = (
        "school_applications",
        "school_profile_preferences",
        "school_job_details",
        "school_monitors",
        "schools",
    )
    op.execute(
        "LOCK TABLE "
        + ",".join(f"{S}.{t}" for t in tables)
        + " IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    for table in tables:
        op.execute(
            f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {S}.{table}) THEN
                RAISE EXCEPTION 'school state remains: {table}';
            END IF;
        END $$"""
        )
    for table in tables:
        op.execute(f"DROP TABLE {S}.{table}")
    op.execute(f"ALTER TABLE {S}.profiles DROP CONSTRAINT uq_profiles_id_consumer")
