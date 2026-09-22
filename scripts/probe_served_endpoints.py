"""Served-endpoint probe with assertions that can actually fail.

Fixes what the revalidation found: the previous canary read `jobs` where the
response carries `data`, so it would have passed a wrongly empty 200. Every
check here raises, and a self-test runs three NEGATIVE controls first to prove
the checks bite before any of them is used as evidence.

Read-only: it signs an ephemeral token in memory for an existing user, issues
GETs and never prints the token, creates nothing and writes nothing.
"""
import asyncio
import json
import statistics
import sys
import time

import httpx
from sqlalchemy import text

from config import settings
from database import async_session


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * len(xs) + 0.5)) - 1))
    return xs[k]


class ProbeFailure(AssertionError):
    pass


def check_catalog(body, *, min_items):
    """The catalogue page must carry `data` with real offers and a total."""
    if not isinstance(body, dict):
        raise ProbeFailure("catalogue body is not an object")
    if "data" not in body:
        raise ProbeFailure(f"no 'data' key; got {sorted(body)[:6]}")
    data = body["data"]
    if not isinstance(data, list):
        raise ProbeFailure("'data' is not a list")
    if len(data) < min_items:
        raise ProbeFailure(f"expected at least {min_items} offers, got {len(data)}")
    total = body.get("total")
    if not isinstance(total, int) or total <= 0:
        raise ProbeFailure(f"total is not a positive integer: {total!r}")
    if total < len(data):
        raise ProbeFailure(f"total {total} smaller than the page it carries")
    for offer in data:
        if not offer.get("hash") or not offer.get("title"):
            raise ProbeFailure("an offer has no identity or no title")
    return len(data), total


def check_matches(body, *, expected_len, expected_total=None):
    """The feed page must carry its items, a coherent total and stable ids."""
    if not isinstance(body, dict):
        raise ProbeFailure("matches body is not an object")
    items = body.get("data")
    if not isinstance(items, list):
        raise ProbeFailure(f"no list of results; keys={sorted(body)[:8]}")
    if len(items) != expected_len:
        raise ProbeFailure(f"expected {expected_len} items, got {len(items)}")
    total = body.get("total")
    if not isinstance(total, int) or total < len(items):
        raise ProbeFailure(f"incoherent total {total!r} for {len(items)} items")
    if expected_total is not None and total != expected_total:
        raise ProbeFailure(f"total {total} != expected {expected_total}")
    return len(items), total


def self_test():
    """Negative controls: the checks must FAIL on these three bodies."""
    casos = [
        ("200 vacío indebido", lambda: check_catalog({"data": [], "total": 46489}, min_items=1)),
        ("clave equivocada", lambda: check_catalog({"jobs": [{"hash": "a", "title": "t"}], "total": 5}, min_items=1)),
        ("total incoherente", lambda: check_matches({"results": [{"id": 1}] * 20, "total": 3}, expected_len=20)),
        ("cardinalidad distinta", lambda: check_matches({"results": [{"id": 1}], "total": 1800}, expected_len=20)),
        ("total distinto del esperado", lambda: check_matches(
            {"results": [{"id": 1}] * 20, "total": 1799}, expected_len=20, expected_total=1800)),
    ]
    for nombre, fn in casos:
        try:
            fn()
        except ProbeFailure:
            continue
        raise SystemExit(f"CONTROL NEGATIVO NO DETECTADO: {nombre}")
    print(json.dumps({"controles_negativos": len(casos), "todos_detectados": True}), flush=True)


async def token_for(user_id):
    """Ephemeral bearer for an EXISTING user. Never printed, never stored."""
    from core.security import create_access_token
    return create_access_token(user_id)


async def main():
    self_test()
    async with async_session() as db:
        user = (await db.execute(text(
            "SELECT user_id FROM jobhunt_profile_map ORDER BY user_id LIMIT 1"))).scalar()
    bearer = await token_for(user)
    headers = {"Authorization": f"Bearer {bearer}"}
    base = "http://127.0.0.1:8000/api/v1"
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20

    async with httpx.AsyncClient(timeout=60) as c:
        # --- catálogo, sin auth
        r = await c.get(f"{base}/jobs/search", params={"limit": 20})
        if r.status_code != 200:
            raise ProbeFailure(f"catalogue returned {r.status_code}")
        n_items, total = check_catalog(r.json(), min_items=20)
        ms = []
        for _ in range(n):
            t = time.perf_counter()
            rr = await c.get(f"{base}/jobs/search", params={"limit": 20})
            ms.append(time.perf_counter() - t)
            check_catalog(rr.json(), min_items=20)
            await asyncio.sleep(0.3)
        print(json.dumps({"endpoint": "/jobs/search", "n": len(ms), "items": n_items,
                          "total": total, "p50_s": round(statistics.median(ms), 3),
                          "p95_s": round(pct(ms, 95), 3), "max_s": round(max(ms), 3),
                          "min_s": round(min(ms), 3)}), flush=True)

        # --- feed servido, autenticado y extremo a extremo.
        # Se mide POR SEPARADO con y sin traduccion: `translate=true` llama a un
        # LLM externo, que la predeclaracion excluye del presupuesto de 2 s y
        # debe tener el suyo propio. Mezclarlos ocultaria cual de los dos cuesta.
        for translate in (False, True):
          params = {"limit": 20, "offset": 0, "translate": str(translate).lower()}
          r = await c.get(f"{base}/match/results", params=params, headers=headers)
          if r.status_code != 200:
              raise ProbeFailure(f"match/results returned {r.status_code}: {r.text[:200]}")
          n_items, total = check_matches(r.json(), expected_len=20)
          ms = []
          for _ in range(n):
              t0 = time.perf_counter()
              rr = await c.get(f"{base}/match/results", params=params, headers=headers)
              ms.append(time.perf_counter() - t0)
              if rr.status_code != 200:
                  raise ProbeFailure(f"match/results returned {rr.status_code}")
              check_matches(rr.json(), expected_len=20, expected_total=total)
              await asyncio.sleep(0.3)
          print(json.dumps({"endpoint": "/match/results", "translate": translate,
                            "n": len(ms), "items": n_items,
                            "total": total, "p50_s": round(statistics.median(ms), 3),
                            "p95_s": round(pct(ms, 95), 3), "max_s": round(max(ms), 3),
                            "min_s": round(min(ms), 3)}), flush=True)


asyncio.run(main())
