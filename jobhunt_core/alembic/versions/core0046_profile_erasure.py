"""Durable erasure receipts and anti-resurrection fence (not backup erasure)."""

from alembic import op
from jobhunt_core.config import settings

revision = "core0046"
down_revision = "core0045"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    # A row lock detects stale REPEATABLE READ snapshots as serialization errors.
    # Advisory locks alone do not refresh their view of a committed tombstone.
    op.execute(f"""CREATE TABLE {S}.profile_erasure_fence (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        generation bigint NOT NULL DEFAULT 0
    )""")
    op.execute(f"INSERT INTO {S}.profile_erasure_fence DEFAULT VALUES")
    op.execute(f"""CREATE TABLE {S}.profile_erasure_receipts (
        profile_id uuid PRIMARY KEY,
        consumer_id uuid NOT NULL REFERENCES {S}.consumers(id),
        external_ref text NOT NULL,
        source_profile_pks text[] NOT NULL DEFAULT ARRAY[]::text[],
        erased_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (consumer_id, external_ref)
    )""")
    op.execute(f"""CREATE TABLE {S}.profile_erasure_acks (
        profile_id uuid NOT NULL REFERENCES {S}.profile_erasure_receipts(profile_id) ON DELETE CASCADE,
        replica_id text NOT NULL CHECK (replica_id IN ('swissjob-live','swissjob-cdc')),
        acknowledged_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY (profile_id, replica_id)
    )""")
    op.execute(f"""CREATE FUNCTION {S}.advance_erasure_fence() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        PERFORM 1 FROM {S}.profile_erasure_fence FOR UPDATE NOWAIT;
        UPDATE {S}.profile_erasure_fence SET generation=generation+1;
        RETURN NEW;
        END $$""")
    op.execute(f"""CREATE TRIGGER advance_erasure_fence
        BEFORE INSERT ON {S}.profile_erasure_receipts
        FOR EACH ROW EXECUTE FUNCTION {S}.advance_erasure_fence()""")
    op.execute(f"""CREATE FUNCTION {S}.redact_erased_shadow_payload() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        IF NEW.src_table <> 'user_profiles' THEN RETURN NEW; END IF;
        -- Staging's table lock is already held: never wait staging -> fence
        -- against an eraser's fence -> staging. Capture retries the transaction.
        PERFORM 1 FROM {S}.profile_erasure_fence FOR SHARE NOWAIT;
        IF EXISTS (
            SELECT 1 FROM {S}.profile_erasure_receipts r
            JOIN {S}.consumers c ON c.id=r.consumer_id
            WHERE c.name='swissjob-shadow' AND (
                r.external_ref=NEW.payload->>'user_id' OR
                NEW.pk=ANY(r.source_profile_pks))
        ) THEN NEW.payload='{{}}'::jsonb; END IF;
        RETURN NEW;
        END $$""")
    op.execute(f"""CREATE TRIGGER redact_erased_shadow_payload
        BEFORE INSERT OR UPDATE OF payload ON {S}.shadow_change_log
        FOR EACH ROW EXECUTE FUNCTION {S}.redact_erased_shadow_payload()""")
    # Serialize enrollment and erasure even when no profile row exists. The
    # hash is ONLY a lock key: collisions serialize, never alias identities.
    op.execute(f"""CREATE FUNCTION {S}.guard_erased_profile() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
        -- Direct DML can already hold profile/index locks. Fail retryably
        -- instead of reversing the eraser's fence -> identity -> profile order.
        PERFORM 1 FROM {S}.profile_erasure_fence FOR SHARE NOWAIT;
        IF TG_OP = 'UPDATE' THEN
            -- UPDATE already owns the row: never wait row -> identity against
            -- erasure's identity -> row. A conflict is a retryable transaction.
            IF NOT pg_try_advisory_xact_lock(hashtextextended(
                NEW.consumer_id::text || ':' || NEW.external_ref, 73146)) THEN
                RAISE EXCEPTION 'concurrent profile erasure' USING ERRCODE='40001';
            END IF;
        ELSE
            PERFORM pg_advisory_xact_lock(hashtextextended(
                NEW.consumer_id::text || ':' || NEW.external_ref, 73146));
        END IF;
        IF EXISTS (SELECT 1 FROM {S}.profile_erasure_receipts
            WHERE profile_id=NEW.id OR (consumer_id=NEW.consumer_id AND external_ref=NEW.external_ref))
        THEN RAISE EXCEPTION 'profile has been erased' USING ERRCODE='23514'; END IF;
        RETURN NEW;
        END $$""")
    op.execute(f"""CREATE TRIGGER guard_erased_profile
        BEFORE INSERT OR UPDATE OF id, consumer_id, external_ref ON {S}.profiles
        FOR EACH ROW EXECUTE FUNCTION {S}.guard_erased_profile()""")


def downgrade():
    # Removing receipts after real erasures would permit old CDC/backups to
    # recreate accounts. Empty development databases can still downgrade.
    op.execute(
        f"LOCK TABLE {S}.profile_erasure_receipts IN ACCESS EXCLUSIVE MODE NOWAIT"
    )
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {S}.profile_erasure_receipts) THEN
            RAISE EXCEPTION 'erasure receipts must be preserved';
        END IF;
    END $$""")
    op.execute(f"DROP TRIGGER redact_erased_shadow_payload ON {S}.shadow_change_log")
    op.execute(f"DROP FUNCTION {S}.redact_erased_shadow_payload()")
    op.execute(f"DROP TRIGGER guard_erased_profile ON {S}.profiles")
    op.execute(f"DROP FUNCTION {S}.guard_erased_profile()")
    op.execute(f"DROP TABLE {S}.profile_erasure_acks")
    op.execute(f"DROP TRIGGER advance_erasure_fence ON {S}.profile_erasure_receipts")
    op.execute(f"DROP FUNCTION {S}.advance_erasure_fence()")
    op.execute(f"DROP TABLE {S}.profile_erasure_receipts")
    op.execute(f"DROP TABLE {S}.profile_erasure_fence")
