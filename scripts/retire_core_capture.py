#!/usr/bin/env python3
"""Transform the live Core Compose for the two capture-retirement phases.

The file may contain production secrets, so this script is designed to run on
the NAS through the already-loaded Core image with the Compose directory bind
mounted. It preserves the text and only changes the capture-specific lines.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


CORE_SERVICES = {
    "core-api",
    "core-worker",
    "core-harvest-worker",
    "core-ops-worker",
}
CAPTURE_SERVICE = "core-capture"
SLOT_KEY = "CORE_CAPTURE_SLOT"
ENABLED_KEY = "CORE_CAPTURE_ENABLED"
_SERVICE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
_ENV_KEY = re.compile(r"^      ([A-Za-z0-9_]+):\s*(.*?)\s*$")


def _service_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    try:
        services_start = lines.index("services:\n") + 1
    except ValueError as exc:
        raise ValueError("compose sin bloque services") from exc
    services_end = next(
        (
            index
            for index in range(services_start, len(lines))
            if lines[index].strip()
            and not lines[index][0].isspace()
        ),
        len(lines),
    )
    starts = [
        (index, match.group(1))
        for index in range(services_start, services_end)
        if (match := _SERVICE.match(lines[index]))
    ]
    return {
        name: (
            start,
            starts[pos + 1][0] if pos + 1 < len(starts) else services_end,
        )
        for pos, (start, name) in enumerate(starts)
    }


def _key_occurrences(
    lines: list[str], key: str
) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for service, (start, end) in _service_ranges(lines).items():
        for index in range(start, end):
            match = _ENV_KEY.match(lines[index])
            if match and match.group(1) == key:
                value = match.group(2).strip().strip("'\"").lower()
                found.append((service, index, value))
    return found


def _require_live_shape(lines: list[str]) -> None:
    services = set(_service_ranges(lines))
    missing = (CORE_SERVICES | {CAPTURE_SERVICE}) - services
    if missing:
        raise ValueError(
            f"compose sin servicios de captura esperados: {sorted(missing)}"
        )
    slots = {service for service, _, _ in _key_occurrences(lines, SLOT_KEY)}
    expected = CORE_SERVICES | {CAPTURE_SERVICE}
    if slots != expected:
        raise ValueError(
            f"{SLOT_KEY} en servicios {sorted(slots)}, esperados {sorted(expected)}"
        )


def _flag_is_disabled_only_in_ops(lines: list[str]) -> bool:
    flags = _key_occurrences(lines, ENABLED_KEY)
    return (
        len(flags) == 1
        and flags[0][0] == "core-ops-worker"
        and flags[0][2] == "false"
    )


def disable_capture_schedule(data: str) -> str:
    lines = data.splitlines(keepends=True)
    _require_live_shape(lines)
    flags = _key_occurrences(lines, ENABLED_KEY)
    if flags:
        if not _flag_is_disabled_only_in_ops(lines):
            summary = [(service, value) for service, _, value in flags]
            raise ValueError(f"{ENABLED_KEY} inesperado: {summary}")
        return data

    start, end = _service_ranges(lines)["core-ops-worker"]
    try:
        environment = lines.index("    environment:\n", start, end)
    except ValueError as exc:
        raise ValueError("core-ops-worker sin bloque environment") from exc
    lines.insert(environment + 1, f"      {ENABLED_KEY}: 'false'\n")
    changed = "".join(lines)
    if not _flag_is_disabled_only_in_ops(changed.splitlines(keepends=True)):
        raise ValueError(f"no se pudo fijar {ENABLED_KEY} sólo en core-ops-worker")
    return changed


def finalize_capture_removal(data: str) -> str:
    lines = data.splitlines(keepends=True)
    services = set(_service_ranges(lines))
    slots = _key_occurrences(lines, SLOT_KEY)
    if (
        CAPTURE_SERVICE not in services
        and not slots
        and CORE_SERVICES <= services
        and _flag_is_disabled_only_in_ops(lines)
    ):
        return data

    _require_live_shape(lines)
    if not _flag_is_disabled_only_in_ops(lines):
        raise ValueError(
            f"{ENABLED_KEY} debe estar false sólo en core-ops-worker antes de finalizar"
        )

    start, end = _service_ranges(lines)[CAPTURE_SERVICE]
    lines = lines[:start] + lines[end:]
    lines = [
        line
        for line in lines
        if not (
            (match := _ENV_KEY.match(line))
            and match.group(1) == SLOT_KEY
        )
    ]

    services = set(_service_ranges(lines))
    if CAPTURE_SERVICE in services or not CORE_SERVICES <= services:
        raise ValueError("topología final de servicios Core inválida")
    if _key_occurrences(lines, SLOT_KEY):
        raise ValueError(f"quedan referencias a {SLOT_KEY}")
    if not _flag_is_disabled_only_in_ops(lines):
        raise ValueError(f"{ENABLED_KEY} dejó de estar acotado a core-ops-worker")
    return "".join(lines)


def transform(path: Path, phase: str) -> bool:
    original = path.read_text()
    changed = (
        disable_capture_schedule(original)
        if phase == "disable"
        else finalize_capture_removal(original)
    )
    if changed == original:
        return False
    path.write_text(changed)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("compose", type=Path)
    parser.add_argument("phase", choices=("disable", "finalize"))
    args = parser.parse_args()
    changed = transform(args.compose, args.phase)
    print(f"{args.phase}: {'changed' if changed else 'already applied'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
