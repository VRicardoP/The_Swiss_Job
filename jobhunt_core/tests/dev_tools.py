"""Costura única hacia las herramientas de desarrollo de `tools/core`.

DT-148 B5 (2026-10-21): `dev_eval.py` y `train_cross_encoder.py` salieron de
`jobhunt_core/` para que no viajen en la imagen de producción. Ningún módulo de
producción los importaba —eso es lo que permitió sacarlos—, pero DOS tests del
núcleo sí dependen de `dev_eval`, y el inventario que ordenó el movimiento no
lo vio porque su búsqueda de consumidores excluía los tests:

- `test_integration_cross_encoder.py` construye sobre cinco helpers de
  `test_integration_dev_eval.py`, que a su vez llaman a `evaluate_dev`;
- `test_review_evidence_20260907.py` reproduce con `dev_eval` la regresión del
  sello del universo.

`dev_eval` es el INSTRUMENTO DE MEDIDA del gate de matching, así que esos tests
están en su sitio. Lo que no debe repetirse es el `sys.path` en cada uno: vive
aquí, declarado una sola vez y con su motivo.
"""

import os
import sys

# `jobhunt_core/tests/dev_tools.py` → raíz del proyecto → `tools/core`. Dentro
# del contenedor la raíz es `/app` y el perfil de DESARROLLO monta `./tools`
# ahí de solo lectura (`docker-compose.dev.yml`), igual que ya montaba
# `./scripts` para los tests de contratos operativos. La imagen operativa NO
# las contiene, que es justo lo que este movimiento buscaba.
_TOOLS_CORE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tools",
    "core",
)
if _TOOLS_CORE not in sys.path:
    sys.path.insert(0, _TOOLS_CORE)

import dev_eval  # noqa: E402

__all__ = ["dev_eval"]
