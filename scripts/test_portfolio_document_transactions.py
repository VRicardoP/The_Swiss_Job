"""Opt-in real PostgreSQL check of the Portfolio generation transaction.

Requires -p jobhunt_core.tests.conftest and /portfolio mounted read-only. Creates
only four synthetic tables in a private schema of the plugin's disposable DB.
No real LLM, credentials, users, NAS or production database are involved.
"""

import os
import subprocess
import sys

from jobhunt_core.tests.test_integration_api_saved_searches import db  # noqa: F401  (fixture de pytest: se importa para que la resuelva por nombre)


_PROBE = r"""
import asyncio, inspect, os, sys, uuid
from types import SimpleNamespace
if os.environ.get("DOCUMENT_TEST_EXTRA_PACKAGES"):
    sys.path.append(os.environ["DOCUMENT_TEST_EXTRA_PACKAGES"])
from fastapi import HTTPException
from sqlalchemy import select, text, func, delete
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from database import Base
from models.user import User
from models.job_application import JobApplication
from models.generated_document import GeneratedDocument
from models.jobhunt_routing import JobhuntRouting
from schemas.generated_document import GenerateDocumentRequest
from routers.cv_generation import generate_documents

url = os.environ["DOCUMENT_TRANSACTION_TEST_DSN"]
assert make_url(url).database == os.environ["DOCUMENT_TRANSACTION_TEST_DATABASE"]
assert make_url(url).database.startswith("jobhunt_suite_")
schema = "doc_tx_" + uuid.uuid4().hex
engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
sessions = async_sessionmaker(engine, expire_on_commit=False)
generate = inspect.unwrap(generate_documents)

class Profiles:
    async def get_cv_text(self, db): return "Synthetic CV"
    async def get_structured_data(self, db, language): return None

async def run():
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                User.__table__, JobApplication.__table__, GeneratedDocument.__table__,
                JobhuntRouting.__table__,
            ]))
        async with sessions() as s:
            user = User(username="synthetic", hashed_password="not-a-password")
            s.add(user)
            await s.commit()
            uid = user.id

        for case in ("pair", "letter_failure", "deleted"):
            async with sessions() as s:
                application = JobApplication(user_id=uid, title="Engineer", company="Synthetic")
                s.add(application)
                await s.commit()
                aid = application.id
                calls = []

                class LLM:
                    async def generate_cv(self, **kwargs):
                        assert not s.in_transaction(), "CV held a database transaction"
                        calls.append("cv")
                        if case == "deleted":
                            async with sessions() as other:
                                await other.execute(delete(JobApplication).where(JobApplication.id == aid))
                                await other.commit()
                        return {"content": {"name": "Synthetic"}, "generation_time_ms": 1}

                    async def generate_cover_letter(self, **kwargs):
                        assert not s.in_transaction() and not s.new, "letter held a transaction/pending CV"
                        calls.append("letter")
                        if case == "letter_failure":
                            raise RuntimeError("synthetic letter failure")
                        return {"content": {"body": "Synthetic"}, "generation_time_ms": 1}

                if case == "pair":
                    original_flush = s.flush
                    async def flush(*args, **kwargs):
                        # The short persistence phase really locks the parent in PG.
                        async with sessions() as other:
                            try:
                                await other.execute(select(JobApplication.id).where(
                                    JobApplication.id == aid).with_for_update(nowait=True))
                            except DBAPIError as exc:
                                assert getattr(exc.orig, "sqlstate", None) == "55P03"
                                await other.rollback()
                            else:
                                raise AssertionError("parent was not locked before saving")
                        return await original_flush(*args, **kwargs)
                    s.flush = flush
                try:
                    response = await generate(
                        request=SimpleNamespace(), current_user=SimpleNamespace(id=uid),
                        data=GenerateDocumentRequest(application_id=aid, include_cover_letter=case != "deleted"),
                        db=s, cv_service=LLM(), cv_profile_svc=Profiles(),
                    )
                    assert case == "pair"
                    assert response.cv_document and response.cover_letter_document
                except RuntimeError as exc:
                    assert case == "letter_failure" and str(exc) == "synthetic letter failure"
                except HTTPException as exc:
                    assert case == "deleted" and exc.status_code == 404

            async with sessions() as check:
                n = await check.scalar(select(func.count()).select_from(GeneratedDocument).where(
                    GeneratedDocument.application_id == aid))
                assert n == (2 if case == "pair" else 0)
                if case == "pair":
                    await check.execute(delete(JobApplication).where(JobApplication.id == aid))
                    await check.commit()
                    documents = (await check.execute(select(GeneratedDocument))).scalars().all()
                    assert len(documents) == 2, "application deletion removed documents"
                    assert all(d.application_id == aid for d in documents)
                    assert all(d.application_snapshot["title"] == "Engineer" for d in documents)
                    await check.execute(delete(GeneratedDocument))
                    await check.commit()
        print("PG: no transaction during LLM; atomic pair; parent lock; retained documents passed")
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()

asyncio.run(run())
"""


def test_portfolio_document_transactions_on_postgres(db):  # noqa: F811  (la fixture, no una redefinición)
    from sqlalchemy.engine import make_url
    from jobhunt_core.config import settings
    from jobhunt_core.tests.conftest import _suite

    assert os.path.isfile("/portfolio/routers/cv_generation.py")
    assert _suite.get("dbname") == make_url(settings.CORE_DATABASE_URL).database
    assert _suite["dbname"].startswith("jobhunt_suite_")
    test_url = (
        make_url(_suite["admin_url"])
        .set(
            drivername="postgresql+asyncpg",
            database=_suite["dbname"],
        )
        .render_as_string(hide_password=False)
    )
    env = {
        **os.environ,
        "PYTHONPATH": "/portfolio",
        "DOCUMENT_TRANSACTION_TEST_DSN": test_url,
        "DOCUMENT_TRANSACTION_TEST_DATABASE": _suite["dbname"],
        "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/unused",
        "DATABASE_URL_ASYNC": "postgresql+asyncpg://test:test@127.0.0.1:1/unused",
        "ADMIN_EMAIL": "transaction-test@example.com",
        "ADMIN_PASSWORD": "synthetic-only",
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd="/tmp",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (result.stdout + result.stderr).replace(
        test_url, "<test-database>"
    )
