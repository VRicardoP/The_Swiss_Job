"""Implementacion LOCAL de la capacidad documentos — A.SEAM (plan §15bis).

Codigo MOVIDO VERBATIM de routers/documents.py (persistencia, listado y
borrado de `generated_documents`, incluida la conversion a respuesta): con
routing 'local' el comportamiento es byte-identico al previo a la costura y
los tests existentes (test_documents.py) quedan intactos como evidencia. NO
cambiar logica aqui sin contract test que lo cubra.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.generated_document import GeneratedDocument
from models.job import Job
from schemas.documents import DocumentListResponse, GeneratedDocumentResponse


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
        db = self._db
        doc = GeneratedDocument(
            user_id=user_id,
            job_hash=job_hash,
            doc_type=doc_type,
            content=content,
            language=language,
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
            .order_by(GeneratedDocument.created_at.desc())
        )
        rows = (await db.execute(stmt)).all()

        data = [
            _to_response(
                doc,
                job_title=job.title if job else None,
                job_company=job.company if job else None,
            )
            for doc, job in rows
        ]

        return DocumentListResponse(data=data, total=len(data))

    async def delete(self, user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
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
