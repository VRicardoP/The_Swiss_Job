#!/usr/bin/env python3
"""Split an existing configured Core worker without exposing its environment."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ALL_QUEUES = "core.default,core.harvest,core.embedding,core.matching,core.notifications"


def _worker(block: str, service: str, container: str, queues: str, beat: bool) -> str:
    clone = block.replace("  core-worker:\n", f"  {service}:\n", 1)
    clone = clone.replace("    mem_limit: 2g\n", "    mem_limit: 768m\n", 1)
    clone = clone.replace("swissjob-core-worker-r5", container)
    clone = clone.replace("        - core-worker\n", f"        - {service}\n")
    clone = clone.replace(ALL_QUEUES, queues, 1)
    if not beat:
        clone = clone.replace(
            "    - --concurrency=1\n"
            "    - -B\n"
            "    - -s\n"
            "    - /tmp/celerybeat-schedule\n",
            "    - --concurrency=1\n",
            1,
        )
    return re.sub(
        r"    volumes:\n"
        r"    - type: volume\n"
        r"      source: swissjob-r5_core-hf-cache\n"
        r"      target: /tmp/hf-cache\n"
        r"      read_only: false\n",
        "    volumes: []\n",
        clone,
        count=1,
    )


def split(path: Path) -> bool:
    data = path.read_text()
    if "  core-harvest-worker:\n" in data and "  core-ops-worker:\n" in data:
        return False
    start = data.index("  core-worker:\n")
    end = data.index("  core-capture:\n", start)
    original = data[start:end]
    if ALL_QUEUES not in original or "    - --concurrency=1\n" not in original:
        raise ValueError("core-worker no tiene la topología esperada")
    model = original.replace(ALL_QUEUES, "core.embedding,core.matching", 1)
    model = model.replace(
        "    - --concurrency=1\n"
        "    - -B\n"
        "    - -s\n"
        "    - /tmp/celerybeat-schedule\n",
        "    - --concurrency=1\n",
        1,
    )
    harvest = _worker(
        original,
        "core-harvest-worker",
        "swissjob-core-harvest-worker-r5",
        "core.harvest",
        False,
    )
    ops = _worker(
        original,
        "core-ops-worker",
        "swissjob-core-ops-worker-r5",
        "core.default,core.notifications",
        True,
    )
    path.write_text(data[:start] + model + harvest + ops + data[end:])
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"uso: {sys.argv[0]} COMPOSE")
    split(Path(sys.argv[1]))
