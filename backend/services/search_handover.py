"""Read-only state capture for the frozen saved-search handover.

Runs with the legacy search/corpus producers drained. Owns no commit and never
marks Redis keys or sends notifications. The caller stores the result privately
and keeps the writers frozen until apply/revert. Revalidation must compare the
source fingerprints and marker values before switching authority.
"""

from datetime import datetime

from sqlalchemy import select, text

from models.job import Job
from models.jobhunt_profile_map import JobhuntProfileMap
from models.saved_search import SavedSearch
from tasks.search_tasks import _SENT_PREFIX, candidate_query


class SearchCaptureError(ValueError):
    pass

# Public offer content needed to recover an absent pending offer through the
# normal sink. No embeddings, user fields or credentials enter this bundle.
OFFER_FIELDS = (
    "hash", "source", "url", "apply_url", "is_active", "duplicate_of",
    "first_seen_at", "title", "company", "description", "tags", "location",
    "canton", "language", "seniority", "contract_type", "remote",
    "salary_min_chf", "salary_max_chf", "salary_original", "salary_currency", "salary_period",
)


# Only columns used by selection/identity and the imported content. Excludes vectors, scrape
# refresh timestamps and other unrelated writes, but includes the actual FTS
# document used by the legacy query. PostgreSQL emits a small digest, not PII.
JOBS_FINGERPRINT_SQL = """
    SELECT count(*) AS n, md5(coalesce(string_agg(md5(jsonb_build_array(
        hash,url,source,is_active,duplicate_of,canton,remote,language,
        seniority,contract_type,salary_min_chf,salary_max_chf,
        first_seen_at,search_vector::text,title,company,description,location,
        tags,apply_url,salary_original,salary_currency,salary_period
    )::text), '' ORDER BY hash COLLATE "C"), '')) AS digest FROM public.jobs
"""


async def capture(db, redis, settings, *, captured_at: datetime):
    if not settings.SAVED_SEARCH_WRITES_FROZEN:
        raise SearchCaptureError("saved-search writers must be frozen")
    if captured_at.tzinfo is None:
        raise SearchCaptureError("capture timestamp must be aware")
    await db.execute(text("SET LOCAL lock_timeout='5s'"))
    await db.execute(text("SET LOCAL statement_timeout='60s'"))
    await db.execute(text("SET LOCAL timezone='UTC'"))
    # Blocks old in-flight mutations as well as future ones; readers stay live.
    # The operational drain precedes these locks (they do not cancel fetches).
    await db.execute(text("LOCK TABLE public.jobhunt_routing, public.jobhunt_profile_map, "
                          "public.saved_searches, public.jobs IN SHARE MODE"))
    modes = (await db.execute(text("SELECT mode FROM public.jobhunt_routing "
        "WHERE consumer_id='swissjob' AND capability='saved_searches'"))).scalars().all()
    if any(mode not in {"local", "shadow", "core_read"} for mode in modes):
        raise SearchCaptureError("searches no longer have local authority")
    searches = (await db.execute(select(SavedSearch).order_by(SavedSearch.id))).scalars().all()
    owners = {search.user_id for search in searches}
    links = dict((await db.execute(select(JobhuntProfileMap.user_id, JobhuntProfileMap.core_profile_id)
        .where(JobhuntProfileMap.user_id.in_(owners)))).all())
    if set(links) != owners or len(set(links.values())) != len(links):
        raise SearchCaptureError("missing or ambiguous owner binding")
    rows, candidates, markers = [], {}, {}
    for search in searches:
        row = {column.name: getattr(search, column.name) for column in SavedSearch.__table__.columns}
        row["notify_frequency"] = search.notify_frequency.value
        rows.append(row)
        result = (await db.execute(candidate_query(search, settings, captured_at)
            .with_only_columns(*(getattr(Job, key) for key in OFFER_FIELDS))
            .order_by(Job.hash))).mappings().all()
        sid = str(search.id)
        candidates[sid] = [dict(item) for item in result]
        keys = [f"{_SENT_PREFIX}:{sid}:{item['hash']}" for item in result]
        # Unlike normal legacy execution, Redis failure is NOT fail-open here.
        # An incomplete migration snapshot must never classify everything new.
        values = await redis.mget(keys) if keys else []
        if len(values) != len(keys) or any(value not in (None, b"1", "1") for value in values):
            raise SearchCaptureError("invalid sent-marker response")
        markers[sid] = {item["hash"]: value is not None for item, value in zip(result, values)}
    fingerprint = dict((await db.execute(text(JOBS_FINGERPRINT_SQL))).mappings().one())
    return {"version": 1, "captured_at": captured_at, "rows": rows,
            "bindings": {str(u): str(p) for u,p in links.items()},
            "candidates": candidates, "sent": markers, "jobs_fingerprint": fingerprint,
            "settings": {key: getattr(settings, key) for key in (
                "NOTIFY_WATERMARK_LAG_MINUTES", "NOTIFY_SENT_MARKER_TTL_DAYS",
                "SAVED_SEARCH_INITIAL_LOOKBACK_DAYS")}}
