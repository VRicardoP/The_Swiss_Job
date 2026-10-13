"""A Core vacancy UUID must resolve without a local Job row."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from models.job import Job
from routers.documents import _load_job
from services.catalog import CoreUnavailableError


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["ok", "missing", "unavailable"])
async def test_core_offer_resolves_without_local_job(db_session, monkeypatch, outcome):
    ref = str(uuid.uuid4())
    catalog = SimpleNamespace(get=AsyncMock())
    catalog.get.return_value = (
        SimpleNamespace(
            title="Core-only position",
            company="Core company",
            description="Core content",
            tags=["python"],
        )
        if outcome == "ok"
        else None
    )
    if outcome == "unavailable":
        catalog.get.side_effect = CoreUnavailableError("unavailable")
    monkeypatch.setattr(
        "routers.documents.resolve_catalog", AsyncMock(return_value=catalog)
    )

    if outcome == "ok":
        job = await _load_job(db_session, ref)
        assert job.title == "Core-only position"
    else:
        with pytest.raises(HTTPException) as error:
            await _load_job(db_session, ref)
        assert error.value.status_code == {"missing": 404, "unavailable": 503}[outcome]
    catalog.get.assert_awaited_once_with(ref)
    assert await db_session.scalar(select(Job).where(Job.hash == ref)) is None


@pytest.mark.parametrize(
    "ref", ["x" * 33, "x" * 36, "{00000000-0000-0000-0000-000000000001}"]
)
def test_document_reference_rejects_noncanonical_long_ids(ref):
    from pydantic import ValidationError

    from schemas.documents import GenerateDocumentRequest

    with pytest.raises(ValidationError):
        GenerateDocumentRequest(job_hash=ref, doc_type="cv")
