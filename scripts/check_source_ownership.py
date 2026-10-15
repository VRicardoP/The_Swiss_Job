#!/usr/bin/env python3
"""Validate DT-144's source inventory and its no-regression ratchet.

The manifest is policy; code discovery is evidence.  This script deliberately
uses only the standard library so CI can run it before installing either app.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs/unificacion/source_ownership.json"
VALID_STATES = {
    "local_pending",
    "core_shadow",
    "core_primary",
    "local_removed",
    "retired",
}
LOCAL_PUBLISHERS = {"reactportfolio", "swissjob"}
VERIFIED_INVENTORY_FIELDS = {
    "aliases",
    "current_harvesters",
    "credentials",
    "transport",
    "external_identity",
    "deduplication",
    "last_success",
    "core_scope_ids",
    "blocker",
}
FORBIDDEN_SWISSJOB_PATHS = {
    *{
        f"backend/scrapers/{name}.py"
        for name in (
            "gastrojob",
            "medjobs",
            "myscience",
            "schuljobs",
            "stelle_admin",
            "swiss_schools_base",
            "swiss_schools_ecolint",
            "swiss_schools_hautlac",
            "swiss_schools_inspired",
            "swiss_schools_isb",
            "swiss_schools_iscs",
            "swiss_schools_isp",
            "swiss_schools_nae",
            "swiss_schools_zis",
            "tes",
        )
    },
    "backend/services/harvest_window.py",
    "backend/services/schools/producer.py",
    "backend/tasks/fetch_tasks.py",
    "backend/tasks/harvest_persist.py",
    "backend/tasks/pipeline_tasks.py",
    "backend/tasks/scraping_tasks.py",
    *{
        f"backend/services/{name}.py"
        for name in (
            "compliance",
            "circuit_breaker",
            "crawler_budget",
            "cursor_store",
            "data_normalizer",
            "deduplicator",
            "job_classifier",
            "job_repository",
            "job_service",
            "scraper_engine",
            "scraper_stealth",
            "source_health",
        )
    },
    "backend/models/source_compliance.py",
    "backend/models/source_cursor.py",
    "backend/models/source_health.py",
    "backend/utils/dates.py",
    "backend/utils/fetch_diagnostics.py",
    "backend/utils/http.py",
}
FORBIDDEN_SWISSJOB_DEPENDENCIES = {"beautifulsoup4", "playwright"}


def _literal_assignments(path: Path, names: set[str]) -> list[object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[object] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id in names for target in targets
        ):
            continue
        try:
            values.append(ast.literal_eval(node.value))
        except (TypeError, ValueError):
            continue
    return values


def _dict_assignment_keys(path: Path, name: str) -> set[str]:
    """Return string keys without evaluating non-literal dictionary values."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        return {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return set()


def _strings(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {item for item in value if isinstance(item, str)}
    if isinstance(value, (list, tuple, set, frozenset)):
        return {item for item in value if isinstance(item, str)}
    return set()


def discover_core_adapters(root: Path) -> set[str]:
    names: set[str] = set()
    provider_dir = root / "jobhunt_core/harvest/providers"
    for path in provider_dir.glob("*.py"):
        for value in _literal_assignments(
            # SOURCE_NAMES: one adapter module may own several sources (the
            # school watchlist is five boards sharing one provider class).
            path,
            {"SOURCE_NAME", "SOURCE_NAMES", "ENDPOINTS", "DOMAINS"},
        ):
            names.update(_strings(value))
    return names


def discover_swissjob_modules(root: Path) -> set[str]:
    names: set[str] = set()
    for directory in (root / "backend/providers", root / "backend/scrapers"):
        for path in directory.glob("*.py"):
            for value in _literal_assignments(path, {"SOURCE_NAME"}):
                names.update(_strings(value))
    return names


def discover_swissjob_registry(root: Path) -> set[str]:
    names: set[str] = set()
    for path, assignment in (
        (root / "backend/providers/__init__.py", "_PROVIDER_CLASSES"),
        (root / "backend/scrapers/__init__.py", "_SCRAPER_CLASSES"),
    ):
        if path.exists():
            names.update(_dict_assignment_keys(path, assignment))
    return names


def discover_portfolio_known_sources(root: Path) -> set[str]:
    path = root / "config.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "HarvestSource"
            for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Subscript) and isinstance(
            node.value.slice, ast.Tuple
        ):
            return {
                item.value
                for item in node.value.slice.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            }
    raise ValueError(f"HarvestSource literal not found in {path}")


def _compare(
    label: str, actual: set[str], expected: set[str], errors: list[str]
) -> None:
    if actual == expected:
        return
    errors.append(
        f"{label} mismatch: missing={sorted(expected - actual)}, "
        f"unexpected={sorted(actual - expected)}"
    )


