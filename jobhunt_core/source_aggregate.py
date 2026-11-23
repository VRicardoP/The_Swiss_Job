"""Bounded source catalog snapshot for legacy-compatible read APIs."""

import sqlalchemy as sa

# The aggregate and the light "recent" read share these fragments, so both
# keep one definition of a row, of the latest observation and of the order.

_ROW_COLUMNS = """      SELECT i.id AS incarnation_id, sl.external_id, i.url, i.apply_url,
             i.first_seen_at, i.last_seen_at,
             o.content->>'title' AS title,
             o.content->>'company' AS company,
             CASE WHEN s.name IN ('jsearch','dynamitejobs')
                  THEN left(latest.provider_description, 500)
                  ELSE left(o.content->>'description', 500) END AS description,
             CASE WHEN s.name='dynamitejobs'
                  THEN latest.statistics_location
                  ELSE o.content->>'location' END AS statistics_location,
             o.content->>'location' AS location,
             o.content->>'salary' AS salary,
             CASE WHEN jsonb_typeof(o.content->'salary_min')='number'
                  THEN o.content->'salary_min' END AS salary_min,
             CASE WHEN jsonb_typeof(o.content->'salary_max')='number'
                  THEN o.content->'salary_max' END AS salary_max,
             o.content->>'salary_currency' AS salary_currency,
             o.content->>'logo' AS logo,
             latest.provider_id, latest.city, latest.state, latest.country,
             latest.publisher,
             CASE WHEN jsonb_typeof(o.content->'remote')='boolean'
                  THEN (o.content->>'remote')::boolean END AS remote,
             CASE WHEN jsonb_typeof(o.content->'tags')='array'
                  THEN o.content->'tags' ELSE '[]'::jsonb END AS tags,
             o.content->>'canton' AS canton,
             o.content->>'prof_group' AS prof_group,
             latest.category, latest.categories_raw, latest.technologies_raw,
             latest.statistics_tags_raw,
             COALESCE(o.content->>'employment_type', latest.job_type) AS job_type,
             latest.job_types_raw,
             latest.published_at, latest.source_order
"""

_ROW_JOINS = """      FROM sources s
      JOIN source_listings sl ON sl.source_id=s.id
      JOIN source_listing_incarnations i
        ON i.source_listing_id=sl.id AND i.ended_at IS NULL
      JOIN vacancies v
        ON v.id=i.vacancy_id AND v.archived_at IS NULL AND v.merged_into IS NULL
      JOIN offer_revisions o ON o.id=v.current_offer_revision_id
"""

_LATEST_A = """        SELECT COALESCE(
                 CASE WHEN jsonb_typeof(r.raw->'category')='string'
                      THEN r.raw->>'category' END,
                 CASE WHEN jsonb_typeof(r.raw->'_core_category')='string'
                      THEN r.raw->>'_core_category' END,
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
               CASE WHEN jsonb_typeof(r.raw->'technologies')='array'
                    THEN r.raw->'technologies' ELSE '[]'::jsonb
               END AS technologies_raw,
               CASE WHEN jsonb_typeof(r.raw->'_core_statistics_tags')='array'
                    THEN r.raw->'_core_statistics_tags' ELSE '[]'::jsonb
               END AS statistics_tags_raw,
               CASE WHEN jsonb_typeof(r.raw->'_core_statistics_location')='string'
                    THEN r.raw->>'_core_statistics_location' END
                 AS statistics_location,
"""

_SOURCE_ORDER = """               CASE WHEN jsonb_typeof(r.raw->'_core_order')='number'
                    THEN (r.raw->>'_core_order')::int END AS source_order,
"""

_LATEST_B = """               CASE WHEN jsonb_typeof(r.raw->'job_city')='string'
                    THEN r.raw->>'job_city' END AS city,
               CASE WHEN jsonb_typeof(r.raw->'job_state')='string'
                    THEN r.raw->>'job_state' END AS state,
               CASE WHEN jsonb_typeof(r.raw->'job_country')='string'
                    THEN r.raw->>'job_country' END AS country,
               CASE WHEN jsonb_typeof(r.raw->'job_publisher')='string'
                    THEN r.raw->>'job_publisher' END AS publisher,
               COALESCE(
                 CASE WHEN jsonb_typeof(r.raw->'job_description')='string'
                      THEN r.raw->>'job_description' END,
                 CASE WHEN jsonb_typeof(r.raw->'_core_description')='string'
                      THEN r.raw->>'_core_description' END
               ) AS provider_description,
               CASE WHEN jsonb_typeof(r.raw->'job_id') IN ('string','number')
                    THEN r.raw->>'job_id' END AS provider_id,
"""

