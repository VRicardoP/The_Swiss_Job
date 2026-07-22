"""ADR-08: el paquete NO importa internals del backend legacy (chequeo estático)."""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]

# Módulos top-level del backend legacy (viven en backend/, fuera de este paquete).
# Lista ampliada (auditoría Opus): también utils/schemas/main/crud/api/scripts.
FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(?:services|models|tasks|routers|config|database|celery_app|core|providers"
    r"|scrapers|utils|schemas|main|crud|api|scripts|logging_config|conftest)\b"
)


def test_no_legacy_imports():
    offenders = []
    for py in PKG.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.match(line):
                offenders.append(f"{py.relative_to(PKG)}:{i}: {line.strip()}")
    assert not offenders, "Imports legacy prohibidos:\n" + "\n".join(offenders)
