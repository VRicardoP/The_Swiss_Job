"""T11 §4 — las cadencias del beat tienen suelo.

Eran `int` a secas. Un `0` o un `5` en el entorno —un dedo, una variable
heredada de otro despliegue— convertía una cadencia de observabilidad en un
martillo contra dos núcleos: el beat encolaría la tarea cada segundo y el
worker no haría otra cosa. No había nada que lo impidiera ni que lo dijera.

El suelo es 60 s. No es una opinión sobre cuál debe ser la cadencia (eso lo
dice el default de cada una), sino sobre cuál NO puede serlo.
"""

import pytest
from pydantic import ValidationError

from jobhunt_core.config import CoreSettings as Settings

CADENCIAS = [
    "CORE_SHADOW_OUTBOX_SAMPLE_EVERY_S",
    "CORE_SHADOW_SLOT_HEALTH_EVERY_S",
    "CORE_SHADOW_PROJECT_EVERY_S",
    "CORE_DELIVERY_DISPATCH_EVERY_S",
    "CORE_IDEMPOTENCY_PURGE_EVERY_S",
    "CORE_HARVEST_HEALTH_EVERY_S",
]


@pytest.mark.parametrize("nombre", CADENCIAS)
@pytest.mark.parametrize("valor", [0, 1, 59, -300])
def test_una_cadencia_por_debajo_del_suelo_no_arranca(nombre, valor):
    with pytest.raises(ValidationError):
        Settings(**{nombre: valor})


@pytest.mark.parametrize("nombre", CADENCIAS)
def test_el_suelo_no_estorba_a_lo_legitimo(nombre):
    """Control: la cota no puede rechazar una cadencia razonable ni mover el
    default. Una cota que cambia el valor efectivo es una regresión disfrazada."""
    assert Settings(**{nombre: 60}).model_dump()[nombre] == 60
    assert Settings(**{nombre: 86400}).model_dump()[nombre] == 86400
    assert Settings().model_dump()[nombre] == Settings.model_fields[nombre].default


def test_la_fraccion_de_alerta_de_fechas_es_una_fraccion():
    """T11 §3: un ratio fuera de [0,1] es una alerta que no salta nunca (>1) o
    que salta siempre (<0). Las dos formas de apagar la vigilancia en silencio."""
    for malo in (-0.1, 1.5, 2):
        with pytest.raises(ValidationError):
            Settings(CORE_HARVEST_MISSING_DATE_ALERT_RATIO=malo)
    assert (
        Settings(
            CORE_HARVEST_MISSING_DATE_ALERT_RATIO=0.9
        ).CORE_HARVEST_MISSING_DATE_ALERT_RATIO
        == 0.9
    )
