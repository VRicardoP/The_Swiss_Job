"""SwissJob saved-search filters against the shared, presentable corpus.

This is not the catalog's substring query: the existing search contract uses
simple full-text AND over title, description and company, excluding tags.
Structured values must have been normalized by the source; missing means
unknown, not an inferred match. No scheduling, notification or watermark is
changed here. The handover must populate these fields before switching readers.
"""

from pydantic import BaseModel, ConfigDict, Field
import sqlalchemy as sa


class SwissJobSearchFilters(BaseModel):
    """Versioned consumer boundary, not an interpretation of arbitrary JSON.

    Mirrors SwissJob's saved filters; unknown/malformed fields fail closed.
    Portfolio's different filter dialect must not enter this evaluator.
    min_score belongs to the saved-search envelope, not these filters: it is
    deliberately inert in the existing search contract (no ranking here).

    DT-148 A18-01: `canton`, `language`, `seniority`, `contract_type` and
    salary_min/max STAY here, unlike in the catalogue search, because this
    evaluator does implement them against the canonical content — they do not
    501. What they do suffer is the same coverage problem: measured on 5,000
    active production vacancies, canton is present in 9.8% of canonicals,
    language 2.9%, contract_type 2.1%, seniority 0.9% and the salary amounts
    1.1%. A saved search filtering by seniority will alert on almost nothing,
    and that is a property of the corpus, not a bug here. `location` is the
    one with full coverage.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    q: str | None = None
    source: str | None = None
    # DT-148 A18-01 (2026-10-21): `location` entra para que una búsqueda
    # guardada pueda expresar la MISMA ubicación que la búsqueda en vivo, que
    # dejó de ofrecer el desplegable de cantones. Se aplica como substring ci
    # sobre `location`, igual que el `city` del catálogo, y sobre el único
    # campo de ubicación que la canónica lleva SIEMPRE (100 % de las activas,
    # frente al 9,8 % de `canton`).
    location: str | None = None
    canton: str | None = None
    language: str | None = None
    seniority: str | None = None
    contract_type: str | None = None
    salary_min: int | None = Field(None, ge=0)
    salary_max: int | None = Field(None, ge=0)
    remote_only: bool = False


def matching_query(
    filters: dict, *, include_nonmatches=False
) -> tuple[sa.TextClause, dict]:
    """One parametrized query, one row per vacancy. Caller owns the transaction.

    Uses the primary listing's source, as the served catalog does; legacy:X
    and its replacement X have the same public name. No interpolation of user
    input, temporal cursor, LIMIT or intermediate watermark can lose results.
    The caller must deduplicate delivered vacancies transactionally.
    """
    f = SwissJobSearchFilters.model_validate(filters)
    eligible = ["v.archived_at IS NULL", "v.merged_into IS NULL", "pi.ended_at IS NULL"]
    where = []
    params = {}
    if f.q:
        # Reuse the existing GIN document as a superset prefilter, but remove
        # tag-only hits with the exact legacy document. No new index needed.
        query = "plainto_tsquery('pg_catalog.simple', :q)"
        where.extend(
            [
                f"o.search_document @@ {query}",
                "to_tsvector('pg_catalog.simple', "
                "coalesce(o.content->>'title','') || ' ' || "
                "coalesce(o.content->>'description','') || ' ' || "
                f"coalesce(o.content->>'company','')) @@ {query}",
            ]
        )
        params["q"] = f.q
    sources = [name.strip() for name in (f.source or "").split(",") if name.strip()]
    if sources:
        where.append("regexp_replace(src.name, '^legacy:', '') = ANY(:sources)")
        params["sources"] = sources
    if f.location and f.location.strip():
        # LIKE, no `position()`: lo primero lo sirve el índice trgm de
        # `core0057`, lo segundo obliga a un Seq Scan por fila (DT-148 A18-01,
        # medido en 45-86 s en producción). Los metacaracteres del usuario se
        # escapan: el contrato es «contiene», no «patrón».
        literal = (
            f.location.strip()
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        where.append(
            "lower(coalesce(o.content->>'location', '')) LIKE lower(:location)"
        )
        params["location"] = "%" + literal + "%"
    cantons = [
        name.strip().upper() for name in (f.canton or "").split(",") if name.strip()
    ]
    if cantons:
        where.append("o.content->>'canton' = ANY(:cantons)")
        params["cantons"] = cantons
    if f.remote_only:
        where.append("o.content->'remote' = 'true'::jsonb")
    for field in ("language", "seniority", "contract_type"):
        value = getattr(f, field)
        if value:
            where.append(f"o.content->>'{field}' = :{field}")
            params[field] = value
    for field, stored, operator in (
        ("salary_min", "salary_max_chf", ">="),
        ("salary_max", "salary_min_chf", "<="),
    ):
        value = getattr(f, field)
        if value is not None:
            # Old/malformed JSON cannot crash the whole sweep (CASE, not a
            # reorderable AND). Numeric strings and booleans are not salaries.
            where.append(
                f"CASE WHEN jsonb_typeof(o.content->'{stored}') = 'number' "
                f"THEN CAST(o.content->>'{stored}' AS numeric) END {operator} :{field}"
            )
            params[field] = value
    # The executor observes matching AND nonmatching vacancies in ONE snapshot.
    # Editing filters must not turn already-observed old offers into new alerts.
    selection = "v.id AS vacancy_id, v.created_at"
    if include_nonmatches:
        selection += (
            ", COALESCE((" + (" AND ".join(where) or "true") + "), false) AS matches"
        )
        where = []
    sql = (
        "SELECT " + selection + " FROM vacancies v "
        "JOIN offer_revisions o ON o.id=v.current_offer_revision_id "
        "JOIN source_listing_incarnations pi ON pi.id=v.primary_incarnation_id "
        "JOIN source_listings sl ON sl.id=pi.source_listing_id "
        "JOIN sources src ON src.id=sl.source_id WHERE "
        + " AND ".join(eligible + where)
        + " ORDER BY v.created_at, v.id"
    )
    return sa.text(sql), params


async def matching_vacancies(session, filters: dict):
    """Return the complete matching set, without mutating any user state."""
    sql, params = matching_query(filters)
    return (await session.execute(sql, params)).all()