_PUBLISHED_AT = """               COALESCE(
                 CASE WHEN jsonb_typeof(r.raw->'publication_date')='string'
                      THEN r.raw->'publication_date' END,
                 CASE WHEN jsonb_typeof(r.raw->'created_at')='string'
                      THEN r.raw->'created_at' END,
                 CASE WHEN jsonb_typeof(r.raw->'created')='string'
                      THEN r.raw->'created' END,
                 CASE WHEN jsonb_typeof(r.raw->'job_posted_at_datetime_utc')='string'
                      THEN r.raw->'job_posted_at_datetime_utc' END,
                 CASE WHEN jsonb_typeof(r.raw->'dateFirstPublished')='string'
                      THEN r.raw->'dateFirstPublished' END,
                 CASE WHEN jsonb_typeof(r.raw->'date') IN ('string', 'number')
                      THEN r.raw->'date' END,
                 CASE WHEN jsonb_typeof(r.raw->'pubDate') IN ('string', 'number')
                      THEN r.raw->'pubDate' END,
                 CASE WHEN jsonb_typeof(r.raw->'activeFrom')='string'
                       THEN r.raw->'activeFrom' END,
                 CASE WHEN jsonb_typeof(r.raw->'_core_published_at')='string'
                       THEN r.raw->'_core_published_at' END,
                 CASE WHEN jsonb_typeof(
                            r.raw#>'{jobAdvertisement,publication,startDate}'
                          )='string'
                       THEN r.raw#>'{jobAdvertisement,publication,startDate}' END,
                 CASE WHEN jsonb_typeof(r.raw#>'{advert,startDate}')='string'
                       THEN r.raw#>'{advert,startDate}' END,
                 CASE WHEN jsonb_typeof(r.raw->'start_date')='string'
                       THEN r.raw->'start_date' END,
                 CASE WHEN jsonb_typeof(r.raw->'date_posted')='string'
                       THEN r.raw->'date_posted' END
               ) AS published_at
"""

_LATEST_END = """        FROM source_listing_revisions r
        WHERE r.incarnation_id=i.id
        ORDER BY r.fetched_at DESC, r.id DESC LIMIT 1
      ) latest ON true
"""

_SOURCE_META = """    source_meta AS (
      SELECT count(*) FILTER (WHERE hs.enabled)::int AS enabled_scopes,
             count(sss.scope_id) FILTER (WHERE hs.enabled)::int AS observed_scopes,
             max(sss.last_complete_at) FILTER (WHERE hs.enabled) AS last_complete_at,
             max(sss.cursor->>'last_query') FILTER (WHERE hs.enabled) AS last_query,
             COALESCE(max(sss.consecutive_failures)
                      FILTER (WHERE hs.enabled), 0)::int AS max_failures
      FROM sources s
      LEFT JOIN harvest_scopes hs ON hs.source_id=s.id
      LEFT JOIN source_scope_state sss ON sss.scope_id=hs.id
      WHERE s.name=:source
    ),
"""

_RECENT_HEAD = """    recent AS (
      SELECT jsonb_build_object(
               'external_id', external_id,
               'provider_id', provider_id,
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
               'city', city,
               'state', state,
               'country', country,
               'publisher', publisher,
               'remote', remote,
               'tags', tags,
               'canton', canton,
               'prof_group', prof_group,
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
             last_seen_at, incarnation_id, published_at, source_order
      FROM source_rows
"""
# Shared order of recent items: the light read picks its winners with it.
_RECENT_ORDER_LIMIT = """      ORDER BY
        CASE WHEN :source IN ('adzuna','jsearch')
             THEN published_at #>> '{}' END DESC NULLS LAST,
        CASE WHEN :source IN ('adzuna','jsearch') THEN source_order END ASC NULLS LAST,
        CASE WHEN :source IN ('dynamitejobs','jobscout24')
             THEN source_order END ASC NULLS LAST,
        last_seen_at DESC, incarnation_id
      LIMIT :item_limit
"""
_RECENT = _RECENT_HEAD + _RECENT_ORDER_LIMIT + "    ),\n"

