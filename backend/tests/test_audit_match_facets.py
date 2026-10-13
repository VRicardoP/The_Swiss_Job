from types import SimpleNamespace

from scripts.audit_match_facets import aggregate_comparison


def _item(ref, school=False):
    return {
        "match": SimpleNamespace(job_hash=ref),
        "school": object() if school else None,
    }


def test_aggregate_comparison_reports_only_count_and_identity_differences():
    facets = {
        "total": 3,
        "watchlist_count": 1,
        "categories": [
            {"id": "A", "count": 2},
            {"id": "otros", "count": 1},
        ],
    }
    report = aggregate_comparison(
        facets,
        [_item("one", school=True), _item("two"), _item("three")],
        {"A": {"one", "two"}, "otros": {"three"}},
        {"one"},
    )
    assert report == {
        "facet_total": 3,
        "baseline_total": 3,
        "category_union_total": 3,
        "category_duplicate_memberships": 0,
        "category_identity_mismatch": 0,
        "category_count_mismatch_l1": 0,
        "facet_watchlist": 1,
        "visible_watchlist": 1,
        "watchlist_identity_mismatch": 0,
    }
