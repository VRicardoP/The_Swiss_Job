"""School observations use the corpus sink; quarantine never leaves a false link."""

import asyncio
import uuid

import pytest
import sqlalchemy as sa

from jobhunt_core.school_ingest import SOURCE_NAME, SCOPE_ID
from jobhunt_core.tests import dbcleanup
from jobhunt_core.tests.test_integration_api_schools import _create, _request, SCOPES
from jobhunt_core.tests.test_integration_api_saved_searches import (
    db,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
    _seed_profile,
    _rows,
    pytestmark,  # noqa: F401  (fixture/marca de pytest: se importa para que la resuelva por nombre)
)


@pytest.fixture()
def school_db(db):  # noqa: F811  (la fixture, no una redefinición)
    yield db
    f, made = db

    async def cleanup():
        async with f() as s:
            await dbcleanup.purge_consumer_graph(s, made["consumers"])
            source = (
                await s.execute(
                    sa.text("SELECT id FROM sources WHERE name=:n"), {"n": SOURCE_NAME}
                )
            ).scalar_one_or_none()
            if source:
                await dbcleanup.purge_source_graph(s, [source], [SCOPE_ID])
            await s.commit()

    asyncio.run(cleanup())


def _observation(**extra):
    ref = uuid.uuid4().hex
    return {
        "publish_missing": True,
        "title": "IT Technician",
        "url": f"https://school.example/jobs/{ref}",
        "content_hash": "a" * 64,
        "dedup_key": ref,
        "role_score": 1,
        "urgency_score": 80,
        "description_snippet": "Technical support",
        **extra,
    }


def test_historical_observation_waits_for_corpus_without_creating_vacancy(school_db):
    f, made = school_db
    token, _, _ = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    path = "/v1/schools/" + monitor["id"] + "/jobs"
    body = _observation(publish_missing=False)
    before = _rows(f, "SELECT count(*) FROM vacancies")
    historical = _request(f, token, path, "POST", body, "historical").json()["item"]
    assert historical["quarantine_reason"] == "awaiting_corpus"
    assert _rows(f, "SELECT count(*) FROM vacancies") == before
    assert _rows(
        f,
        "SELECT count(*) FROM source_listings sl JOIN sources s ON s.id=sl.source_id WHERE s.name=:n",
        n=SOURCE_NAME,
    ) == [(0,)]
    # A new LIVE sighting confirms presence, retaining ID and notification.
    live = _request(f, token, path, "POST", {**body, "publish_missing": True}, "live")
    assert live.status_code == 200, live.text
    assert live.json()["created"] is False
    assert live.json()["item"]["id"] == historical["id"]
    assert live.json()["item"]["vacancy_id"] is not None
    assert live.json()["item"]["quarantine_reason"] is None


def test_same_source_sighting_preserves_canonical_content(school_db):
    f, made = school_db
    token, _, _ = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    path = "/v1/schools/" + monitor["id"] + "/jobs"
    body = _observation(description_snippet="Complete original job description")
    first = _request(f, token, path, "POST", body, "full").json()["item"]
    second = _request(
        f,
        token,
        path,
        "POST",
        {**body, "dedup_key": "later", "description_snippet": "Short snippet"},
        "short",
    ).json()["item"]
    assert first["vacancy_id"] == second["vacancy_id"]
    assert _rows(
        f,
        "SELECT r.content->>'description' FROM vacancies v JOIN offer_revisions r ON r.id=v.current_offer_revision_id WHERE v.id=:v",
        v=uuid.UUID(first["vacancy_id"]),
    ) == [("Complete original job description",)]


def test_link_to_other_source_does_not_leave_extra_active_incarnation(school_db):
    from jobhunt_core.school_ingest import link_observation

    f, made = school_db
    token, _, _ = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    body = _observation()
    first = _request(
        f, token, "/v1/schools/" + monitor["id"] + "/jobs", "POST", body, "initial"
    ).json()["item"]

    async def run():
        async with f() as s:
            foreign = uuid.uuid4()
            await s.execute(
                sa.text("INSERT INTO sources(id,name,tier) VALUES (:id,:name,0)"),
                {"id": foreign, "name": "school-foreign-" + foreign.hex},
            )
            await s.execute(
                sa.text(
                    "UPDATE source_listings SET source_id=:sid WHERE id IN "
                    "(SELECT source_listing_id FROM source_listing_incarnations WHERE vacancy_id=:vid)"
                ),
                {"sid": foreign, "vid": uuid.UUID(first["vacancy_id"])},
            )
            count = await s.scalar(
                sa.text(
                    "SELECT count(*) FROM source_listing_incarnations WHERE vacancy_id=:v"
                ),
                {"v": uuid.UUID(first["vacancy_id"])},
            )
            linked, reason = await link_observation(
                s, monitor, body, publish_missing=True
            )
            assert str(linked) == first["vacancy_id"] and reason is None
            assert (
                await s.scalar(
                    sa.text(
                        "SELECT count(*) FROM source_listing_incarnations WHERE vacancy_id=:v"
                    ),
                    {"v": linked},
                )
                == count
            )
            assert (
                await s.scalar(
                    sa.text(
                        "SELECT count(*) FROM source_listings sl JOIN sources s ON s.id=sl.source_id WHERE s.name=:n"
                    ),
                    {"n": SOURCE_NAME},
                )
                == 0
            )
            await s.rollback()  # Fixture retains its original source graph.

    asyncio.run(run())


