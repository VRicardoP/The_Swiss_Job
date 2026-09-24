"""Synthetic subprocess for test_document_adapter_contract.py, never a live tool."""

import asyncio
import os
import sys
import uuid

if os.environ.get("DOCUMENT_TEST_EXTRA_PACKAGES"):
    sys.path.append(os.environ["DOCUMENT_TEST_EXTRA_PACKAGES"])

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database import Base
from models.user import User
from models.generated_document import GeneratedDocument
from models.document_delivery import DocumentDelivery
from models.jobhunt_profile_map import JobhuntProfileMap
from models.jobhunt_routing import JobhuntRouting, CONSUMER_SWISSJOB
from services.documents.core_client import CoreDocuments
from services.documents.export import export_documents

url = os.environ["DOCUMENT_JOURNAL_DSN"]
assert make_url(url).database == os.environ["DOCUMENT_JOURNAL_DATABASE"]
assert make_url(url).database.startswith("jobhunt_suite_")
schema = os.environ["DOCUMENT_JOURNAL_SCHEMA"]
assert schema.startswith("document_delivery_") and schema.replace("_", "").isalnum()
engine = create_async_engine(
    url, connect_args={"server_settings": {"search_path": schema}}
)
sessions = async_sessionmaker(engine, expire_on_commit=False)
pid = uuid.UUID(os.environ["DOCUMENT_TEST_PROFILE"])
uid, op = uuid.uuid4(), uuid.uuid4()


async def run():
    try:
        async with engine.begin() as c:
            await c.execute(text(f'CREATE SCHEMA "{schema}"'))
            await c.run_sync(
                lambda c: Base.metadata.create_all(
                    c,
                    tables=[
                        User.__table__,
                        GeneratedDocument.__table__,
                        DocumentDelivery.__table__,
                        JobhuntProfileMap.__table__,
                        JobhuntRouting.__table__,
                    ],
                )
            )
        async with sessions() as db:
            db.add(
                User(
                    id=uid,
                    email="synthetic-export@example.invalid",
                    hashed_password="synthetic",
                )
            )
            await db.flush()
            db.add(JobhuntProfileMap(user_id=uid, core_profile_id=pid))
            db.add(
                JobhuntRouting(
                    consumer_id=CONSUMER_SWISSJOB,
                    capability="documents",
                    profile_id=uid,
                    mode="core_primary",
                )
            )
            db.add(
                GeneratedDocument(
                    user_id=uid,
                    job_hash="a" * 32,
                    doc_type="cv",
                    content="Synthetic retained output",
                    language="en",
                )
            )
            db.add(
                DocumentDelivery(
                    operation_id=op,
                    user_id=uid,
                    profile_id=pid,
                    request_hash="a" * 64,
                    payload_hash="b" * 64,
                    payload={"content": "Synthetic prepared output"},
                )
            )
            await db.commit()
        client = CoreDocuments(profile_id=pid, user_id=uid)
        expected = set()
        for _ in range(21):
            doc = await client.create(
                uid,
                "b" * 32,
                "cv",
                "Synthetic remote output",
                "fr",
                operation_id=uuid.uuid4(),
            )
            expected.add(doc.id)
        async with sessions() as db:
            exported = await export_documents(db, uid)
            assert not db.in_transaction()
        assert {d["id"] for d in exported["documents"]} == expected
        assert exported["documents_authority"] == "core"
        assert all(
            d["content"] == "Synthetic remote output" for d in exported["documents"]
        )
        assert (
            exported["retained_local_documents"][0]["content"]
            == "Synthetic retained output"
        )
        assert exported["document_deliveries"][0]["operation_id"] == op
        print(
            "SwissJob export + PostgreSQL + real core HTTP: 21 remote, 1 retained, 1 prepared passed"
        )
    finally:
        await engine.dispose()


asyncio.run(run())
