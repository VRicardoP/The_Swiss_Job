import asyncio
import json

import httpx
import sqlalchemy as sa

from config import settings
from database import async_session


async def fetch_pages(client, profile_id, *, category=None):
    cursor = None
    byte_count = 0
    item_count = 0
    requests = 0
    total = None
    while True:
        params = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        if category:
            params["category"] = category
        response = await client.get(f"/profiles/{profile_id}/matches", params=params)
        response.raise_for_status()
        byte_count += len(response.content)
        requests += 1
        body = response.json()
        item_count += len(body.get("items") or [])
        total = body.get("total", total)
        cursor = body.get("next_cursor")
        if not cursor:
            return {
                "bytes": byte_count,
                "items": item_count,
                "requests": requests,
                "total": total,
            }


async def main():
    async with async_session() as db:
        profile_ids = (
            (
                await db.execute(
                    sa.text(
                        "SELECT core_profile_id FROM jobhunt_profile_map ORDER BY user_id"
                    )
                )
            )
            .scalars()
            .all()
        )
    headers = {"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"}
    results = []
    async with httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers=headers,
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    ) as client:
        for index, profile_id in enumerate(profile_ids, 1):
            facets_response = await client.get(f"/profiles/{profile_id}/matches/facets")
            facets_response.raise_for_status()
            facets = facets_response.json()
            category = next(
                entry["id"] for entry in facets["categories"] if entry["count"]
            )
            full = await fetch_pages(client, profile_id)
            category_page = await client.get(
                f"/profiles/{profile_id}/matches",
                params={"limit": 50, "category": category},
            )
            category_page.raise_for_status()
            category_body = category_page.json()
            results.append(
                {
                    "profile_index": index,
                    "facets_bytes": len(facets_response.content),
                    "facets_total": facets["total"],
                    "full_feed": full,
                    "first_category": category,
                    "category_page_bytes": len(category_page.content),
                    "category_page_items": len(category_body.get("items") or []),
                    "category_total": category_body.get("total"),
                }
            )
    print(json.dumps(results, sort_keys=True))


asyncio.run(main())
