"""School presentation joins; never persist an ORM overlay as local state."""

from types import SimpleNamespace
import uuid

from services.matching.identity import resolve_core_profile_id
from .http_client import SchoolClient
from .port import CoreUnavailableError
from .seam import resolve_schools
from .state import state_on_core


async def school_job_refs(db, user_id, vacancy_ids):
    """Local actionable IDs for school vacancies without a legacy CDC listing."""
    if not vacancy_ids or not await state_on_core(db, user_id):
        return {}
    result = {}
    try:
        wanted = {str(uuid.UUID(str(value))) for value in vacancy_ids}
        for row in await SchoolClient().pages("/school-jobs"):
            vacancy = row.get("vacancy_id")
            if vacancy is None or str(uuid.UUID(vacancy)) not in wanted:
                continue
            ref, source = row["source_ref"], row["metadata"]["source"]
            if not isinstance(ref, str) or not isinstance(source, str) or not ref:
                raise ValueError("invalid school source identity")
            result.setdefault(str(uuid.UUID(vacancy)), set()).add((ref, source))
        return {vid: sorted(refs) for vid, refs in result.items()}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise CoreUnavailableError("invalid school corpus link") from None


async def school_for_job(db, user_id, job):
    port = await resolve_schools(db, user_id)
    rows = (await port.list())["schools"]
    by_ref = {row["id"]: row for row in rows}
    row = next((by_ref[tag] for tag in job.tags or [] if tag in by_ref), None)
    return SimpleNamespace(**row) if row else None


async def overlay_school_results(db, user_id, results):
    if not results or not await state_on_core(db, user_id):
        return results
    pid = await resolve_core_profile_id(db, user_id)
    if pid is None:
        raise CoreUnavailableError("school profile is not enrolled")
    port = await resolve_schools(db, user_id)
    schools = {
        row["id"]: SimpleNamespace(**row) for row in (await port.list())["schools"]
    }
    states = {row.source_ref: row for row in await SchoolClient().states(pid)}
    output = []
    for item in results:
        school = next(
            (schools[tag] for tag in item["job"].tags or [] if tag in schools), None
        )
        projected = {**item, "school": school}
        if school:
            original = item["match"]
            row = states.get(original.job_hash)
            # Both ORM and core views expose these public presentation fields.
            fields = (
                "id",
                "job_hash",
                "score_final",
                "score_embedding",
                "score_salary",
                "score_location",
                "score_recency",
                "score_llm",
                "explanation",
                "matching_skills",
                "missing_skills",
                "feedback",
                "urgency_score",
                "created_at",
            )
            projected["match"] = SimpleNamespace(
                **{name: getattr(original, name) for name in fields},
                application_status=row.status if row else "detected",
                draft_letter=row.draft_content if row else None,
            )
        output.append(projected)
    return output
