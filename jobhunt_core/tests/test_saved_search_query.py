"""Fail-closed filters and SQL binding, without running production searches."""

import pytest
from pydantic import ValidationError

from jobhunt_core.saved_search_query import matching_query


@pytest.mark.parametrize("filters", [
    None, [], "python", {"source": 123}, {"canton": ["ZH"]},
    {"language": {"$ne": None}}, {"remote_only": "false"},
    {"salary_min": True}, {"salary_max": -1}, {"salary_min": "50"},
    {"unknown_filter": True}, {"keywords": "python"},
])
def test_invalid_filters_never_become_unfiltered_searches(filters):
    with pytest.raises(ValidationError):
        matching_query(filters)


def test_filter_values_are_bound_not_interpolated():
    hostile = "x' OR true --"
    sql, params = matching_query({"q": hostile, "source": hostile, "language": hostile})
    assert hostile not in str(sql)
    assert params == {"q": hostile, "sources": [hostile], "language": hostile}


def test_zero_salary_bounds_are_not_ignored():
    sql, params = matching_query({"salary_min": 0, "salary_max": 0})
    assert params == {"salary_min": 0, "salary_max": 0}
    assert ">= :salary_min" in str(sql) and "<= :salary_max" in str(sql)
