"""ADR-10 admission for native sources: window for NEW items, refresh known ones.

Run inside the fenced persistence transaction. The optional scope parameter is
explicit, semantic, and never forwarded upstream. Its omission preserves the
pre-cutover behaviour. No date is invented from ingestion time, and raw payloads
reach the sink unchanged. Counts describe this RUN, not unique discarded offers.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from jobhunt_core.harvest.provider import (
    ADMISSION_WINDOW_PARAM,
    LEGACY_TITLE_FILTER_PARAM,
    ProviderConfigError,
)
from jobhunt_core.harvest.identity import extract_identity
from jobhunt_core.harvest.legacy_title_policy import excluded_title
from jobhunt_core.harvest.published import parse_published_at

ADMISSION_CURSOR_KEY = "_admission"
DATE_FIELDS = {
    "arbeitnow": "created_at",
    "remotive": "publication_date",
    "workingnomads": "pub_date",
    "jobicy": "pubDate",
    "weworkremotely": "pubDate",
    "euremotejobs": "pubDate",
    "jobspresso": "pubDate",
    "globaljobs": "pubDate",
    "zebis": "pubDate",
    "ostjob": "dateFirstPublished",
    "zentraljob": "dateFirstPublished",
    "publicjobs": "publicFrom",
    "nav_arbeidsplassen": ("_source", "published"),
    "thehub": "createdAt",
    "jobgether": "createdAt",
}


def publication_date(source, payload):
    """Read only the declared portal date, including NAV's raw ES envelope."""
    path = DATE_FIELDS[source]
    path = (path,) if isinstance(path, str) else path
    value = payload
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return parse_published_at(value)


def admission_window(source, params):
    """Validate BEFORE fetch; unsupported/invalid policy is configuration error."""
    if ADMISSION_WINDOW_PARAM not in params:
        return None
    days = params[ADMISSION_WINDOW_PARAM]
    if type(days) is not int or days < 1 or source not in DATE_FIELDS:
        raise ProviderConfigError("invalid admission_window_days or unsupported source")
    try:
        window = timedelta(days=days)
        datetime.now(timezone.utc) - window
    except (OverflowError, ValueError):
        raise ProviderConfigError(
            "admission_window_days exceeds datetime range"
        ) from None
    return window


def title_filter_enabled(source, params):
    """Explicit migration policy; never impose SwissJob's filter on other scopes."""
    value = params.get(LEGACY_TITLE_FILTER_PARAM, False)
    if type(value) is not bool:
        raise ProviderConfigError("legacy_title_filter must be a boolean")
    if value:
        from jobhunt_core.harvest.registry import ensure_handler

        if source not in DATE_FIELDS or not ensure_handler(source):
            raise ProviderConfigError(
                "legacy_title_filter requires a supported native source"
            )
    return value


async def admit_listings(
    session, source, result, window, *, now=None, filter_titles=False
):
    """One batched lookup for rejected candidates; known entries keep refreshing.

    Policy is opt-in per scope. Historical recognition remains source-scoped and
    exact-URL (never normalized URL), with native upstream ids authoritative.
    """
    if window is None and not filter_titles:
        return result
    cutoff = (
        (now or datetime.now(timezone.utc)) - window if window is not None else None
    )
    dates = (
        [publication_date(source, row.payload) for row in result.listings]
        if window is not None
        else [None] * len(result.listings)
    )
    blocked_titles = [
        filter_titles and excluded_title(extract_identity(source, row.payload)[0])
        for row in result.listings
    ]
    outside = [
        row
        for row, date, blocked in zip(result.listings, dates, blocked_titles)
        if blocked or (window is not None and (date is None or date < cutoff))
    ]
    known_ids, known_urls = set(), set()
    if outside:
        rows = (
            await session.execute(
                sa.text("""
            SELECT sl.external_id, NULL::text AS url
              FROM source_listings sl JOIN sources s ON s.id=sl.source_id
             WHERE s.name=:source AND sl.external_id=ANY(CAST(:ids AS text[]))
            UNION ALL
            SELECT NULL::text, i.url
              FROM source_listing_incarnations i
              JOIN source_listings sl ON sl.id=i.source_listing_id
              JOIN sources s ON s.id=sl.source_id
             WHERE s.name IN (:source, :legacy)
               AND i.url=ANY(CAST(:urls AS text[]))
        """),
                {
                    "source": source,
                    "legacy": "legacy:" + source,
                    "ids": list({row.external_id for row in outside}),
                    "urls": list({row.url for row in outside}),
                },
            )
        ).all()
        known_ids = {row.external_id for row in rows if row.external_id is not None}
        known_urls = {row.url for row in rows if row.url is not None}
    counts = {
        "accepted": 0,
        "refreshed": 0,
        "stale": 0,
        "missing_date": 0,
        "date_present": 0,
    }
    if filter_titles:
        counts["title_excluded"] = 0
    accepted, considered = [], 0
    for row, date, blocked in zip(result.listings, dates, blocked_titles):
        known = row.external_id in known_ids or row.url in known_urls
        if blocked and not known:
            counts["title_excluded"] += 1
            continue
        considered += 1
        counts["date_present"] += date is not None
        if window is None or (date is not None and date >= cutoff):
            counts["accepted"] += 1
            accepted.append(row)
        elif known:
            counts["refreshed"] += 1
            accepted.append(row)
        else:
            counts["missing_date" if date is None else "stale"] += 1
    # The old pipeline filtered new titles BEFORE assessing dates. A deliberately
    # excluded batch is not a broken date contract; retained refreshes still are.
    missing_dates = window is not None and considered > 0 and not counts["date_present"]
    return replace(
        result,
        listings=tuple(accepted),
        next_cursor={**result.next_cursor, ADMISSION_CURSOR_KEY: counts},
        complete=result.complete and not missing_dates,
        error=result.error or ("admission_missing_dates" if missing_dates else None),
    )