def test_school_observation_corpus_idempotence_and_notification(school_db):
    f, made = school_db
    token, _, _ = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    body = _observation()
    path = "/v1/schools/" + monitor["id"] + "/jobs"
    first = _request(f, token, path, "POST", body, "observation")
    assert first.status_code == 201, first.text
    job = first.json()["item"]
    assert job["vacancy_id"] and job["quarantine_reason"] is None
    linked = _rows(
        f,
        "SELECT r.content FROM vacancies v JOIN offer_revisions r ON r.id=v.current_offer_revision_id WHERE v.id=:v",
        v=uuid.UUID(job["vacancy_id"]),
    )
    assert linked[0][0]["title"] == "IT Technician"
    assert (
        _request(f, token, path, "POST", body, "observation").json()["item"]["id"]
        == job["id"]
    )
    repeat = _request(f, token, path, "POST", body, "second-operation")
    assert repeat.status_code == 200 and repeat.json()["created"] is False
    assert (
        len(
            _request(f, token, "/v1/school-jobs?pending=true&min_role=0.5").json()[
                "items"
            ]
        )
        == 1
    )
    get = _request(f, token, "/v1/school-jobs/" + job["id"])
    marked = _request(
        f,
        token,
        "/v1/school-jobs/" + job["id"] + "/notified",
        "POST",
        key="notify",
        etag=get.headers["etag"],
    )
    assert marked.status_code == 200 and marked.json()["notified_at"], marked.text
    assert _request(f, token, "/v1/school-jobs?pending=true").json()["items"] == []
    assert (
        _request(
            f,
            token,
            "/v1/school-jobs/" + job["id"] + "/notified",
            "POST",
            key="notify",
            etag=get.headers["etag"],
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "url,reason",
    [
        (None, "no_url"),
        ("", "no_url"),
        ("https://school.example/careers", "listing_page_url"),
        ("https://school.example/" + "é" * 1030, "url_limit"),
        ("javascript:x", "invalid_url"),
    ],
)
def test_school_quarantine_is_visible_without_false_corpus(school_db, url, reason):
    f, made = school_db
    token, _, _ = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token, jobs_page_url="https://school.example/careers").json()
    result = _request(
        f,
        token,
        "/v1/schools/" + monitor["id"] + "/jobs",
        "POST",
        _observation(url=url),
        "quarantine",
    )
    assert result.status_code == 201, result.text
    job = result.json()["item"]
    assert job["vacancy_id"] is None and job["quarantine_reason"] == reason
    assert len(_request(f, token, "/v1/school-jobs").json()["items"]) == 1
    assert _rows(
        f,
        "SELECT count(*) FROM source_listings sl JOIN sources s ON s.id=sl.source_id WHERE s.name=:n",
        n=SOURCE_NAME,
    ) == [(0,)]


def test_school_reused_url_different_title_is_not_a_false_link(school_db):
    f, made = school_db
    token, _, _ = _seed_profile(f, made, SCOPES)
    monitor = _create(f, token).json()
    path = "/v1/schools/" + monitor["id"] + "/jobs"
    body = _observation()
    first = _request(f, token, path, "POST", body, "first").json()["item"]
    second = _request(
        f,
        token,
        path,
        "POST",
        {**body, "dedup_key": "another-role", "title": "Head Teacher"},
        "second",
    )
    assert second.status_code == 201, second.text
    assert second.json()["item"]["quarantine_reason"] == "url_identity_conflict"
    assert second.json()["item"]["vacancy_id"] is None
    assert _rows(
        f,
        "SELECT count(*) FROM source_listings sl JOIN sources s ON s.id=sl.source_id WHERE s.name=:n",
        n=SOURCE_NAME,
    ) == [(1,)]
    content = _rows(
        f,
        "SELECT r.content->>'title' FROM vacancies v JOIN offer_revisions r ON r.id=v.current_offer_revision_id WHERE v.id=:v",
        v=uuid.UUID(first["vacancy_id"]),
    )
    assert content == [("IT Technician",)]
