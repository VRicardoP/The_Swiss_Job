"""`language` viaja desde la canónica hasta el DTO servido.

El campo existía en el contenido canónico (lo escribe el proyector, y los
metadatos de búsqueda lo declaran) pero NADIE lo transportaba: `VacancyDTO` no
lo declaraba y `_vacancy_dtos` no lo leía. El consumidor lo deducía por título
en cada oferta servida — 50,1 ms por oferta, 1.800 por petición (punto 5,
2026-09-22). Una revalidación externa demostró que rellenarlo en los
normalizadores nativos no habría arreglado nada: se perdía aguas abajo.

Un valor inutilizable se sirve como AUSENTE en vez de tumbar la página entera:
la canónica es JSONB de productores heterogéneos y el idioma es un indicador,
no la identidad de la oferta.
"""

import pytest

from jobhunt_core.api import v1
from jobhunt_core.tests import test_integration_api as api

db = api.db
pytestmark = api.pytestmark


def test_language_viaja_de_la_canonica_al_dto(db):
    factory, created = db
    term, vacancies, token = api._seed_catalog(factory, created, n=3)
    api._enrich_content(factory, vacancies[term + "v0"], {"language": "de"})
    # Forma inválida: el productor escribió algo que no es un código.
    api._enrich_content(factory, vacancies[term + "v1"], {"language": 7})
    # v2 se queda SIN el campo: ausente sigue siendo un estado legítimo.

    items = api._api(factory, f"/v1/vacancies?q={term}", token=token).json()["items"]
    por_titulo = {i["title"]: i for i in items}
    assert len(por_titulo) == 3

    assert por_titulo[term + "v0"]["language"] == "de"
    assert por_titulo[term + "v1"]["language"] is None, (
        "un tipo inválido debe servirse ausente"
    )
    assert por_titulo[term + "v2"]["language"] is None


def test_language_se_normaliza(db):
    factory, created = db
    term, vacancies, token = api._seed_catalog(factory, created, n=1)
    api._enrich_content(factory, vacancies[term + "v0"], {"language": "  FR  "})
    items = api._api(factory, f"/v1/vacancies?q={term}", token=token).json()["items"]
    assert items[0]["language"] == "fr"


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("de", "de"),
        ("  EN ", "en"),
        ("", None),
        ("   ", None),
        (None, None),
        (7, None),
        (["de"], None),
        ({"code": "de"}, None),
        (True, None),
        # T11 §5: además de ser cadena, tiene que PARECER un código de idioma.
        # Lo de abajo llegaba tal cual al consumidor como «idioma de la oferta».
        ("deutsch", None),
        ("Softwareentwickler (m/w/d)", None),
        ("unknown", None),
        ("<p>de</p>", None),
        ("d", None),
        ("de_CH", None),  # el separador canónico es el guion, no el subrayado
        ("pt-BR", "pt-br"),  # regional SÍ, y normalizado
    ],
)
def test_canonical_language_acota_lo_utilizable(valor, esperado):
    """Unidad de la guarda, sin base de datos: lo que no sea una cadena con
    contenido es ausencia, nunca una excepción."""
    assert v1._canonical_language(valor) == esperado
