# tools/core — herramientas de DESARROLLO del núcleo

Dos programas de línea de comandos que **no corren en producción** y que hasta
DT-148 B5 (2026-10-21) viajaban dentro de `jobhunt_core/`, y por tanto dentro
de la imagen del núcleo:

| Fichero | Qué hace | Cómo se ejecutaba | Cómo se ejecuta ahora |
|---|---|---|---|
| `dev_eval.py` | Mide el nDCG del corpus de desarrollo y sella el universo evaluado | `python -m jobhunt_core.dev_eval …` | `python tools/core/dev_eval.py …` |
| `train_cross_encoder.py` | Entrena el cross-encoder y escribe su manifiesto reproducible | `python -m jobhunt_core.train_cross_encoder …` | `python tools/core/train_cross_encoder.py …` |

Ninguno estaba en `celery_app.conf.include` ni lo importaba un solo módulo de
producción: su único uso es `python -m` a mano, citado en las actas de la
Fase E. P3 de DT-145 conservó a propósito `migrate.py`, Alembic y
`erasure_restore.py`; de estos dos no dijo nada y sobrevivieron sin decisión.

**Siguen importando `jobhunt_core`** (`from jobhunt_core import matching, …`) y
eso no cambia: el paquete está instalado en el contenedor. Lo que cambia es la
dirección de la dependencia — ahora las herramientas dependen del núcleo y el
núcleo no sabe que existen.

## Tests

`tests/` lleva **sólo** el de `train_cross_encoder`, que no toca la base.

El de `dev_eval` se quedó en `jobhunt_core/tests/`: dos tests del NÚCLEO
—`test_integration_cross_encoder.py` y `test_review_evidence_20260907.py`—
construyen sobre sus helpers, porque `dev_eval` es el instrumento de medida del
gate de matching. La costura hacia aquí vive en `jobhunt_core/tests/dev_tools.py`,
declarada una sola vez. El inventario que ordenó este movimiento no lo había
visto: su búsqueda de consumidores excluía los tests.

Se ejecutan con la raíz del repo montada, como el resto:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm -v "$PWD:/repo" -w /repo core-migrate \
  python -m pytest tools/core/tests
```

`conftest.py` añade este directorio a `sys.path` para que el test importe
`train_cross_encoder` por su nombre, igual que haría quien lo ejecuta a mano
desde aquí.
