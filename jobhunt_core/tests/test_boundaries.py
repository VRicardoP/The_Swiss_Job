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


def test_wal_retention_single_definition():
    """G1-P3-8: UNA sola definición de «WAL retenido por el slot» (restart_lsn
    — los bytes que el slot impide reciclar) contra el único umbral de 2 GiB.
    El healthcheck de capture medía COALESCE(confirmed_flush_lsn, …) y podía
    dar verde mientras la alerta del gate (restart_lsn) media más: señales
    contradictorias sobre el mismo riesgo de disco. Chequeo estático: ambos
    usan la constante compartida y nadie reintroduce una query propia."""
    import inspect

    from jobhunt_core.shadow import capture, gate

    assert "restart_lsn" in capture.SLOT_RETAINED_BYTES_SQL
    assert "confirmed_flush_lsn" not in capture.SLOT_RETAINED_BYTES_SQL
    for fn in (capture.health_check, gate.check_slot_health):
        src = inspect.getsource(fn)
        assert "SLOT_RETAINED_BYTES_SQL" in src, fn.__qualname__
        assert "confirmed_flush_lsn" not in src, fn.__qualname__


def test_rollback_replay_siempre_con_cota_de_readiness():
    """G1 H-13: rollback_replay construía ShadowCapture sin ready_max_retries
    (None = espera INDEFINIDA) DESPUÉS de destruir la sombra — si el legacy no
    respondía, la herramienta se colgaba para siempre. Chequeo estático: la
    firma expone una cota con default finito y la construcción la pasa."""
    import inspect

    from jobhunt_core.shadow import gate

    sig = inspect.signature(gate.rollback_replay)
    default = sig.parameters["ready_max_retries"].default
    assert isinstance(default, int) and default > 0
    src = inspect.getsource(gate.rollback_replay)
    assert "ready_max_retries=ready_max_retries" in src
