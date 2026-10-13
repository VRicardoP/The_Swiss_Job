#!/usr/bin/env python3
"""Fail-closed post-deploy probe for the Core harvest adapter routes.

Run through stdin inside the live Portfolio backend. Output contains only
versions, counts, timings and failure codes; vacancy payloads and credentials
are never printed.
"""

import argparse
import json
import time
import uuid

import httpx

from config import settings


SOURCES = (
    "arbeitnow",
    "irishjobs",
    "jobgether",
    "nav_arbeidsplassen",
    "ostjob",
    "remotive",
    "thehub",
    "weworkremotely",
    "zentraljob",
)
COUNT_KEYS = {
    "locations",
    "tags",
    "companies",
    "categories",
    "job_types",
    "cantons",
    "remote",
    "onsite",
}


def request_ms(client: httpx.Client, method: str, path: str, **kwargs):
    started = time.perf_counter()
    response = client.request(method, path, **kwargs)
    return response, round((time.perf_counter() - started) * 1000, 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--ready-only",
        action="store_true",
        help="validate transport and ready without requiring candidate routes",
    )
    parser.add_argument(
        "--after-grant",
        action="store_true",
        help="also prove harvest:write reaches the dispatch handler without enqueueing",
    )
    args = parser.parse_args()

    failures: list[str] = []
    result: dict = {"sources": {}}
    headers = {"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"}
    with httpx.Client(
        base_url=settings.CORE_API_BASE_URL,
        headers=headers,
        timeout=180,
    ) as client:
        ready, ready_ms = request_ms(client, "GET", "/ready")
        ready_body = ready.json() if ready.status_code == 200 else {}
        result["ready"] = {
            "status_code": ready.status_code,
            "elapsed_ms": ready_ms,
            "status": ready_body.get("status"),
            "release": ready_body.get("release"),
            "alembic": ready_body.get("alembic"),
            "authoritative": ready_body.get("authoritative"),
        }
        if ready.status_code != 200 or ready_body.get("status") != "ready":
            failures.append("core_not_ready")
        if ready_body.get("authoritative") is not True:
            failures.append("core_not_authoritative")

        if not args.ready_only:
            first_etag = None
            for source in SOURCES:
                response, elapsed_ms = request_ms(
                    client,
                    "GET",
                    f"/harvest/sources/{source}/aggregate",
                    params={"item_limit": 500, "top_n": 500},
                )
                entry = {
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                }
                if response.status_code != 200:
                    failures.append(f"{source}:status_{response.status_code}")
                    result["sources"][source] = entry
                    continue
                body = response.json()
                items = body.get("items")
                counts = body.get("counts")
                entry.update(
                    {
                        "source": body.get("source"),
                        "total": body.get("total"),
                        "items": len(items) if isinstance(items, list) else None,
                        "enabled_scopes": body.get("enabled_scopes"),
                        "observed_scopes": body.get("observed_scopes"),
                        "max_consecutive_failures": body.get(
                            "max_consecutive_failures"
                        ),
                        "etag": bool(response.headers.get("etag")),
                        "count_keys": sorted(counts) if isinstance(counts, dict) else [],
                    }
                )
                if body.get("source") != source:
                    failures.append(f"{source}:source_mismatch")
                if not isinstance(items, list) or len(items) > 500:
                    failures.append(f"{source}:items_unbounded")
                if isinstance(body.get("total"), int) and body["total"] > 0:
                    if not items:
                        failures.append(f"{source}:nonempty_corpus_without_items")
                elif not isinstance(body.get("total"), int) or body["total"] < 0:
                    failures.append(f"{source}:invalid_total")
                if not isinstance(counts, dict) or set(counts) != COUNT_KEYS:
                    failures.append(f"{source}:count_contract")
                enabled = body.get("enabled_scopes")
                if not isinstance(enabled, int) or enabled < 1:
                    failures.append(f"{source}:no_enabled_scope")
                if body.get("observed_scopes") != enabled:
                    failures.append(f"{source}:scope_state_incomplete")
                if body.get("max_consecutive_failures") != 0:
                    failures.append(f"{source}:harvest_failures")
                if not response.headers.get("etag"):
                    failures.append(f"{source}:etag_missing")
                if first_etag is None:
                    first_etag = (source, response.headers["etag"])
                result["sources"][source] = entry

            if first_etag is not None:
                source, etag = first_etag
                cached, elapsed_ms = request_ms(
                    client,
                    "GET",
                    f"/harvest/sources/{source}/aggregate",
                    params={"item_limit": 500, "top_n": 500},
                    headers={"If-None-Match": etag},
                )
                result["etag_revalidation"] = {
                    "source": source,
                    "status_code": cached.status_code,
                    "elapsed_ms": elapsed_ms,
                }
                if cached.status_code != 304:
                    failures.append("etag_revalidation_not_304")

            if args.after_grant:
                missing = "__optimization_probe_missing__"
                dispatched, elapsed_ms = request_ms(
                    client,
                    "POST",
                    f"/harvest/sources/{missing}/runs",
                    headers={"Idempotency-Key": f"probe-{uuid.uuid4()}"},
                )
                try:
                    error_code = dispatched.json().get("code")
                except ValueError:
                    error_code = None
                result["dispatch_scope_probe"] = {
                    "status_code": dispatched.status_code,
                    "error_code": error_code,
                    "elapsed_ms": elapsed_ms,
                    "enqueued": False,
                }
                if (dispatched.status_code, error_code) != (404, "not_found"):
                    failures.append("dispatch_scope_or_route_failed")

    result["failures"] = failures
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(main())
