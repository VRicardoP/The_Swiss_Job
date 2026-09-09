"""Frozen historical document migration; no BFF imports and no live cutover.

The caller seals the batch ID, source snapshot and bindings BEFORE execution and
owns commit. UUIDs and timestamps are preserved. Per-row provenance is inserted
with the document, so a lost manifest/ACK can be reconstructed by replaying the
same sealed batch. A UUID collision, drift or ownership mismatch aborts the whole
batch. Freeze/drain both writers before using this administrative path.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

from jobhunt_core import documents
from jobhunt_core.api.document_schemas import DocumentCreateDTO
from jobhunt_core.api.deps import ensure_json_storable

_COMMON = {"id", "user_id", "doc_type", "content", "language", "created_at"}
_FIELDS = {
    "swissjob": _COMMON | {"job_hash", "job_title", "job_company"},
    "portfolio": _COMMON
    | {"application_id", "application_snapshot", "model_used", "generation_time_ms"},
}
_CORE_FIELDS = (
    "id",
    "profile_id",
    "offer_revision_id",
    "doc_type",
    "content",
    "output_hash",
    "language",
    "source_ref",
    "context",
    "model_used",
    "generation_time_ms",
    "created_at",
)


class DocumentMigrationError(ValueError):
    """Safe diagnostics only: never embed source content in an exception."""


def _time(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DocumentMigrationError("document timestamp requires a timezone")
    return value.astimezone(timezone.utc)


def _canonical(value):
    def convert(item):
        if isinstance(item, datetime):
            return _time(item).isoformat()
        if isinstance(item, uuid.UUID):
            return str(item)
        raise TypeError("unsupported snapshot type")

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=convert,
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def prepare_batch(*, batch_id, origin, consumer, bindings, rows):
    """Validate every row before database writes. Unknown source columns fail closed."""
    try:
        batch_id = uuid.UUID(str(batch_id))
        if origin not in _FIELDS or not isinstance(consumer, str) or not consumer:
            raise DocumentMigrationError("unsupported origin or consumer")
        mapping = {str(owner): uuid.UUID(str(pid)) for owner, pid in bindings.items()}
        if len(set(mapping.values())) != len(mapping):
            raise DocumentMigrationError("two source owners share one core profile")
        prepared, seen = [], set()
        for raw in rows:
            if set(raw) != _FIELDS[origin]:
                raise DocumentMigrationError(
                    "source columns differ from the migration contract"
                )
            row = dict(raw)
            row["id"], row["created_at"] = uuid.UUID(str(row["id"])), _time(
                row["created_at"]
            )
            owner = str(row["user_id"])
            if owner not in mapping or row["id"] in seen:
                raise DocumentMigrationError("unbound owner or repeated source UUID")
            seen.add(row["id"])
            if origin == "swissjob":
                row["user_id"] = uuid.UUID(owner)
                if (
                    not isinstance(row["job_hash"], str)
                    or not 1 <= len(row["job_hash"]) <= 36
                    or (len(row["job_hash"]) > 32 and str(uuid.UUID(row["job_hash"])) != row["job_hash"])
                ):
                    raise DocumentMigrationError("invalid document job reference")
                for key, limit in (("job_title", 500), ("job_company", 300)):
                    if row[key] is not None and (
                        not isinstance(row[key], str) or len(row[key]) > limit
                    ):
                        raise DocumentMigrationError("invalid job snapshot")
                context = {key: row[key] for key in ("job_title", "job_company")}
                ref, model, elapsed = row["job_hash"], None, None
            else:
                if type(row["user_id"]) is not int or row["user_id"] <= 0:
                    raise DocumentMigrationError("invalid portfolio owner")
                row["application_id"] = uuid.UUID(str(row["application_id"]))
                if not isinstance(row["application_snapshot"], dict):
                    raise DocumentMigrationError("invalid application snapshot")
                if not isinstance(row["model_used"], str) or not isinstance(
                    row["language"], str
                ):
                    raise DocumentMigrationError("portfolio metadata cannot be null")
                context = {
                    "portfolio_user_id": row["user_id"],
                    "application_snapshot": row["application_snapshot"],
                }
                ref, model, elapsed = (
                    str(row["application_id"]),
                    row["model_used"],
                    row["generation_time_ms"],
                )
            source_hash = digest(row)
            context["_migration"] = {
                "batch_id": str(batch_id),
                "origin": origin,
                "source_sha256": source_hash,
            }
            values = DocumentCreateDTO(
                doc_type=row["doc_type"],
                content=row["content"],
                language=row["language"],
                source_ref=ref,
                context=context,
                model_used=model,
                generation_time_ms=elapsed,
            ).model_dump()
            ensure_json_storable(values)
            if (
                len(values["content"].encode()) > 1_000_000
                or len(_canonical(context).encode()) > 65536
            ):
                raise DocumentMigrationError(
                    "source document exceeds the core boundary"
                )
            values.update(
                id=row["id"],
                profile_id=mapping[owner],
                created_at=row["created_at"],
                output_hash=hashlib.sha256(row["content"].encode()).hexdigest(),
            )
            prepared.append(
                {"source": row, "source_sha256": source_hash, "target": values}
            )
        prepared.sort(key=lambda item: str(item["target"]["id"]))
        result = {
            "batch_id": str(batch_id),
            "origin": origin,
            "consumer": consumer,
            "bindings": mapping,
            "documents": prepared,
        }
        result["seal"] = digest(result)
        return result
    except DocumentMigrationError:
        raise
    except Exception:
        raise DocumentMigrationError("invalid historical document snapshot") from None


async def import_batch(session, batch):
    """INSERT + provenance + event are atomic. The caller commits or rolls back."""
    unsigned = {key: value for key, value in batch.items() if key != "seal"}
    if digest(unsigned) != batch.get("seal"):
        raise DocumentMigrationError("batch seal mismatch")
    # Re-derive targets from the source, not merely from a caller-supplied digest.
    checked = prepare_batch(
        batch_id=batch["batch_id"],
        origin=batch["origin"],
        consumer=batch["consumer"],
        bindings=batch["bindings"],
        rows=[item["source"] for item in batch["documents"]],
    )
    if checked["seal"] != batch["seal"]:
        raise DocumentMigrationError("batch transformation mismatch")
    # JSON persistence loses UUID/datetime types; use the validated reconstruction.
    batch = checked
    inserted, existing, manifest = [], [], []
    async with session.begin_nested():
        for pid in sorted(set(batch["bindings"].values()), key=str):
            consumer = await session.scalar(
                sa.text(
                    "SELECT c.name FROM profiles p JOIN consumers c ON c.id=p.consumer_id "
                    "WHERE p.id=:pid FOR UPDATE OF p"
                ),
                {"pid": pid},
            )
            if consumer != batch["consumer"]:
                raise DocumentMigrationError(
                    "profile ownership changed or profile missing"
                )
        for item in batch["documents"]:
            expected = item["target"]
            context = _canonical(expected["context"])
            size = await session.scalar(
                sa.text("SELECT octet_length(CAST(:j AS jsonb)::text)"), {"j": context}
            )
            if size > 65536:
                raise DocumentMigrationError(
                    "expanded context exceeds the core boundary"
                )
            did = await session.scalar(
                sa.text(
                    "INSERT INTO generated_documents (id,profile_id,offer_revision_id,doc_type,content,"
                    "output_hash,language,source_ref,context,model_used,generation_time_ms,created_at) "
                    "VALUES (:id,:profile_id,:offer_revision_id,:doc_type,:content,:output_hash,:language,"
                    ":source_ref,CAST(:context AS jsonb),:model_used,:generation_time_ms,:created_at) "
                    "ON CONFLICT (id) DO NOTHING RETURNING id"
                ),
                {**expected, "context": context},
            )
            actual = (
                (
                    await session.execute(
                        sa.text("SELECT * FROM generated_documents WHERE id=:id"),
                        {"id": expected["id"]},
                    )
                )
                .mappings()
                .one()
            )
            material = {key: actual[key] for key in _CORE_FIELDS}
            if (
                digest(material) != digest(expected)
                or actual["version"] != 1
                or actual["async_state"] != "ready"
                or actual["pdf_location"] is not None
            ):
                raise DocumentMigrationError("target UUID collision or material drift")
            if did is not None:
                await documents.emit_changed(
                    session, expected["profile_id"], did, batch["consumer"]
                )
                inserted.append(str(did))
            else:
                existing.append(str(expected["id"]))
            manifest.append(
                {
                    "id": str(expected["id"]),
                    "profile_id": str(expected["profile_id"]),
                    "source_sha256": item["source_sha256"],
                    "target_sha256": digest(material),
                }
            )
    return {
        "batch_id": batch["batch_id"],
        "seal": batch["seal"],
        "inserted": inserted,
        "already_imported": existing,
        "documents": manifest,
        "verdict": "verified",
    }


def restore_rows(core_rows, *, origin, bindings):
    """Project an entire frozen core document snapshot back into one BFF.

    Includes post-cutover creations and excludes deletions by construction. The
    caller reconciles the complete owned collection transactionally under freeze;
    it must not append blindly to retained pre-cutover rows or resurrect deletions.
    """
    reverse = {str(pid): owner for owner, pid in bindings.items()}
    if len(reverse) != len(bindings) or origin not in _FIELDS:
        raise DocumentMigrationError("invalid reverse bindings")
    result = []
    for doc in core_rows:
        if str(doc["profile_id"]) not in reverse:
            raise DocumentMigrationError("snapshot contains an unowned profile")
        if (
            hashlib.sha256(doc["content"].encode()).hexdigest() != doc["output_hash"]
            or doc["version"] != 1
            or doc["async_state"] != "ready"
            or doc["pdf_location"] is not None
        ):
            raise DocumentMigrationError("unsupported or corrupt core document")
        owner, context = reverse[str(doc["profile_id"])], doc["context"]
        row = {
            key: doc[key]
            for key in ("id", "doc_type", "content", "language", "created_at")
        }
        if origin == "swissjob":
            row.update(
                user_id=str(owner),
                job_hash=doc["source_ref"],
                job_title=context.get("job_title"),
                job_company=context.get("job_company"),
            )
        else:
            if context.get("portfolio_user_id") != int(owner):
                raise DocumentMigrationError("portfolio context ownership mismatch")
            row.update(
                user_id=int(owner),
                application_id=doc["source_ref"],
                application_snapshot=context.get("application_snapshot", {}),
                model_used=doc["model_used"],
                generation_time_ms=doc["generation_time_ms"],
            )
        result.append(row)
    # Reuse all input boundaries, including legacy field lengths and nullability.
    validated = prepare_batch(
        batch_id=uuid.UUID(int=0),
        origin=origin,
        consumer="validation-only",
        bindings=bindings,
        rows=result,
    )
    return [item["source"] for item in validated["documents"]]
