"""Pone `tools/core` en `sys.path` para el test de `train_cross_encoder`.

DT-148 B5: la herramienta salió de `jobhunt_core/` —no es producción y viajaba
en la imagen—. Su test la importa por su nombre, que es también como la importa
quien ejecuta `python tools/core/train_cross_encoder.py`.

El test de `dev_eval` NO está aquí: dos tests del núcleo construyen sobre sus
helpers, así que se quedó en `jobhunt_core/tests/` y la costura hacia la
herramienta vive en `jobhunt_core/tests/dev_tools.py`. Por eso este conftest no
necesita el aislamiento de base del núcleo: lo único que queda aquí no la toca.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