def validate(manifest: dict, portfolio_root: Path | None) -> list[str]:
    errors: list[str] = []
    sources = manifest.get("sources")
    if manifest.get("schema_version") != 1 or not isinstance(sources, list):
        return ["manifest must have schema_version=1 and a sources list"]

    names = [source.get("name") for source in sources if isinstance(source, dict)]
    if len(names) != len(sources) or any(
        not isinstance(name, str) or not name for name in names
    ):
        errors.append("every source must have a non-empty string name")
        return errors
    if names != sorted(names):
        errors.append("sources must be sorted by name")
    if len(names) != len(set(names)):
        errors.append("source names must be unique")

    for source in sources:
        name = source["name"]
        state = source.get("state")
        target = source.get("target_owner")
        publishers = source.get("active_publishers")
        if state not in VALID_STATES:
            errors.append(f"{name}: invalid state {state!r}")
            continue
        if not isinstance(publishers, list) or len(publishers) != len(set(publishers)):
            errors.append(f"{name}: active_publishers must be a unique list")
            continue
        if state == "retired":
            if target is not None or publishers:
                errors.append(f"{name}: retired sources have no target or publishers")
        elif target != "core":
            errors.append(f"{name}: every non-retired source targets core")
        if state in {"core_primary", "local_removed"} and publishers != ["core"]:
            errors.append(f"{name}: core-owned states require core as sole publisher")
        if state == "local_pending" and not set(publishers).intersection(
            LOCAL_PUBLISHERS
        ):
            errors.append(f"{name}: local_pending requires a local publisher")
        if state == "core_shadow" and "core" in publishers:
            errors.append(f"{name}: core_shadow must not publish from core")
        if source.get("inventory_verified"):
            missing = VERIFIED_INVENTORY_FIELDS - source.keys()
            if missing:
                errors.append(f"{name}: verified inventory missing {sorted(missing)}")
                continue
            for field in (
                "aliases",
                "current_harvesters",
                "credentials",
                "last_success",
                "core_scope_ids",
            ):
                if not isinstance(source[field], list):
                    errors.append(f"{name}: {field} must be a list")
            harvesters = source["current_harvesters"]
            if isinstance(harvesters, list):
                harvester_products = {
                    item.get("product") for item in harvesters if isinstance(item, dict)
                }
                if harvester_products != set(publishers):
                    errors.append(
                        f"{name}: current_harvesters must match active_publishers"
                    )
            for field in ("transport", "external_identity", "deduplication"):
                if not isinstance(source[field], str) or not source[field]:
                    errors.append(f"{name}: {field} must be a non-empty string")
            if source["blocker"] is not None and not isinstance(source["blocker"], str):
                errors.append(f"{name}: blocker must be a string or null")

    ratchet = manifest.get("ratchet", {})
    core_owned = sum(
        source["state"] in {"core_primary", "local_removed"} for source in sources
    )
    local_pending = sum(source["state"] == "local_pending" for source in sources)
    local_publishers = sum(
        publisher in LOCAL_PUBLISHERS
        for source in sources
        for publisher in source["active_publishers"]
    )
    if core_owned < ratchet.get("min_core_owned_sources", 0):
        errors.append("ratchet regression: core-owned source count decreased")
    inventory_verified = sum(
        source.get("inventory_verified") is True for source in sources
    )
    if inventory_verified < ratchet.get("min_inventory_verified_sources", 0):
        errors.append("ratchet regression: verified inventory count decreased")
    if local_pending > ratchet.get("max_local_pending_sources", local_pending):
        errors.append("ratchet regression: local-pending source count increased")
    if local_publishers > ratchet.get("max_active_local_publishers", local_publishers):
        errors.append("ratchet regression: active local publisher count increased")

    expected_core = {source["name"] for source in sources if source.get("core_adapter")}
    expected_modules = {
        source["name"] for source in sources if source.get("swissjob_module")
    }
    expected_registry = {
        source["name"] for source in sources if source.get("swissjob_registry")
    }
    _compare("core adapters", discover_core_adapters(ROOT), expected_core, errors)
    _compare(
        "SwissJob modules", discover_swissjob_modules(ROOT), expected_modules, errors
    )
    _compare(
        "SwissJob registry", discover_swissjob_registry(ROOT), expected_registry, errors
    )
    provider_modules = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "backend/providers").glob("*.py")
    )
    if provider_modules:
        errors.append(f"retired SwissJob provider modules present: {provider_modules}")
    present_paths = sorted(
        path for path in FORBIDDEN_SWISSJOB_PATHS if (ROOT / path).exists()
    )
    if present_paths:
        errors.append(f"retired SwissJob harvest paths present: {present_paths}")

    requirements = (ROOT / "backend/requirements.txt").read_text(encoding="utf-8")
    installed_names = {
        line.split("==", 1)[0].strip().lower()
        for line in requirements.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    retired_dependencies = sorted(FORBIDDEN_SWISSJOB_DEPENDENCIES & installed_names)
    if retired_dependencies:
        errors.append(
            f"retired SwissJob harvest dependencies present: {retired_dependencies}"
        )
    dockerfile = (ROOT / "backend/Dockerfile").read_text(encoding="utf-8").lower()
    if "playwright install" in dockerfile:
        errors.append("retired Playwright browser installation present")
    compose = (ROOT / "deploy/nas/swissjob.configured.yml").read_text(encoding="utf-8")
    if "PLAYWRIGHT_BROWSERS_PATH" in compose:
        errors.append("retired Playwright browser environment present")

    if portfolio_root is not None:
        expected_portfolio = {
            source["reactportfolio_source_name"]
            for source in sources
            if source.get("reactportfolio_source_name")
        }
        _compare(
            "ReactPortfolio known sources",
            discover_portfolio_known_sources(portfolio_root),
            expected_portfolio,
            errors,
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--portfolio-root", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = validate(manifest, args.portfolio_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(
        json.dumps(
            {
                "sources": len(manifest["sources"]),
                "core_owned": sum(
                    source["state"] in {"core_primary", "local_removed"}
                    for source in manifest["sources"]
                ),
                "local_pending": sum(
                    source["state"] == "local_pending" for source in manifest["sources"]
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
