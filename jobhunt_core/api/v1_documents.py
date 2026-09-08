"""E.1 document storage API. No existing BFF routing is changed by this router.

POST requires Idempotency-Key, but the receipt stores ONLY the document ID:
a replay cannot return deleted CV content or recreate a document during its TTL.
Profile ownership is checked even on replay. Content is immutable (new generation
= new document); DELETE honors If-Match. Locks: profile -> receipt/document.
"""
import json
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request

from jobhunt_core import documents
from jobhunt_core.api import document_schemas as schemas
from jobhunt_core.api.deps import ApiError, ensure_json_storable, error_404, get_session, require_scope
from jobhunt_core.api.http_contract import check_if_match, json_response, request_hash, with_etag
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.api.v1 import decode_vacancy_cursor, encode_vacancy_cursor

router = APIRouter(prefix="/v1/profiles/{profile_id}/documents", tags=["documents"])


def _dto(row):
    return schemas.DocumentDTO(**row).model_dump(mode="json")


@router.post("", status_code=201, response_model=schemas.DocumentDTO)
async def create_document(profile_id: uuid.UUID, body: schemas.DocumentCreateDTO, request: Request,
                          session=Depends(get_session), principal=Depends(require_scope("documents:write"))):
    key = request.headers.get("idempotency-key")
    if key is None:
        raise ApiError(400, "idempotency_key_required", "el alta de documento exige Idempotency-Key")
    values = body.model_dump()
    ensure_json_storable(values)
    # Byte limits mirror PostgreSQL; character counts alone miss multibyte text.
    if len(body.content.encode()) > 1_000_000 or len(json.dumps(body.context, ensure_ascii=False).encode()) > 65536:
        raise ApiError(400, "document_too_large", "contenido o contexto excede la cota de almacenamiento")
    # JSONB expands exponent notation; validate the actual stored representation.
    context_size = (await session.execute(sa.text(
        "SELECT octet_length(CAST(:context AS jsonb)::text)"
    ), {"context": json.dumps(body.context, ensure_ascii=False)})).scalar_one()
    if context_size > 65536:
        raise ApiError(400, "document_too_large", "contexto JSONB excede la cota de almacenamiento")

    destination = await documents.owner(session, profile_id, principal.consumer_id, write=True)
    if destination is None:
        raise error_404("perfil")

    async def handler():
        did = await documents.create(session, profile_id, values, destination)
        if did is None:
            raise error_404("revisión de oferta")
        return 201, {"id": str(did)}

    # FastAPI accepts upper-case/compact UUIDs; receipts and erase use canonical IDs.
    status, receipt = await run_idempotent(
        session, principal, f"POST /v1/profiles/{profile_id}/documents",
        request_hash(body.model_dump(mode="json")), key, handler,
    )
    row = await documents.fetch(session, profile_id, uuid.UUID(receipt["id"]), principal.consumer_id)
    if row is None:
        raise error_404("documento")
    return json_response(status, _dto(row))


@router.get("", response_model=schemas.DocumentsPageDTO)
async def list_documents(profile_id: uuid.UUID, request: Request,
                         session=Depends(get_session), principal=Depends(require_scope("documents:read")),
                         limit: int = Query(20, ge=1, le=20), cursor: str | None = None,
                         doc_type: schemas.DocType | None = None,
                         source_ref: str | None = Query(None, max_length=512)):
    if await documents.owner(session, profile_id, principal.consumer_id) is None:
        raise error_404("perfil")
    ensure_json_storable(source_ref)
    cur = decode_vacancy_cursor(cursor) if cursor else None
    rows, following = await documents.page(session, profile_id, principal.consumer_id,
                                          limit, cur, doc_type, source_ref)
    return with_etag(request, schemas.DocumentsPageDTO(
        items=[schemas.DocumentDTO(**r) for r in rows],
        next_cursor=encode_vacancy_cursor(*following) if following else None,
    ).model_dump(mode="json"))


@router.get("/{document_id}", response_model=schemas.DocumentDTO)
async def get_document(profile_id: uuid.UUID, document_id: uuid.UUID, request: Request,
                       session=Depends(get_session), principal=Depends(require_scope("documents:read"))):
    row = await documents.fetch(session, profile_id, document_id, principal.consumer_id)
    if row is None:
        raise error_404("documento")
    return with_etag(request, _dto(row))


@router.delete("/{document_id}", status_code=204)
async def delete_document(profile_id: uuid.UUID, document_id: uuid.UUID, request: Request,
                          session=Depends(get_session), principal=Depends(require_scope("documents:write"))):
    destination = await documents.owner(session, profile_id, principal.consumer_id, write=True)
    if destination is None:
        raise error_404("perfil")

    async def handler():
        row = await documents.fetch(session, profile_id, document_id, principal.consumer_id)
        if row is None:
            raise error_404("documento")
        check_if_match(request, _dto(row))
        await documents.delete(session, profile_id, document_id, destination)
        return 204, None

    status, payload = await run_idempotent(
        session, principal, f"DELETE /v1/profiles/{profile_id}/documents/{document_id}",
        request_hash({}), request.headers.get("idempotency-key"), handler,
    )
    return json_response(status, payload)
