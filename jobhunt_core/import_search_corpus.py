"""One-shot recovery of public legacy offers owed by frozen saved searches.

Not a new producer. Uses the normal legacy payload/sink under the projector's
lock; ONE source per transaction. Existing slots are never overwritten or
reopened. The caller revalidates the frozen source and commits one source at a
time, saving the returned provenance privately. A search revert never deletes
this valid shared corpus. No scheduling, alerts or matching are triggered here.
"""

import sqlalchemy as sa

from jobhunt_core.harvest.sink import RawListingSink
from jobhunt_core.harvest.types import RawListing
from jobhunt_core.import_portfolio_provenance import begin_exact_capture, captured_provenance
from jobhunt_core.import_swissjob_durables import resolve_vacancies_by_incarnation_urls
from jobhunt_core.import_swissjob_searches import SearchMigrationError
from jobhunt_core.shadow.projector import JOB_PAYLOAD_MAP, _PROJECTOR_LOCK, _ensure_legacy_source


async def seed_missing_offers(session, source, rows):
    """Fill absent offers only; reject incomplete/quarantined/conflicting input."""
    if not isinstance(source, str) or not source or source.startswith("legacy:"):
        raise SearchMigrationError("invalid original source")
    ids = [row["hash"] for row in rows]
    if len(set(ids)) != len(ids):
        raise SearchMigrationError("repeated offer identity")
    required = {"hash", "source", "url", "apply_url", "is_active", "duplicate_of", *JOB_PAYLOAD_MAP}
    if any(not required <= row.keys() or row["source"] != source or row["is_active"] is not True
           or row["duplicate_of"] is not None for row in rows):
        raise SearchMigrationError("incomplete or ineligible original offer")
    async with session.begin_nested():
        await session.execute(sa.text("SET LOCAL lock_timeout='5s'"))
        await session.execute(sa.text("SET LOCAL statement_timeout='60s'"))
        await session.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k,0))"), {"k": _PROJECTOR_LOCK})
        mapping = await resolve_vacancies_by_incarnation_urls(session, [row["url"] for row in rows])
        vacancies = {vid for hits in mapping.values() for vid in hits}
        presentable = set((await session.execute(sa.text(
            "SELECT id FROM vacancies WHERE id=ANY(:ids) AND archived_at IS NULL "
            "AND merged_into IS NULL AND current_offer_revision_id IS NOT NULL"
        ), {"ids": list(vacancies)})).scalars()) if vacancies else set()
        missing = []
        for row in rows:
            hits = set(mapping[row["url"]]) & presentable
            if len(hits) > 1:
                raise SearchMigrationError("ambiguous existing offer")
            if not hits:
                missing.append(row)
        if not missing:
            return {"resolved": len(rows), "inserted_listings": 0, "provenance": {}}
        name = "legacy:" + source
        existing = await session.scalar(sa.text(
            "SELECT count(*) FROM source_listings sl JOIN sources s ON s.id=sl.source_id "
            "WHERE s.name=:name AND sl.external_id=ANY(:ids)"
        ), {"name": name, "ids": [row["hash"] for row in missing]})
        if existing:
            raise SearchMigrationError("existing slot requires reconciliation, not missing-offer import")
        await begin_exact_capture(session)
        source_id, scope_id = await _ensure_legacy_source(session, name)
        listings = tuple(RawListing(row["hash"], row["url"],
            {target: row[key] for key,target in JOB_PAYLOAD_MAP.items()}, apply_url=row["apply_url"])
            for row in missing)
        await RawListingSink().handle(session, str(scope_id), listings)
        actual = (await session.execute(sa.text(
            "SELECT sl.external_id,i.url,v.id FROM source_listings sl "
            "JOIN source_listing_incarnations i ON i.source_listing_id=sl.id AND i.ended_at IS NULL "
            "JOIN vacancies v ON v.id=i.vacancy_id "
            "WHERE sl.source_id=:src AND sl.external_id=ANY(:ids) AND v.archived_at IS NULL "
            "AND v.merged_into IS NULL AND v.current_offer_revision_id IS NOT NULL"
        ), {"src": source_id, "ids": [row["hash"] for row in missing]})).all()
        if {(r.external_id,r.url) for r in actual} != {(r["hash"],r["url"]) for r in missing}:
            raise SearchMigrationError("missing-offer import incomplete")
        provenance = await captured_provenance(session)
        return {"resolved": len(rows), "inserted_listings": len(missing), "provenance": provenance}
