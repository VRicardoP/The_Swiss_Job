"""Document-only cutover freeze. Reads remain available; generation drains safely."""

from fastapi import HTTPException, Request

from config import settings


def assert_document_writes_enabled():
    if settings.DOCUMENT_WRITES_FROZEN:
        raise HTTPException(
            status_code=503, detail="Document writes are frozen for cutover."
        )


def block_document_writes(request: Request):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        assert_document_writes_enabled()
