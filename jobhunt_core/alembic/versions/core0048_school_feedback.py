"""Preserve feedback on school observations, including quarantined observations."""

from alembic import op

from jobhunt_core.config import settings

revision = "core0048"
down_revision = "core0047"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    op.execute(
        f"ALTER TABLE {S}.profile_vacancy_state ADD COLUMN feedback_recorded_at timestamptz"
    )
    op.execute(
        f"ALTER TABLE {S}.school_applications ADD COLUMN feedback text "
        "CHECK (feedback IN ('thumbs_up','thumbs_down','applied','dismissed')), "
        "ADD COLUMN feedback_recorded_at timestamptz, "
        "ADD COLUMN feedback_implicit jsonb NOT NULL DEFAULT '[]'::jsonb "
        "CHECK (jsonb_typeof(feedback_implicit)='array')"
    )


def downgrade():
    # Reverting code is not permission to erase marks created since cutover.
    op.execute(f"LOCK TABLE {S}.school_applications IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute(f"LOCK TABLE {S}.profile_vacancy_state IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {S}.school_applications
                   WHERE feedback IS NOT NULL OR feedback_recorded_at IS NOT NULL
                   OR feedback_implicit <> '[]'::jsonb) THEN
            RAISE EXCEPTION 'school feedback remains';
        END IF;
        IF EXISTS (SELECT 1 FROM {S}.profile_vacancy_state WHERE feedback_recorded_at IS NOT NULL) THEN
            RAISE EXCEPTION 'vacancy feedback authority remains';
        END IF;
    END $$""")
    op.execute(
        f"ALTER TABLE {S}.school_applications DROP COLUMN feedback_implicit, DROP COLUMN feedback_recorded_at, DROP COLUMN feedback"
    )
    op.execute(
        f"ALTER TABLE {S}.profile_vacancy_state DROP COLUMN feedback_recorded_at"
    )
