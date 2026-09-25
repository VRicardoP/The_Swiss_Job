"""Offline: controls exposed by the frontend are rejected by CoreCatalog.

Run with PYTHONPATH=SwissJob/backend and an existing project Python environment.
No DB/API calls: a tripwire fails if a network client is constructed.
"""

import asyncio
import json
from services.catalog.core_client import CoreCatalog
from services.catalog.port import CatalogSearchParams, CatalogUnsupportedError
from routers.jobs import _catalog_http_error


async def main():
    def no_network():
        raise AssertionError("Unexpected network")

    cases = [{"sort": value} for value in ("oldest", "salary", "relevance")]
    cases += [
        {"canton": "ZH"},
        {"language": "de"},
        {"seniority": "senior"},
        {"contract_type": "full_time"},
        {"salary_min": 80000},
    ]
    rejected = []
    for case in cases:
        try:
            await CoreCatalog(client_factory=no_network).search(
                CatalogSearchParams(**case)
            )
        except CatalogUnsupportedError as exc:
            assert _catalog_http_error(exc).status_code == 501
            rejected.append(case)
        else:
            raise AssertionError(f"Defect no longer reproduced: {case}")
    print(
        json.dumps({"visible_search_controls_rejected": rejected, "network_calls": 0})
    )


if __name__ == "__main__":
    asyncio.run(main())
