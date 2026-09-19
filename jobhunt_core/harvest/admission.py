"""ADR-10 admission for native sources: window for NEW items, refresh known ones.

Run inside the fenced persistence transaction. The optional scope parameter is
explicit, semantic, and never forwarded upstream. Its omission preserves the
pre-cutover behaviour. No date is invented from ingestion time, and raw payloads
reach the sink unchanged. Counts describe this RUN, not unique discarded offers.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from jobhunt_core.harvest.provider import ADMISSION_WINDOW_PARAM, ProviderConfigError
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
}


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
        raise ProviderConfigError("admission_window_days exceeds datetime range") from None
    return window


async def admit_listings(session, source, result, window, *, now=None):
    """One batched lookup, only for out-of-window items; no fuzzy URL recognition.

    Legacy identities are mutable MD5s, so at handover recognize their EXACT raw
    URL in the SAME source as well. Never use a normalized URL here: fragments
    may identify different positions. Native upstream IDs remain authoritative.
    """
    if window is None:
        return result
    cutoff = (now or datetime.now(timezone.utc)) - window
    dates = [parse_published_at(row.payload.get(DATE_FIELDS[source])) for row in result.listings]
    outside = [row for row, date in zip(result.listings, dates) if date is None or date < cutoff]
    known_ids, known_urls = set(), set()
    if outside:
        rows = (await session.execute(sa.text("""
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
        """), {"source": source, "legacy": "legacy:" + source,
               "ids": list({row.external_id for row in outside}),
               "urls": list({row.url for row in outside})})).all()
        known_ids = {row.external_id for row in rows if row.external_id is not None}
        known_urls = {row.url for row in rows if row.url is not None}
    counts = {"accepted": 0, "refreshed": 0, "stale": 0, "missing_date": 0,
              "date_present": sum(date is not None for date in dates)}
    accepted = []
    for row, date in zip(result.listings, dates):
        if date is not None and date >= cutoff:
            counts["accepted"] += 1
            accepted.append(row)
        elif row.external_id in known_ids or row.url in known_urls:
            counts["refreshed"] += 1
            accepted.append(row)
        else:
            counts["missing_date" if date is None else "stale"] += 1
    # Refresh known offers even if the portal date contract breaks, but don't
    # call this a healthy complete harvest or reset its failure counter.
    missing_dates = bool(result.listings) and not counts["date_present"]
    return replace(result, listings=tuple(accepted),
                   next_cursor={**result.next_cursor, ADMISSION_CURSOR_KEY: counts},
                   complete=result.complete and not missing_dates,
                   error=result.error or ("admission_missing_dates" if missing_dates else None))
