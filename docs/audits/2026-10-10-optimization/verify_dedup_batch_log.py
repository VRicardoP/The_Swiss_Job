#!/usr/bin/env python3
"""Validate one post-deploy dedup chain from core-ops-worker logs."""

import ast
import json
import re
import sys


BATCH_MARKER = "dedup_scan_batch: "
ERROR_MARKERS = (
    "dedup_scan fragment failed",
    "SoftTimeLimitExceeded",
    "ERROR/",
    "CRITICAL/",
    "Traceback (most recent call last)",
)
DURATION_RE = re.compile(
    r"Task jobhunt\.maintenance\.dedup_scan\[[^]]+\] "
    r"succeeded in ([0-9.]+)s:"
)


def main() -> int:
    raw = sys.stdin.read()
    failures = [f"log_contains:{marker}" for marker in ERROR_MARKERS if marker in raw]
    batches = []
    parse_errors = 0
    for line in raw.splitlines():
        if BATCH_MARKER not in line:
            continue
        try:
            batches.append(ast.literal_eval(line.split(BATCH_MARKER, 1)[1]))
        except (SyntaxError, ValueError):
            parse_errors += 1

    if parse_errors:
        failures.append(f"unparsed_batches:{parse_errors}")
    if not batches:
        failures.append("no_dedup_batches")

    partial = [batch for batch in batches if batch.get("complete") is False]
    complete = [batch for batch in batches if batch.get("complete") is True]
    if not partial:
        failures.append("no_partial_batch")
    if len(complete) != 1:
        failures.append(f"terminal_batches:{len(complete)}")

    for index, batch in enumerate(batches):
        scanned = batch.get("escaneadas")
        if not isinstance(scanned, int) or not 0 <= scanned <= 250:
            failures.append(f"batch_{index}:invalid_size")
        if batch.get("complete") is False:
            if batch.get("status") != "partial" or scanned != 250:
                failures.append(f"batch_{index}:invalid_partial")
            if {"candidatos_exactos_intra", "candidatos_lexicos"} & set(batch):
                failures.append(f"batch_{index}:early_final_arms")
            cursor = batch.get("next_cursor")
            if not isinstance(cursor, dict) or set(cursor) != {
                "created_at",
                "vacancy_id",
            }:
                failures.append(f"batch_{index}:invalid_cursor")
        elif batch.get("complete") is True:
            if batch.get("status") not in {"ok", "sin_modelo"}:
                failures.append(f"batch_{index}:invalid_terminal_status")
            if batch.get("next_cursor") is not None:
                failures.append(f"batch_{index}:terminal_cursor")
            if batch.get("status") == "ok" and not {
                "candidatos_exactos_intra",
                "candidatos_lexicos",
            }.issubset(batch):
                failures.append(f"batch_{index}:missing_final_arms")
        else:
            failures.append(f"batch_{index}:missing_complete")

    durations = [float(value) for value in DURATION_RE.findall(raw)]
    if batches and len(durations) != len(batches):
        failures.append(
            f"duration_count:{len(durations)}_for_{len(batches)}_batches"
        )
    if any(duration >= 1800 for duration in durations):
        failures.append("fragment_reached_soft_limit")

    result = {
        "batches": len(batches),
        "partial_batches": len(partial),
        "terminal_batches": len(complete),
        "scanned_total": sum(
            batch.get("escaneadas", 0)
            for batch in batches
            if isinstance(batch.get("escaneadas"), int)
        ),
        "new_candidates": sum(
            batch.get("candidatos_nuevos", 0)
            for batch in batches
            if isinstance(batch.get("candidatos_nuevos"), int)
        ),
        "max_fragment_seconds": max(durations) if durations else None,
        "failures": failures,
    }
    print(json.dumps(result, sort_keys=True))
    return int(bool(failures))


raise SystemExit(main())
