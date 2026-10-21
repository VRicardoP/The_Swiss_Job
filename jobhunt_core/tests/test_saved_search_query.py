"""Fail-closed filters and SQL binding, without running production searches."""

import pytest
from pydantic import ValidationError

from jobhunt_core.saved_search_query import matching_query


@pytest.mark.parametrize(
    "filters",
    [
        None,
        [],
        "python",
        {"source": 123},
        {"canton": ["ZH"]},
        {"language": {"$ne": None}},
        {"remote_only": "false"},
        {"salary_min": True},
        {"salary_max": -1},
        {"salary_min": "50"},
        {"unknown_filter": True},
        {"keywords": "python"},
    ],
)
def test_invalid_filters_never_become_unfiltered_searches(filters):
    with pytest.raises(ValidationError):
        matching_query(filters)


def test_filter_values_are_bound_not_interpolated():
    hostile = "x' OR true --"
    sql, params = matching_query({"q": hostile, "source": hostile, "language": hostile})
    assert hostile not in str(sql)
    assert params == {"q": hostile, "sources": [hostile], "language": hostile}


def test_location_is_a_bound_substring_and_stays_indexable():
    """DT-148 A18-01: `location` filtra como subcadena ci, porque el campo es
    TEXTO LIBRE («8000 Zürich», «Zürich, ZH», «Remote job») y una igualdad no
    casaría ninguno de los tres.

    Y se escribe con `LIKE`, no con `position()`: la diferencia no es de estilo.
    Un índice trgm (`core0057`) sirve `LIKE` y NO sirve `position()`, y medido
    en producción eso era 86 s contra un doble Seq Scan sobre 227.053
    revisiones. Si alguien vuelve a `position()`, el filtro sigue dando el
    resultado correcto y tardando minuto y medio — un fallo que sólo se ve
    cronometrando. Por eso se fija aquí la FORMA.
    """
    sql, params = matching_query({"location": "  Zürich  "})
    assert params == {"location": "%Zürich%"}  # recortado y enlazado
    assert "Zürich" not in str(sql)  # jamás interpolado
    assert "LIKE lower(:location)" in str(sql)
    assert "position(" not in str(sql)


def test_location_metacharacters_are_literal_not_wildcards():
    """El contrato es «contiene este texto», no «este patrón». Sin escapar,
    un `%` del usuario casaría CUALQUIER ubicación y el filtro parecería
    aplicarse mientras no filtra nada."""
    _sql, params = matching_query({"location": "100% remote_work"})
    assert params == {"location": "%100\\% remote\\_work%"}


def test_an_empty_location_does_not_filter():
    """Cadena vacía o de espacios = «no filtrar», nunca un substring vacío que
    casa TODO mientras parece un filtro activo."""
    for vacia in ("", "   "):
        _sql, params = matching_query({"location": vacia})
        assert "location" not in params, vacia


def test_zero_salary_bounds_are_not_ignored():
    sql, params = matching_query({"salary_min": 0, "salary_max": 0})
    assert params == {"salary_min": 0, "salary_max": 0}
    assert ">= :salary_min" in str(sql) and "<= :salary_max" in str(sql)
