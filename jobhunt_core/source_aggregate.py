"""Bounded source catalog snapshot for legacy-compatible read APIs."""

import sqlalchemy as sa

_SOURCE_AGGREGATE_SQL = sa.text(
    """
    WITH source_rows AS MATERIALIZED (
      SELECT i.id AS incarnation_id, sl.external_id, i.url, i.apply_url,
             i.first_seen_at, i.last_seen_at,
             o.content->>'title' AS title,
             o.content->>'company' AS company,
             left(o.content->>'description', 500) AS description,
             o.content->>'location' AS location,
             o.content->>'salary' AS salary,
             CASE WHEN jsonb_typeof(o.content->'salary_min')='number'
                  THEN o.content->'salary_min' END AS salary_min,
             CASE WHEN jsonb_typeof(o.content->'salary_max')='number'
                  THEN o.content->'salary_max' END AS salary_max,
             o.content->>'salary_currency' AS salary_currency,
             o.content->>'logo' AS logo,
             CASE WHEN jsonb_typeof(o.content->'remote')='boolean'
                  THEN (o.content->>'remote')::boolean END AS remote,
             CASE WHEN jsonb_typeof(o.content->'tags')='array'
                  THEN o.content->'tags' ELSE '[]'::jsonb END AS tags,
             o.content->>'canton' AS canton,
             latest.category, latest.categories_raw,
             COALESCE(o.content->>'employment_type', latest.job_type) AS job_type,
             latest.job_types_raw,
             latest.published_at
      FROM sources s
      JOIN source_listings sl ON sl.source_id=s.id
      JOIN source_listing_incarnations i
        ON i.source_listing_id=sl.id AND i.ended_at IS NULL
      JOIN vacancies v
        ON v.id=i.vacancy_id AND v.archived_at IS NULL AND v.merged_into IS NULL
      JOIN offer_revisions o ON o.id=v.current_offer_revision_id
      LEFT JOIN LATERAL (
        SELECT COALESCE(
                 CASE WHEN jsonb_typeof(r.raw->'category')='string'
                      THEN r.raw->>'category' END,
                 CASE WHEN jsonb_typeof(r.raw->'jobIndustry')='string'
                      THEN r.raw->>'jobIndustry' END
               ) AS category,
               CASE
                 WHEN jsonb_typeof(r.raw->'jobIndustry')='array'
                   THEN r.raw->'jobIndustry'
                 WHEN jsonb_typeof(r.raw->'categories')='array'
                   THEN r.raw->'categories'
                 ELSE '[]'::jsonb
               END AS categories_raw,
               COALESCE(
                 CASE WHEN jsonb_typeof(r.raw->'job_type')='string'
                      THEN r.raw->>'job_type' END,
                 CASE WHEN jsonb_typeof(r.raw->'jobType')='string'
                      THEN r.raw->>'jobType' END
               ) AS job_type,
               CASE
                 WHEN jsonb_typeof(r.raw->'job_types')='array'
                   THEN r.raw->'job_types'
                 WHEN jsonb_typeof(r.raw->'jobType')='array'
                   THEN r.raw->'jobType'
                 ELSE '[]'::jsonb
               END AS job_types_raw,
               COALESCE(
                 CASE WHEN jsonb_typeof(r.raw->'publication_date')='string'
                      THEN r.raw->'publication_date' END,
                 CASE WHEN jsonb_typeof(r.raw->'created_at')='string'
                      THEN r.raw->'created_at' END,
                 CASE WHEN jsonb_typeof(r.raw->'dateFirstPublished')='string'
                      THEN r.raw->'dateFirstPublished' END,
                 CASE WHEN jsonb_typeof(r.raw->'date') IN ('string', 'number')
                      THEN r.raw->'date' END,
                 CASE WHEN jsonb_typeof(r.raw->'pubDate') IN ('string', 'number')
                      THEN r.raw->'pubDate' END
               ) AS published_at
        FROM source_listing_revisions r
        WHERE r.incarnation_id=i.id
        ORDER BY r.fetched_at DESC, r.id DESC LIMIT 1
      ) latest ON true
      WHERE s.name=:source
    ),
    source_meta AS (
      SELECT count(*) FILTER (WHERE hs.enabled)::int AS enabled_scopes,
             count(sss.scope_id) FILTER (WHERE hs.enabled)::int AS observed_scopes,
             max(sss.last_complete_at) FILTER (WHERE hs.enabled) AS last_complete_at,
             COALESCE(max(sss.consecutive_failures)
                      FILTER (WHERE hs.enabled), 0)::int AS max_failures
      FROM sources s
      LEFT JOIN harvest_scopes hs ON hs.source_id=s.id
      LEFT JOIN source_scope_state sss ON sss.scope_id=hs.id
      WHERE s.name=:source
    ),
    recent AS (
      SELECT jsonb_build_object(
               'external_id', external_id,
               'url', url,
               'apply_url', apply_url,
               'first_seen_at', first_seen_at,
               'last_seen_at', last_seen_at,
               'title', title,
               'company', company,
               'description', description,
               'location', location,
               'salary', salary,
               'salary_min', salary_min,
               'salary_max', salary_max,
               'salary_currency', salary_currency,
               'logo', logo,
               'remote', remote,
               'tags', tags,
               'canton', canton,
               'category', category,
               'categories', COALESCE((
                 SELECT jsonb_agg(value) FROM jsonb_array_elements(categories_raw)
                   AS item(value)
                 WHERE jsonb_typeof(value)='string'
               ), '[]'::jsonb),
               'job_type', job_type,
               'job_types', COALESCE((
                 SELECT jsonb_agg(value) FROM jsonb_array_elements(job_types_raw)
                   AS item(value)
                 WHERE jsonb_typeof(value)='string'
               ), '[]'::jsonb),
               'published_at', published_at
             ) AS item,
             last_seen_at, incarnation_id
      FROM source_rows
      ORDER BY last_seen_at DESC, incarnation_id
      LIMIT :item_limit
    ),
    location_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT location AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    ),
    tag_counts AS (
      SELECT tag.value, count(*)::int AS n FROM source_rows
      CROSS JOIN LATERAL jsonb_array_elements_text(
        tags
      ) AS tag(value)
      WHERE btrim(tag.value)<>''
      GROUP BY tag.value ORDER BY n DESC, tag.value LIMIT :top_n
    ),
    company_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT company AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    ),
    category_counts AS (
      SELECT category.value, count(*)::int AS n FROM source_rows
      CROSS JOIN LATERAL (
        SELECT lower(replace(btrim(value), ' ', '-')) AS value
        FROM (
          SELECT category AS value
          WHERE jsonb_array_length(categories_raw)=0
          UNION
          SELECT item #>> '{}'
          FROM jsonb_array_elements(categories_raw) AS entry(item)
          WHERE jsonb_typeof(item)='string'
        ) values_
        WHERE value IS NOT NULL AND btrim(value)<>''
      ) AS category
      GROUP BY category.value ORDER BY n DESC, category.value LIMIT :top_n
    ),
    job_type_counts AS (
      SELECT job_type.value, count(*)::int AS n FROM source_rows
      CROSS JOIN LATERAL (
        SELECT value FROM (
          SELECT job_type AS value
          WHERE jsonb_array_length(job_types_raw)=0
          UNION
          SELECT item #>> '{}'
          FROM jsonb_array_elements(job_types_raw) AS entry(item)
          WHERE jsonb_typeof(item)='string'
        ) values_
        WHERE value IS NOT NULL AND btrim(value)<>''
      ) AS job_type
      WHERE btrim(job_type.value)<>''
      GROUP BY job_type.value ORDER BY n DESC, job_type.value LIMIT :top_n
    ),
    canton_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT canton AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    )
    SELECT EXISTS(SELECT 1 FROM sources WHERE name=:source) AS found,
           (SELECT count(*)::int FROM source_rows) AS total,
           (SELECT max(last_seen_at) FROM source_rows) AS corpus_updated_at,
           meta.enabled_scopes, meta.observed_scopes,
           meta.last_complete_at, meta.max_failures,
           EXISTS(
             SELECT 1 FROM source_harvest_runs shr
             JOIN harvest_scopes hs ON hs.id=shr.scope_id
             JOIN sources s ON s.id=hs.source_id
             WHERE s.name=:source AND shr.status='running'
               AND shr.finished_at IS NULL
               AND COALESCE(shr.heartbeat_at, shr.started_at) >=
                   clock_timestamp()-make_interval(secs => :lease)
           ) AS update_in_progress,
           COALESCE((SELECT jsonb_agg(item ORDER BY last_seen_at DESC, incarnation_id)
                     FROM recent), '[]'::jsonb) AS items,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM location_counts),
                    '{}'::jsonb) AS locations,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM tag_counts),
                    '{}'::jsonb) AS tags,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM company_counts),
                    '{}'::jsonb) AS companies,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM category_counts),
                    '{}'::jsonb) AS categories,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM job_type_counts),
                    '{}'::jsonb) AS job_types,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM canton_counts),
                    '{}'::jsonb) AS cantons,
           (SELECT count(*) FILTER (WHERE remote IS TRUE)::int
            FROM source_rows) AS remote,
           (SELECT count(*) FILTER (WHERE remote IS FALSE)::int
            FROM source_rows) AS onsite
    FROM source_meta meta
    """
)


async def fetch_source_aggregate(
    session, source: str, *, item_limit: int, top_n: int, lease: int
):
    row = (
        (
            await session.execute(
                _SOURCE_AGGREGATE_SQL,
                {
                    "source": source,
                    "item_limit": item_limit,
                    "top_n": top_n,
                    "lease": lease,
                },
            )
        )
        .mappings()
        .one()
    )
    if not row["found"]:
        return None
    return {
        "source": source,
        "total": row["total"],
        "corpus_updated_at": row["corpus_updated_at"],
        "last_complete_at": row["last_complete_at"],
        "enabled_scopes": row["enabled_scopes"],
        "observed_scopes": row["observed_scopes"],
        "max_consecutive_failures": row["max_failures"],
        "update_in_progress": row["update_in_progress"],
        "counts": {
            "locations": row["locations"],
            "tags": row["tags"],
            "companies": row["companies"],
            "categories": row["categories"],
            "job_types": row["job_types"],
            "cantons": row["cantons"],
            "remote": row["remote"],
            "onsite": row["onsite"],
        },
        "items": row["items"],
    }
