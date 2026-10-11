#!/usr/bin/env python3
"""Measure the affected NAS containers with the optimization audit protocol."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import statistics
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


DOCKER_BIN = "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker"
CONTAINERS = (
    "swissjob-core-api-r5",
    "swissjob-core-worker-r5",
    "swissjob-core-harvest-worker-r5",
    "swissjob-core-ops-worker-r5",
    "swissjob-core-capture-r5",
    "swissjob-backend",
    "swissjob-worker",
    "portfolio_backend",
    "portfolio_worker",
)
CAPTURE_CONTAINER = "swissjob-core-capture-r5"
MEMORY_FACTORS = {
    "B": 1 / (1024 * 1024),
    "KiB": 1 / 1024,
    "MiB": 1,
    "GiB": 1024,
    "TiB": 1024 * 1024,
}


def memory_mib(value: str) -> float:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)", value.strip())
    if match is None:
        raise ValueError(f"invalid memory value: {value}")
    amount, unit = match.groups()
    try:
        return float(amount) * MEMORY_FACTORS[unit]
    except KeyError as exc:
        raise ValueError(f"unsupported memory unit: {unit}") from exc


def parse_stats(output: str) -> dict[str, dict[str, float | int]]:
    rows: dict[str, dict[str, float | int]] = {}
    for line in output.splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        rows[raw["Name"]] = {
            "cpu_percent": float(raw["CPUPerc"].strip().removesuffix("%")),
            "memory_mib": memory_mib(raw["MemUsage"].split("/", 1)[0]),
            "pids": int(raw["PIDs"]),
        }
    return rows


def sample(host: str, containers: tuple[str, ...]) -> dict[str, dict[str, float | int]]:
    command = [
        DOCKER_BIN,
        "stats",
        "--no-stream",
        "--format",
        "{{json .}}",
        *containers,
    ]
    result = subprocess.run(
        ["ssh", host, shlex.join(command)],
        check=True,
        capture_output=True,
        text=True,
    )
    rows = parse_stats(result.stdout)
    missing = set(containers) - set(rows)
    extra = set(rows) - set(containers)
    if missing or extra:
        raise RuntimeError(
            f"unexpected container set: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return rows


def validate_capture_state(container_names: set[str], capture_retired: bool) -> None:
    capture_present = CAPTURE_CONTAINER in container_names
    if capture_retired and capture_present:
        raise RuntimeError(f"{CAPTURE_CONTAINER} still exists")
    if not capture_retired and not capture_present:
        raise RuntimeError(f"{CAPTURE_CONTAINER} is already absent")


def check_capture_state(host: str, capture_retired: bool) -> None:
    command = [DOCKER_BIN, "ps", "-a", "--format", "{{.Names}}"]
    result = subprocess.run(
        ["ssh", host, shlex.join(command)],
        check=True,
        capture_output=True,
        text=True,
    )
    validate_capture_state(set(result.stdout.splitlines()), capture_retired)


def measure(host: str, count: int, interval: float, capture_retired: bool) -> dict:
    check_capture_state(host, capture_retired)
    containers = tuple(
        name for name in CONTAINERS if not capture_retired or name != CAPTURE_CONTAINER
    )
    started = datetime.now(timezone.utc)
    samples = []
    for index in range(count):
        samples.append(sample(host, containers))
        if index + 1 < count:
            time.sleep(interval)
    ended = datetime.now(timezone.utc)

    measured = {}
    for name in containers:
        cpu = [row[name]["cpu_percent"] for row in samples]
        memory = [row[name]["memory_mib"] for row in samples]
        pids = [row[name]["pids"] for row in samples]
        measured[name] = {
            "cpu_percent": cpu,
            "memory_mib": memory,
            "pids": pids,
            "median_cpu_percent": statistics.median(cpu),
            "median_memory_mib": statistics.median(memory),
            "median_pids": statistics.median(pids),
        }

    return {
        "sample_window": {
            "started_at": started.isoformat().replace("+00:00", "Z"),
            "ended_at": ended.isoformat().replace("+00:00", "Z"),
            "samples": count,
            "interval_seconds": interval,
            "method": "docker stats --no-stream over SSH",
            "capture_retired": capture_retired,
        },
        "containers": measured,
        "aggregate_medians": {
            "container_count": len(measured),
            "cpu_percent_sum": round(
                sum(row["median_cpu_percent"] for row in measured.values()), 2
            ),
            "memory_mib_sum": round(
                sum(row["median_memory_mib"] for row in measured.values()), 2
            ),
            "pids_sum": sum(row["median_pids"] for row in measured.values()),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="nas")
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--interval", type=float, default=8)
    parser.add_argument("--capture-retired", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 1 or args.interval < 0:
        parser.error("--samples must be positive and --interval non-negative")
    result = measure(args.host, args.samples, args.interval, args.capture_retired)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
