"""Local document storage, preserving the generation context independently of jobs.

The E preservation contract is covered by test_document_preservation. Owner erase
still cascades; deleting a job does not delete personal documents.
"""

import uuid
import base64
import json
from datetime import datetime

from fastapi import HTTPException

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from models.generated_document import GeneratedDocument
from models.job import Job
from .freeze import assert_document_writes_enabled
from schemas.documents import (
    DocumentListResponse,
    GeneratedDocumentResponse,
    DocumentPageResponse,
)


def _to_response(
    doc: GeneratedDocument,
    job_title: str | None = None,
    job_company: str | None = None,
) -> GeneratedDocumentResponse:
    return GeneratedDocumentResponse(
        id=doc.id,
        job_hash=doc.job_hash,
        doc_type=doc.doc_type,
        content=doc.content,
        language=doc.language,
        created_at=doc.created_at,
        job_title=job_title,
        job_company=job_company,
    )


class LocalDocuments:
    """Almacen actual: tabla `generated_documents`."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(
        self,
        user_id: uuid.UUID,
        job_hash: str,
        doc_type: str,
        content: str,
        language: str,
        job_title: str | None = None,
        job_company: str | None = None,
    ) -> GeneratedDocumentResponse:
        assert_document_writes_enabled()
        db = self._db
        if job_title is None or job_company is None:
            job = await db.get(Job, job_hash)
            if job is not None:
                job_title = job.title if job_title is None else job_title
                job_company = job.company if job_company is None else job_company
        doc = GeneratedDocument(
            user_id=user_id,
            job_hash=job_hash,
            doc_type=doc_type,
            content=content,
            language=language,
            job_title=job_title,
            job_company=job_company,
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)

        return _to_response(doc, job_title=job_title, job_company=job_company)

    async def list(
        self, user_id: uuid.UUID, job_hash: str, doc_type: str | None = None
    ) -> DocumentListResponse:
        db = self._db
        conditions = [
            GeneratedDocument.user_id == user_id,
            GeneratedDocument.job_hash == job_hash,
        ]
        if doc_type:
            conditions.append(GeneratedDocument.doc_type == doc_type)

        stmt = (
            select(GeneratedDocument, Job)
            .outerjoin(Job, GeneratedDocument.job_hash == Job.hash)
            .where(*conditions)
            .order_by(GeneratedDocument.created_at.desc(), GeneratedDocument.id.desc())
        )
        rows = (await db.execute(stmt)).all()

        data = [
            _to_response(
                doc,
                job_title=doc.job_title
                if doc.job_title is not None
                else job.title
                if job
                else None,
                job_company=doc.job_company
                if doc.job_company is not None
                else job.company
                if job
                else None,
            )
            for doc, job in rows
        ]

        return DocumentListResponse(data=data, total=len(data))

    async def page(self, user_id, cursor=None):
        stmt = select(GeneratedDocument).where(GeneratedDocument.user_id == user_id)
        if cursor:
            try:
                if len(cursor) > 512:
                    raise ValueError()
                stamp, identity = json.loads(
                    base64.b64decode(cursor, altchars=b"-_", validate=True)
                )
                stamp, identity = datetime.fromisoformat(stamp), uuid.UUID(identity)
                if stamp.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise HTTPException(
                    status_code=400, detail="Invalid document cursor"
                ) from None
            stmt = stmt.where(
                tuple_(GeneratedDocument.created_at, GeneratedDocument.id)
                < tuple_(stamp, identity)
            )
        docs = (
            await self._db.scalars(
                stmt.order_by(
                    GeneratedDocument.created_at.desc(), GeneratedDocument.id.desc()
                ).limit(21)
            )
        ).all()
        next_cursor = None
        if len(docs) > 20:
            last = docs[19]
            next_cursor = base64.urlsafe_b64encode(
                json.dumps([last.created_at.isoformat(), str(last.id)]).encode()
            ).decode()
        return DocumentPageResponse(
            data=[
                _to_response(doc, job_title=doc.job_title, job_company=doc.job_company)
                for doc in docs[:20]
            ],
            next_cursor=next_cursor,
        )

    async def get(self, user_id, document_id):
        doc = await self._db.scalar(
            select(GeneratedDocument).where(
                GeneratedDocument.id == document_id,
                GeneratedDocument.user_id == user_id,
            )
        )
        if doc is None:
            return None
        return _to_response(doc, job_title=doc.job_title, job_company=doc.job_company)

    async def delete(self, user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        assert_document_writes_enabled()
        db = self._db
        doc = (
            await db.execute(
                select(GeneratedDocument).where(
                    GeneratedDocument.id == document_id,
                    GeneratedDocument.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            return False
        await db.delete(doc)
        await db.commit()
        return True