_META_SELECT = """           meta.enabled_scopes, meta.observed_scopes,
           meta.last_complete_at, meta.last_query, meta.max_failures,
           EXISTS(
             SELECT 1 FROM source_harvest_runs shr
             JOIN harvest_scopes hs ON hs.id=shr.scope_id
             JOIN sources s ON s.id=hs.source_id
             WHERE s.name=:source AND shr.status='running'
               AND shr.finished_at IS NULL
               AND COALESCE(shr.heartbeat_at, shr.started_at) >=
                   clock_timestamp()-make_interval(secs => :lease)
           ) AS update_in_progress,
"""

_ITEMS = """           COALESCE((SELECT jsonb_agg(item ORDER BY
                       CASE WHEN :source IN ('adzuna','jsearch')
                            THEN published_at #>> '{}' END DESC NULLS LAST,
                       CASE WHEN :source IN ('adzuna','jsearch')
                            THEN source_order END ASC NULLS LAST,
                       CASE WHEN :source IN ('dynamitejobs','jobscout24')
                            THEN source_order END ASC NULLS LAST,
                       last_seen_at DESC, incarnation_id)
                     FROM recent), '[]'::jsonb) AS items,
"""

_LATEST_LATERAL = (
    "      LEFT JOIN LATERAL (\n"
    + _LATEST_A
    + _SOURCE_ORDER
    + _LATEST_B
    + _PUBLISHED_AT
    + _LATEST_END
)

_COUNTS = """    location_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT statistics_location AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    ),
    tag_counts AS (
      SELECT tag.value, count(*)::int AS n FROM source_rows
      CROSS JOIN LATERAL (
        SELECT item #>> '{}' AS value
        FROM jsonb_array_elements(
          CASE
            WHEN :source='swissdevjobs' THEN technologies_raw
            WHEN :source IN ('jobroom','dynamitejobs','jobscout24')
              THEN statistics_tags_raw
            ELSE tags
          END
        ) AS entry(item)
        WHERE jsonb_typeof(item)='string'
      ) AS tag
      WHERE btrim(tag.value)<>''
      GROUP BY tag.value ORDER BY n DESC, tag.value LIMIT :top_n
    ),
    company_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT company AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    ),
    publisher_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT publisher AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    ),
    category_counts AS (
      SELECT category.value, count(*)::int AS n FROM source_rows
      CROSS JOIN LATERAL (
        SELECT CASE WHEN :source='adzuna' THEN btrim(value)
                    ELSE lower(replace(btrim(value), ' ', '-')) END AS value
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
    ),
    prof_group_counts AS (
      SELECT value, count(*)::int AS n FROM (
        SELECT prof_group AS value FROM source_rows
      ) values_ WHERE value IS NOT NULL AND btrim(value)<>''
      GROUP BY value ORDER BY n DESC, value LIMIT :top_n
    )
"""

_COUNT_SELECTS = """           COALESCE((SELECT jsonb_object_agg(value, n) FROM location_counts),
                    '{}'::jsonb) AS locations,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM tag_counts),
                    '{}'::jsonb) AS tags,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM company_counts),
                    '{}'::jsonb) AS companies,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM publisher_counts),
                    '{}'::jsonb) AS publishers,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM category_counts),
                    '{}'::jsonb) AS categories,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM job_type_counts),
                    '{}'::jsonb) AS job_types,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM canton_counts),
                    '{}'::jsonb) AS cantons,
           COALESCE((SELECT jsonb_object_agg(value, n) FROM prof_group_counts),
                    '{}'::jsonb) AS prof_groups,
           (SELECT count(*) FILTER (WHERE remote IS TRUE)::int
            FROM source_rows) AS remote,
           (SELECT count(*) FILTER (WHERE remote IS FALSE)::int
            FROM source_rows) AS onsite
"""

