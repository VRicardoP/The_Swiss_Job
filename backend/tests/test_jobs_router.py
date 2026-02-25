"""Tests for jobs router — search, detail, stats, sources."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from models.job import Job


def _job_data(**overrides):
    """Factory for Job constructor kwargs."""
    base = {
        "hash": "a" * 32,
        "source": "test_source",
        "title": "Python Developer",
        "company": "Acme Corp",
        "url": "https://example.com/job/1",
        "location": "Zurich, ZH",
        "canton": "ZH",
        "description": "Build Python APIs with FastAPI and PostgreSQL",
        "description_snippet": "Build Python APIs...",
        "remote": False,
        "tags": ["python", "fastapi"],
        "language": "en",
        "seniority": "mid",
        "contract_type": "full_time",
        "salary_min_chf": 80000,
        "salary_max_chf": 120000,
        "is_active": True,
    }
    base.update(overrides)
    return base


async def _insert_job(db: AsyncSession, **overrides) -> str:
    """Insert a job via ORM and return its hash."""
    data = _job_data(**overrides)
    valid = {c.key for c in Job.__table__.columns}
    job = Job(**{k: v for k, v in data.items() if k in valid})
    db.add(job)
    await db.commit()
    return data["hash"]


# ---------------------------------------------------------------------------
# Search endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestSearchJobs:
    async def test_search_empty_db(self, client: AsyncClient):
        resp = await client.get("/api/v1/jobs/search")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["data"] == []
        assert data["has_more"] is False

    async def test_search_returns_jobs(self, client: AsyncClient, db_session):
        for i in range(3):
            h = (f"j{i}" + "0" * 30)[:32]
            await _insert_job(db_session, hash=h, url=f"https://example.com/j/{i}")
        resp = await client.get("/api/v1/jobs/search")
        assert resp.status_code == 200
        assert resp.json()["total"] == 3
        assert len(resp.json()["data"]) == 3

    async def test_search_fulltext_q(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("py" + "0" * 30)[:32],
            title="Python Developer",
            description="Build Python APIs",
            url="https://example.com/py",
        )
        await _insert_job(
            db_session,
            hash=("jv" + "0" * 30)[:32],
            title="Java Developer",
            description="Build Java microservices",
            url="https://example.com/jv",
        )
        resp = await client.get("/api/v1/jobs/search", params={"q": "Python"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["data"][0]["title"] == "Python Developer"

    async def test_search_filter_source(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("s1" + "0" * 30)[:32],
            source="jobicy",
            url="https://example.com/s1",
        )
        await _insert_job(
            db_session,
            hash=("s2" + "0" * 30)[:32],
            source="adzuna",
            url="https://example.com/s2",
        )
        resp = await client.get("/api/v1/jobs/search", params={"source": "jobicy"})
        assert resp.json()["total"] == 1
        assert resp.json()["data"][0]["source"] == "jobicy"

    async def test_search_filter_source_comma(self, client: AsyncClient, db_session):
        for i, src in enumerate(["jobicy", "adzuna", "remotive"]):
            h = (f"sc{i}" + "0" * 30)[:32]
            await _insert_job(
                db_session,
                hash=h,
                source=src,
                url=f"https://example.com/sc/{i}",
            )
        resp = await client.get(
            "/api/v1/jobs/search", params={"source": "jobicy,adzuna"}
        )
        assert resp.json()["total"] == 2

    async def test_search_filter_canton(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("zh" + "0" * 30)[:32],
            canton="ZH",
            url="https://example.com/zh",
        )
        await _insert_job(
            db_session,
            hash=("be" + "0" * 30)[:32],
            canton="BE",
            url="https://example.com/be",
        )
        resp = await client.get("/api/v1/jobs/search", params={"canton": "ZH"})
        assert resp.json()["total"] == 1
        assert resp.json()["data"][0]["canton"] == "ZH"

    async def test_search_filter_canton_comma(self, client: AsyncClient, db_session):
        for i, ct in enumerate(["ZH", "BE", "GE"]):
            h = (f"ct{i}" + "0" * 30)[:32]
            await _insert_job(
                db_session,
                hash=h,
                canton=ct,
                url=f"https://example.com/ct/{i}",
            )
        resp = await client.get("/api/v1/jobs/search", params={"canton": "ZH,BE"})
        assert resp.json()["total"] == 2

    async def test_search_filter_remote_only(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("rm" + "0" * 30)[:32],
            remote=True,
            url="https://example.com/rm",
        )
        await _insert_job(
            db_session,
            hash=("of" + "0" * 30)[:32],
            remote=False,
            url="https://example.com/of",
        )
        resp = await client.get("/api/v1/jobs/search", params={"remote_only": "true"})
        assert resp.json()["total"] == 1

    async def test_search_filter_language(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("de" + "0" * 30)[:32],
            language="de",
            url="https://example.com/de",
        )
        await _insert_job(
            db_session,
            hash=("en" + "0" * 30)[:32],
            language="en",
            url="https://example.com/en",
        )
        resp = await client.get("/api/v1/jobs/search", params={"language": "de"})
        assert resp.json()["total"] == 1

    async def test_search_filter_seniority(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("sr" + "0" * 30)[:32],
            seniority="senior",
            url="https://example.com/sr",
        )
        await _insert_job(
            db_session,
            hash=("jr" + "0" * 30)[:32],
            seniority="junior",
            url="https://example.com/jr",
        )
        resp = await client.get("/api/v1/jobs/search", params={"seniority": "senior"})
        assert resp.json()["total"] == 1

    async def test_search_filter_contract_type(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("ft" + "0" * 30)[:32],
            contract_type="full_time",
            url="https://example.com/ft",
        )
        await _insert_job(
            db_session,
            hash=("pt" + "0" * 30)[:32],
            contract_type="part_time",
            url="https://example.com/pt",
        )
        resp = await client.get(
            "/api/v1/jobs/search", params={"contract_type": "part_time"}
        )
        assert resp.json()["total"] == 1

    async def test_search_filter_salary_min(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("hi" + "0" * 30)[:32],
            salary_min_chf=100000,
            salary_max_chf=150000,
            url="https://example.com/hi",
        )
        await _insert_job(
            db_session,
            hash=("lo" + "0" * 30)[:32],
            salary_min_chf=40000,
            salary_max_chf=60000,
            url="https://example.com/lo",
        )
        resp = await client.get("/api/v1/jobs/search", params={"salary_min": 80000})
        # Only the high-salary job has salary_max_chf >= 80000
        assert resp.json()["total"] == 1

    async def test_search_filter_salary_max(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("hi" + "0" * 30)[:32],
            salary_min_chf=100000,
            salary_max_chf=150000,
            url="https://example.com/hi",
        )
        await _insert_job(
            db_session,
            hash=("lo" + "0" * 30)[:32],
            salary_min_chf=40000,
            salary_max_chf=60000,
            url="https://example.com/lo",
        )
        resp = await client.get("/api/v1/jobs/search", params={"salary_max": 70000})
        # Only the low-salary job has salary_min_chf <= 70000
        assert resp.json()["total"] == 1

    async def test_search_excludes_inactive(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("ac" + "0" * 30)[:32],
            is_active=True,
            url="https://example.com/ac",
        )
        await _insert_job(
            db_session,
            hash=("ia" + "0" * 30)[:32],
            is_active=False,
            url="https://example.com/ia",
        )
        resp = await client.get("/api/v1/jobs/search")
        assert resp.json()["total"] == 1

    async def test_search_excludes_duplicates(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("or" + "0" * 30)[:32],
            url="https://example.com/or",
        )
        await _insert_job(
            db_session,
            hash=("dp" + "0" * 30)[:32],
            duplicate_of=("or" + "0" * 30)[:32],
            url="https://example.com/dp",
        )
        resp = await client.get("/api/v1/jobs/search")
        assert resp.json()["total"] == 1

    async def test_search_pagination(self, client: AsyncClient, db_session):
        for i in range(5):
            h = (f"pg{i}" + "0" * 30)[:32]
            await _insert_job(db_session, hash=h, url=f"https://example.com/pg/{i}")
        # Page 1
        resp = await client.get("/api/v1/jobs/search", params={"limit": 2, "offset": 0})
        data = resp.json()
        assert data["total"] == 5
        assert len(data["data"]) == 2
        assert data["has_more"] is True

        # Last page
        resp = await client.get("/api/v1/jobs/search", params={"limit": 2, "offset": 4})
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["has_more"] is False

    async def test_search_sort_newest(self, client: AsyncClient, db_session):
        """Default sort is newest (last_seen_at DESC)."""
        resp = await client.get("/api/v1/jobs/search", params={"sort": "newest"})
        assert resp.status_code == 200

    async def test_search_sort_salary(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("lo" + "0" * 30)[:32],
            salary_max_chf=60000,
            url="https://example.com/lo",
        )
        await _insert_job(
            db_session,
            hash=("hi" + "0" * 30)[:32],
            salary_max_chf=150000,
            url="https://example.com/hi",
        )
        await _insert_job(
            db_session,
            hash=("no" + "0" * 30)[:32],
            salary_max_chf=None,
            salary_min_chf=None,
            url="https://example.com/no",
        )
        resp = await client.get("/api/v1/jobs/search", params={"sort": "salary"})
        data = resp.json()["data"]
        # Highest salary first, nulls last
        assert data[0]["salary_max_chf"] == 150000
        assert data[1]["salary_max_chf"] == 60000
        assert data[2]["salary_max_chf"] is None

    async def test_search_limit_max_100(self, client: AsyncClient):
        resp = await client.get("/api/v1/jobs/search", params={"limit": 200})
        assert resp.status_code == 422

    async def test_search_combined_filters(self, client: AsyncClient, db_session):
        # Only this job matches all filters
        await _insert_job(
            db_session,
            hash=("mt" + "0" * 30)[:32],
            canton="ZH",
            remote=True,
            seniority="senior",
            url="https://example.com/mt",
        )
        await _insert_job(
            db_session,
            hash=("nm" + "0" * 30)[:32],
            canton="BE",
            remote=False,
            seniority="junior",
            url="https://example.com/nm",
        )
        resp = await client.get(
            "/api/v1/jobs/search",
            params={
                "canton": "ZH",
                "remote_only": "true",
                "seniority": "senior",
            },
        )
        assert resp.json()["total"] == 1

    async def test_search_response_shape(self, client: AsyncClient, db_session):
        await _insert_job(db_session)
        resp = await client.get("/api/v1/jobs/search")
        data = resp.json()
        assert "data" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert "has_more" in data
        job = data["data"][0]
        assert "hash" in job
        assert "title" in job
        assert "company" in job
        assert "source" in job
        assert "description_snippet" in job
        assert "language" in job
        assert "seniority" in job
        assert "contract_type" in job


# ---------------------------------------------------------------------------
# Job detail endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestJobDetail:
    async def test_get_job_found(self, client: AsyncClient, db_session):
        h = await _insert_job(db_session)
        resp = await client.get(f"/api/v1/jobs/{h}")
        assert resp.status_code == 200
        assert resp.json()["hash"] == h
        assert resp.json()["title"] == "Python Developer"

    async def test_get_job_not_found(self, client: AsyncClient):
        resp = await client.get("/api/v1/jobs/nonexistent_hash_1234567890ab")
        assert resp.status_code == 404

    async def test_get_job_inactive_returns_404(self, client: AsyncClient, db_session):
        h = await _insert_job(db_session, is_active=False)
        resp = await client.get(f"/api/v1/jobs/{h}")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Stats endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestJobStats:
    async def test_stats_empty_db(self, client: AsyncClient):
        resp = await client.get("/api/v1/jobs/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_jobs"] == 0
        assert data["by_source"] == {}

    async def test_stats_counts(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("s1" + "0" * 30)[:32],
            source="jobicy",
            canton="ZH",
            language="en",
            url="https://example.com/s1",
        )
        await _insert_job(
            db_session,
            hash=("s2" + "0" * 30)[:32],
            source="jobicy",
            canton="BE",
            language="de",
            url="https://example.com/s2",
        )
        await _insert_job(
            db_session,
            hash=("s3" + "0" * 30)[:32],
            source="adzuna",
            canton="ZH",
            language="en",
            url="https://example.com/s3",
        )
        resp = await client.get("/api/v1/jobs/stats")
        data = resp.json()
        assert data["total_jobs"] == 3
        assert data["by_source"]["jobicy"] == 2
        assert data["by_source"]["adzuna"] == 1
        assert data["by_canton"]["ZH"] == 2
        assert data["by_language"]["en"] == 2

    async def test_stats_salary(self, client: AsyncClient, db_session):
        await _insert_job(
            db_session,
            hash=("sa" + "0" * 30)[:32],
            salary_min_chf=80000,
            salary_max_chf=100000,
            url="https://example.com/sa",
        )
        await _insert_job(
            db_session,
            hash=("sb" + "0" * 30)[:32],
            salary_min_chf=120000,
            salary_max_chf=160000,
            url="https://example.com/sb",
        )
        resp = await client.get("/api/v1/jobs/stats")
        sal = resp.json()["salary_stats"]
        assert sal["min"] == 80000
        assert sal["max"] == 160000
        assert sal["mean"] is not None


# ---------------------------------------------------------------------------
# Sources endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestJobSources:
    async def test_sources_empty_db(self, client: AsyncClient):
        resp = await client.get("/api/v1/jobs/sources")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_sources_returns_counts(self, client: AsyncClient, db_session):
        for i in range(3):
            h = (f"sr{i}" + "0" * 30)[:32]
            await _insert_job(
                db_session,
                hash=h,
                source="jobicy",
                url=f"https://example.com/sr/{i}",
            )
        await _insert_job(
            db_session,
            hash=("az" + "0" * 30)[:32],
            source="adzuna",
            url="https://example.com/az",
        )
        resp = await client.get("/api/v1/jobs/sources")
        data = resp.json()
        assert len(data) == 2
        # Ordered by count DESC
        assert data[0]["name"] == "jobicy"
        assert data[0]["count"] == 3
        assert data[1]["name"] == "adzuna"
        assert data[1]["count"] == 1
        assert "last_seen" in data[0]
