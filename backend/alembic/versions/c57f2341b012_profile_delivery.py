"""Capture all profile writers, including retained CV workers, without lost deliveries."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c57f2341b012"
down_revision = "b46e1230a901"
branch_labels = depends_on = None

# Frozen migration SQL. Tests install these same triggers after ORM create_all,
# just as the existing tsvector trigger is installed in their fixture.
TRIGGER_DDL = (
    """CREATE OR REPLACE FUNCTION profile_source_payload(p user_profiles) RETURNS jsonb
       LANGUAGE sql IMMUTABLE AS $$ SELECT jsonb_build_object(
         'title',p.title, 'cv_text',p.cv_text,
         'skills',COALESCE(NULLIF(p.skills,'null'::jsonb),'[]'::jsonb),
         'languages',COALESCE(NULLIF(p.languages,'null'::jsonb),'[]'::jsonb),
         'locations',COALESCE(NULLIF(p.locations,'null'::jsonb),'[]'::jsonb),
         'experience_years',p.experience_years, 'salary_min',p.salary_min,
         'salary_max',p.salary_max, 'remote_pref',p.remote_pref) $$""",
    """CREATE OR REPLACE FUNCTION queue_profile_source_content() RETURNS trigger
       LANGUAGE plpgsql AS $$ DECLARE uid uuid; payload jsonb; BEGIN
         IF TG_OP='DELETE' THEN
           uid := OLD.user_id;
           -- Parent deletion cascades the outbox too; never recreate it.
           IF NOT EXISTS (SELECT 1 FROM users WHERE id=uid) THEN RETURN OLD; END IF;
           payload := jsonb_build_object('title',NULL,'cv_text',NULL,'skills','[]'::jsonb,
             'languages','[]'::jsonb,'locations','[]'::jsonb,'experience_years',NULL,
             'salary_min',NULL,'salary_max',NULL,'remote_pref','any');
         ELSE
           uid := NEW.user_id;
           payload := profile_source_payload(NEW);
           IF TG_OP='UPDATE' THEN
             IF OLD.user_id IS DISTINCT FROM NEW.user_id THEN
               RAISE EXCEPTION 'profile ownership cannot change implicitly';
             END IF;
             IF payload IS NOT DISTINCT FROM profile_source_payload(OLD) THEN RETURN NEW; END IF;
           END IF;
         END IF;
         INSERT INTO profile_sync_state(user_id,version,content,active)
         SELECT uid,1,payload,is_active FROM users WHERE id=uid
         ON CONFLICT(user_id) DO UPDATE SET
           version=profile_sync_state.version+1,content=EXCLUDED.content,
           last_error=NULL,updated_at=clock_timestamp();
         -- Do NOT replace active here: a concurrent users edit owns that part.
         RETURN COALESCE(NEW,OLD);
       END $$""",
    """CREATE OR REPLACE FUNCTION queue_profile_source_activity() RETURNS trigger
       LANGUAGE plpgsql AS $$ BEGIN
         IF TG_OP='UPDATE' AND OLD.is_active IS NOT DISTINCT FROM NEW.is_active THEN RETURN NEW; END IF;
         INSERT INTO profile_sync_state(user_id,version,active)
         VALUES(NEW.id,1,NEW.is_active)
         ON CONFLICT(user_id) DO UPDATE SET
           version=profile_sync_state.version+1,active=EXCLUDED.active,
           last_error=NULL,updated_at=clock_timestamp();
         -- Do NOT replace content: the profile writer owns that part.
         RETURN NEW;
       END $$""",
    "DROP TRIGGER IF EXISTS profile_source_content ON user_profiles",
    "CREATE TRIGGER profile_source_content AFTER INSERT OR UPDATE OR DELETE ON user_profiles FOR EACH ROW EXECUTE FUNCTION queue_profile_source_content()",
    "DROP TRIGGER IF EXISTS profile_source_activity ON users",
    "CREATE TRIGGER profile_source_activity AFTER INSERT OR UPDATE OF is_active ON users FOR EACH ROW EXECUTE FUNCTION queue_profile_source_activity()",
)


def upgrade():
    op.execute("SET LOCAL lock_timeout='5s'")
    # Seed and install atomically: there must be no edit between the two.
    op.execute("LOCK TABLE users,user_profiles IN SHARE ROW EXCLUSIVE MODE")
    op.create_table(
        "profile_sync_state",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column(
            "delivered_version", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("content", postgresql.JSONB()),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "version > 0 AND delivered_version >= 0 AND delivered_version <= version",
            name="ck_profile_sync_version",
        ),
    )
    for statement in TRIGGER_DDL:
        op.execute(statement)
    op.execute("""INSERT INTO profile_sync_state(user_id,version,content,active)
        SELECT u.id,1,CASE WHEN p.id IS NOT NULL THEN profile_source_payload(p) END,u.is_active
        FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id""")


def downgrade():
    op.execute("LOCK TABLE users,user_profiles IN SHARE ROW EXCLUSIVE MODE NOWAIT")
    op.execute("LOCK TABLE profile_sync_state IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute("""DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM profile_sync_state) THEN
            RAISE EXCEPTION 'profile delivery requires explicit authority reconciliation before downgrade';
        END IF;
    END $$""")
    op.execute("DROP TRIGGER profile_source_content ON user_profiles")
    op.execute("DROP TRIGGER profile_source_activity ON users")
    op.execute("DROP FUNCTION queue_profile_source_content()")
    op.execute("DROP FUNCTION queue_profile_source_activity()")
    op.execute("DROP FUNCTION profile_source_payload(user_profiles)")
    op.drop_table("profile_sync_state")
