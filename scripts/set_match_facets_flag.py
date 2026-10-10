#!/usr/bin/env python3
"""Set the existing BFF facet flag in a configured compose without exposing it."""

from __future__ import annotations

import re
import sys
from pathlib import Path


KEY = "CORE_MATCH_FACETS_ENABLED"


def set_enabled(path: Path, enabled: bool) -> bool:
    data = path.read_text()
    wanted = "true" if enabled else "false"
    pattern = re.compile(rf"^(      {KEY}: ')[^']+(')$", re.MULTILINE)
    matches = pattern.findall(data)
    if len(matches) != 1:
        raise ValueError(f"se esperaba exactamente un {KEY}; encontrados {len(matches)}")
    changed = pattern.sub(rf"\g<1>{wanted}\g<2>", data, count=1)
    if changed == data:
        return False
    path.write_text(changed)
    return True


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[2] not in {"true", "false"}:
        raise SystemExit(f"uso: {sys.argv[0]} COMPOSE true|false")
    set_enabled(Path(sys.argv[1]), sys.argv[2] == "true")
