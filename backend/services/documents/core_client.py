"""E.2: HTTP document adapter; no live storage cutover is performed here.

Identity is the existing per-user profile map. An unbound CoreDocuments()
retains the old Unsupported boundary used by the live resolver until migration.
A bound adapter requires a caller-owned operation UUID for POST: no random or
content-derived key that could duplicate an ambiguous retry or replay a new CV.
The canary still writes LOCAL; enabling core writes needs a separate cutover.
"""

import asyncio
import hashlib
import uuid
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from config import settings
from schemas.documents import (
    DocumentListResponse,
    GeneratedDocumentResponse,
    DocumentPageResponse,
)
from services.matching.identity import resolve_core_profile_id
from .port import CoreUnavailableError, DocumentsUnsupportedError

_MAX_PAGES = (
    100  # fail, never truncate; paginate the BFF before exceeding 2000 docs/job
)


class _Context(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_title: str | None = None
    job_company: str | None = None


class _Document(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: uuid.UUID
    profile_id: uuid.UUID
    source_ref: str
    doc_type: Literal["cv", "cover_letter"]
    content: str
    language: str | None
    created_at: AwareDatetime
    version: int = Field(ge=1, le=1, strict=True)
    async_state: Literal["ready"]
    output_hash: str
    context: _Context


class _Page(BaseModel):
    items: list[_Document]
    next_cursor: str | None = None


def default_client_factory():
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )


def _view(doc, pid, job_hash=None):
    if doc.profile_id != pid or (job_hash is not None and doc.source_ref != job_hash):
        raise CoreUnavailableError("identidad de documento incompatible")
    if doc.output_hash != hashlib.sha256(doc.content.encode()).hexdigest():
        raise CoreUnavailableError("hash del documento incompatible")
    return GeneratedDocumentResponse(
        id=doc.id,
        job_hash=doc.source_ref,
        doc_type=doc.doc_type,
        content=doc.content,
        language=doc.language,
        created_at=doc.created_at,
        job_title=doc.context.job_title,
        job_company=doc.context.job_company,
    )


class CoreDocuments:
    def __init__(self, db=None, client_factory=None, *, profile_id=None, user_id=None):
        if (profile_id is None) != (user_id is None):
            raise ValueError("profile_id and user_id must be bound together")
        self.profile_id = profile_id
        self._user_id = user_id
        self._db = db
        self._client_factory = client_factory or default_client_factory

    async def _profile(self, user_id):
        if self.profile_id is not None:
            if user_id != self._user_id or not settings.CORE_CONSUMER_KEY:
                raise CoreUnavailableError("propietario o credencial core incompatible")
            return self.profile_id
        if self._db is None:
            raise DocumentsUnsupportedError(
                "documentos: vinculación E pendiente del corte"
            )
        if not settings.CORE_CONSUMER_KEY:
            raise CoreUnavailableError("credencial core no configurada")
        pid = await resolve_core_profile_id(self._db, user_id)
        if pid is None:
            raise CoreUnavailableError("usuario sin perfil core vinculado")
        return pid

    @asynccontextmanager
    async def _client(self):
        try:
            # End-to-end bound, not one fresh timeout per page.
            async with asyncio.timeout(
                max(float(settings.CORE_HTTP_TIMEOUT_SECONDS), 1) * 2
            ):
                async with self._client_factory() as client:
                    yield client
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            # No response bodies, CV text, URLs or credentials in diagnostics.
            raise CoreUnavailableError(
                "core documentos inaccesible o respuesta inválida"
            ) from None

    async def create(
        self,
        user_id,
        job_hash,
        doc_type,
        content,
        language,
        job_title=None,
        job_company=None,
        *,
        operation_id=None,
    ):
        pid = await self._profile(user_id)
        if not isinstance(operation_id, uuid.UUID):
            raise CoreUnavailableError(
                "alta core exige UUID de operación estable del solicitante"
            )
        body = {
            "doc_type": doc_type,
            "content": content,
            "language": language,
            "source_ref": job_hash,
            "context": {"job_title": job_title, "job_company": job_company},
        }
        async with self._client() as client:
            response = await client.post(
                f"/profiles/{pid}/documents",
                json=body,
                headers={"Idempotency-Key": str(operation_id)},
            )
            if response.status_code != 201:
                raise CoreUnavailableError(
                    f"alta de documento core: HTTP {response.status_code}"
                )
            doc = _Document.model_validate(response.json())
            result = _view(doc, pid, job_hash)
            if (
                doc.doc_type,
                doc.content,
                doc.language,
                doc.context.job_title,
                doc.context.job_company,
            ) != (doc_type, content, language, job_title, job_company):
                raise CoreUnavailableError(
                    "recibo core no corresponde al documento enviado"
                )
            return result

    async def list(self, user_id, job_hash, doc_type=None):
        pid = await self._profile(user_id)
        items, seen_ids, seen_cursors = [], set(), set()
        params = {"source_ref": job_hash, "limit": 20}
        if doc_type is not None:
            params["doc_type"] = doc_type
        async with self._client() as client:
            for _ in range(_MAX_PAGES):
                response = await client.get(f"/profiles/{pid}/documents", params=params)
                if response.status_code != 200:
                    raise CoreUnavailableError(
                        f"listado de documentos core: HTTP {response.status_code}"
                    )
                page = _Page.model_validate(response.json())
                for doc in page.items:
                    if doc.id in seen_ids or (
                        doc_type is not None and doc.doc_type != doc_type
                    ):
                        raise CoreUnavailableError(
                            "listado core repetido o fuera del filtro"
                        )
                    seen_ids.add(doc.id)
                    items.append(_view(doc, pid, job_hash))
                if page.next_cursor is None:
                    return DocumentListResponse(data=items, total=len(items))
                if (
                    not page.next_cursor
                    or page.next_cursor in seen_cursors
                    or not page.items
                ):
                    raise CoreUnavailableError("cursor core sin progreso")
                seen_cursors.add(page.next_cursor)
                params["cursor"] = page.next_cursor
        raise CoreUnavailableError(
            "listado de documentos excede la cota; no se devuelve truncado"
        )

    async def page(self, user_id, cursor=None):
        pid = await self._profile(user_id)
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        async with self._client() as client:
            response = await client.get(f"/profiles/{pid}/documents", params=params)
            if response.status_code != 200:
                raise CoreUnavailableError(
                    f"biblioteca de documentos core: HTTP {response.status_code}"
                )
            page = _Page.model_validate(response.json())
            if (
                len(page.items) > 20
                or len({d.id for d in page.items}) != len(page.items)
                or (
                    page.next_cursor is not None
                    and (
                        not page.items
                        or not page.next_cursor
                        or page.next_cursor == cursor
                    )
                )
            ):
                raise CoreUnavailableError("pagina core sin progreso o incompatible")
            return DocumentPageResponse(
                data=[_view(doc, pid) for doc in page.items],
                next_cursor=page.next_cursor,
            )

    async def get(self, user_id, document_id):
        pid = await self._profile(user_id)
        async with self._client() as client:
            response = await client.get(f"/profiles/{pid}/documents/{document_id}")
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                raise CoreUnavailableError(
                    f"lectura de documento core: HTTP {response.status_code}"
                )
            doc = _Document.model_validate(response.json())
            if doc.id != document_id:
                raise CoreUnavailableError("identidad de documento incompatible")
            return _view(doc, pid)

    async def delete(self, user_id, document_id):
        pid = await self._profile(user_id)
        url = f"/profiles/{pid}/documents/{document_id}"
        async with self._client() as client:
            response = await client.get(url)
            if response.status_code == 404:
                return False
            if response.status_code != 200:
                raise CoreUnavailableError(
                    f"lectura de documento core: HTTP {response.status_code}"
                )
            doc = _Document.model_validate(response.json())
            _view(doc, pid)
            if doc.id != document_id:
                raise CoreUnavailableError("identidad de documento incompatible")
            etag = response.headers.get("etag", "")
            if not etag.startswith('"') or not etag.endswith('"'):
                raise CoreUnavailableError("documento core sin ETag fuerte")
            response = await client.delete(
                url,
                headers={
                    "If-Match": etag,
                    "Idempotency-Key": f"delete-document-{document_id}",
                },
            )
            if response.status_code == 404:
                return False
            if response.status_code != 204:
                raise CoreUnavailableError(
                    f"borrado de documento core: HTTP {response.status_code}"
                )
            return True
