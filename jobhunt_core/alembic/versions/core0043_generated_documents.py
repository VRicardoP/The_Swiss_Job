"""E.1: immutable document content, independent of the lifetime of its offer.

No live reader/writer is flipped by this additive migration. Profile deletion
is RESTRICTed until its documents are explicitly erased; corpus deletion only
nulls the optional offer reference. Populated storage cannot be downgraded away.
"""
from alembic import op
from jobhunt_core.config import settings

revision = "core0043"
down_revision = "core0042"
branch_labels = depends_on = None
S = settings.CORE_DB_SCHEMA


def upgrade():
    op.execute(f"""
        CREATE TABLE {S}.generated_documents (
            id uuid PRIMARY KEY,
            profile_id uuid NOT NULL REFERENCES {S}.profiles(id),
            offer_revision_id uuid REFERENCES {S}.offer_revisions(id) ON DELETE SET NULL,
            doc_type text NOT NULL CHECK (doc_type IN ('cv','cover_letter')),
            version integer NOT NULL DEFAULT 1 CHECK (version > 0),
            async_state text NOT NULL DEFAULT 'ready' CHECK (async_state = 'ready'),
            content text NOT NULL CHECK (octet_length(content) <= 1000000),
            output_hash text NOT NULL CHECK (output_hash ~ '^[0-9a-f]{{64}}$'),
            pdf_location text,
            language varchar(5),
            source_ref varchar(512),
            context jsonb NOT NULL DEFAULT '{{}}'::jsonb
                CHECK (jsonb_typeof(context) = 'object' AND octet_length(context::text) <= 65536),
            model_used varchar(100),
            generation_time_ms integer CHECK (generation_time_ms >= 0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX ix_documents_profile_page ON {S}.generated_documents
            (profile_id, created_at DESC, id DESC);
        CREATE INDEX ix_documents_offer ON {S}.generated_documents (offer_revision_id)
            WHERE offer_revision_id IS NOT NULL;
        CREATE FUNCTION {S}.guard_document_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF (to_jsonb(NEW) - 'offer_revision_id') IS DISTINCT FROM
               (to_jsonb(OLD) - 'offer_revision_id') OR
               (NEW.offer_revision_id IS DISTINCT FROM OLD.offer_revision_id
                AND NEW.offer_revision_id IS NOT NULL) THEN
                RAISE EXCEPTION 'generated document content is immutable';
            END IF;
            RETURN NEW;
        END $$;
        CREATE TRIGGER trg_document_immutable BEFORE UPDATE ON {S}.generated_documents
            FOR EACH ROW EXECUTE FUNCTION {S}.guard_document_immutable();
    """)


def downgrade():
    # Guard and DROP must see the same empty table; a late INSERT cannot slip in.
    # Operational quiesce is still required; do not wait on an active writer.
    op.execute(f"LOCK TABLE {S}.generated_documents IN ACCESS EXCLUSIVE MODE NOWAIT")
    op.execute(f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM {S}.generated_documents)
           OR EXISTS (SELECT 1 FROM {S}.integration_outbox WHERE type='document.changed') THEN
            RAISE EXCEPTION 'documents remain: migrate or explicitly reconcile before downgrade';
        END IF;
    END $$""")
    op.execute(f"DROP TABLE {S}.generated_documents")
    op.execute(f"DROP FUNCTION {S}.guard_document_immutable()")