_SOURCE_AGGREGATE_SQL = sa.text(
    "\n    WITH source_rows AS MATERIALIZED (\n"
    + _ROW_COLUMNS
    + _ROW_JOINS
    + _LATEST_LATERAL
    + "      WHERE s.name=:source\n    ),\n"
    + _SOURCE_META
    + _RECENT
    + _COUNTS
    + "    SELECT EXISTS(SELECT 1 FROM sources WHERE name=:source) AS found,\n"
    + "           (SELECT count(*)::int FROM source_rows) AS total,\n"
    + "           (SELECT max(last_seen_at) FROM source_rows) AS corpus_updated_at,\n"
    + _META_SELECT
    + _ITEMS
    + _COUNT_SELECTS
    + "    FROM source_meta meta\n    "
)

# Only these sources order their recent items by fields of the latest raw
# observation; every other source orders by last_seen_at alone.
_RAW_ORDERED_SOURCES = "('adzuna','jsearch','dynamitejobs','jobscout24')"

# Light read for recent items: rank the current incarnations with the minimal
# keys first, then extract the full row only for the `item_limit` winners.
# The aggregate extracts every JSON field of EVERY row before limiting, which
# is what made a recent list cost minutes on large sources (NAS, 2026-11).
_SOURCE_RECENT_SQL = sa.text(
    "\n    WITH candidates AS MATERIALIZED (\n"
    + "      SELECT i.id AS incarnation_id, i.last_seen_at,\n"
    + "             latest.published_at, latest.source_order\n"
    + _ROW_JOINS
    + "      LEFT JOIN LATERAL (\n"
    + "        SELECT "
    + _PUBLISHED_AT.lstrip()
    + "               , "
    + _SOURCE_ORDER.lstrip().rstrip().rstrip(",")
    + "\n"
    + _LATEST_END.replace(
        "        WHERE r.incarnation_id=i.id\n",
        "        WHERE r.incarnation_id=i.id\n"
        f"          AND :source IN {_RAW_ORDERED_SOURCES}\n",
    )
    + "      WHERE s.name=:source\n    ),\n"
    + "    chosen AS (\n"
    + "      SELECT incarnation_id FROM candidates\n"
    + _RECENT_ORDER_LIMIT
    + "    ),\n"
    + "    source_rows AS MATERIALIZED (\n"
    + _ROW_COLUMNS
    + _ROW_JOINS
    + _LATEST_LATERAL
    + "      WHERE s.name=:source\n"
    + "        AND i.id IN (SELECT incarnation_id FROM chosen)\n"
    + "    ),\n"
    + _SOURCE_META
    + _RECENT.rstrip().rstrip(",")
    + "\n"
    + "    SELECT EXISTS(SELECT 1 FROM sources WHERE name=:source) AS found,\n"
    + "           (SELECT count(*)::int FROM candidates) AS total,\n"
    + "           (SELECT max(last_seen_at) FROM candidates) AS corpus_updated_at,\n"
    + _META_SELECT
    + _ITEMS.rstrip().rstrip(",")
    + "\n    FROM source_meta meta\n    "
)


def _snapshot(source: str, row) -> dict:
    """Fields shared by the aggregate and the light recent read."""
    return {
        "source": source,
        "total": row["total"],
        "corpus_updated_at": row["corpus_updated_at"],
        "last_complete_at": row["last_complete_at"],
        "last_query": row["last_query"],
        "enabled_scopes": row["enabled_scopes"],
        "observed_scopes": row["observed_scopes"],
        "max_consecutive_failures": row["max_failures"],
        "update_in_progress": row["update_in_progress"],
        "items": row["items"],
    }


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
        **_snapshot(source, row),
        "counts": {
            "locations": row["locations"],
            "tags": row["tags"],
            "companies": row["companies"],
            "publishers": row["publishers"],
            "categories": row["categories"],
            "job_types": row["job_types"],
            "cantons": row["cantons"],
            "prof_groups": row["prof_groups"],
            "remote": row["remote"],
            "onsite": row["onsite"],
        },
    }


async def fetch_source_recent(session, source: str, *, item_limit: int, lease: int):
    """Same items, total, freshness and harvest state as the aggregate, without
    its per-dimension counts."""
    row = (
        (
            await session.execute(
                _SOURCE_RECENT_SQL,
                {"source": source, "item_limit": item_limit, "lease": lease},
            )
        )
        .mappings()
        .one()
    )
    if not row["found"]:
        return None
    return _snapshot(source, row)
