"""School persistence. Caller owns transactions; never fetches or sends email.

Schools are shared identities. A monitor's settings, including its contact and
alert configuration, belong only to its consumer. Lock an existing monitor
before editing/deleting it; creation serializes on the shared school's key.
"""

import json
import uuid

import sqlalchemy as sa

# Verified aliases of existing monitors, not fuzzy entity matching. Two Beau
# Soleil endpoints and both consumers retain their own independent settings.
_SWISS_ALIASES = {
    "beau": "beausoleil_villars",
    "beausoleil_inline": "beausoleil_villars",
    "isb": "isb_basel",
    "iscs": "iscs_zug",
    "isg": "ecolint_geneva",
    "vis": "verbier_vis",
    "zis": "zis_zurich",
}


def school_key(country, external_ref):
    country = country.upper()
    slug = (
        _SWISS_ALIASES.get(external_ref, external_ref)
        if country == "CH"
        else external_ref
    )
    return country + ":" + slug


async def monitor(session, consumer_id, monitor_id, *, write=False):
    return (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_monitors WHERE id=:id AND consumer_id=:cid"
                    + (" FOR UPDATE" if write else "")
                ),
                {"id": monitor_id, "cid": consumer_id},
            )
        )
        .mappings()
        .one_or_none()
    )


async def monitors(session, consumer_id, limit, cursor=None):
    params = {"cid": consumer_id, "limit": limit + 1}
    condition = ""
    if cursor is not None:
        params["ts"], params["id"] = cursor
        condition = " AND (created_at,id)<(:ts,:id)"
    rows = (
        (
            await session.execute(
                sa.text(
                    "SELECT * FROM school_monitors WHERE consumer_id=:cid"
                    + condition
                    + " ORDER BY created_at DESC,id DESC LIMIT :limit"
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    items = rows[:limit]
    following = (
        (items[-1]["created_at"], items[-1]["id"]) if len(rows) > limit else None
    )
    return items, following


async def create_monitor(
    session, consumer_id, external_ref, settings, *, monitor_id=None
):
    # A slug is only meaningful within its country. No personal configuration
    # is copied into the shared identity, and later tenants cannot overwrite it.
    key = school_key(settings["country"], external_ref)
    await session.execute(
        sa.text(
            "INSERT INTO schools(id,school_key,name,country) VALUES (:id,:key,:name,:country) "
            "ON CONFLICT(school_key) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "key": key,
            "name": settings["name"],
            "country": settings["country"].upper(),
        },
    )
    school_id = (
        await session.execute(
            sa.text("SELECT id FROM schools WHERE school_key=:key"), {"key": key}
        )
    ).scalar_one()
    return (
        await session.execute(
            sa.text(
                "INSERT INTO school_monitors(id,consumer_id,school_id,external_ref,settings) "
                "VALUES (:id,:cid,:sid,:ref,CAST(:data AS jsonb)) "
                "ON CONFLICT(consumer_id,external_ref) DO NOTHING RETURNING id"
            ),
            {
                "id": monitor_id or uuid.uuid4(),
                "cid": consumer_id,
                "sid": school_id,
                "ref": external_ref,
                "data": json.dumps(settings, ensure_ascii=False),
            },
        )
    ).scalar_one_or_none()


async def update_monitor(session, consumer_id, monitor_id, settings):
    await session.execute(
        sa.text(
            "UPDATE school_monitors SET settings=CAST(:data AS jsonb),version=version+1,"
            "updated_at=clock_timestamp() WHERE id=:id AND consumer_id=:cid"
        ),
        {
            "data": json.dumps(settings, ensure_ascii=False),
            "id": monitor_id,
            "cid": consumer_id,
        },
    )


async def delete_monitor(session, consumer_id, monitor_id):
    # Never delete shared vacancies, a profile's drafts, or history via CASCADE.
    dependent = (
        await session.execute(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM school_job_details WHERE monitor_id=:id) OR "
                "EXISTS(SELECT 1 FROM school_applications WHERE monitor_id=:id)"
            ),
            {"id": monitor_id},
        )
    ).scalar_one()
    if dependent:
        return False
    await session.execute(
        sa.text("DELETE FROM school_monitors WHERE id=:id AND consumer_id=:cid"),
        {"id": monitor_id, "cid": consumer_id},
    )
    return True
