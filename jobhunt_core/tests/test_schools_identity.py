"""Verified source aliases, never fuzzy merging of school names."""

from jobhunt_core.schools import school_key


def test_existing_consumers_share_school_not_monitor_identity():
    for local, canonical in {
        "beau": "beausoleil_villars",
        "beausoleil_inline": "beausoleil_villars",
        "isb": "isb_basel",
        "isg": "ecolint_geneva",
        "iscs": "iscs_zug",
        "zis": "zis_zurich",
        "vis": "verbier_vis",
    }.items():
        assert school_key("CH", local) == school_key("ch", canonical)
        assert school_key("DE", local) != school_key("CH", canonical)
    assert school_key("CH", "unknown") == "CH:unknown"


def test_school_source_handler_can_be_restored_in_a_fresh_worker():
    from jobhunt_core.harvest import normalize, registry
    from jobhunt_core.school_ingest import SOURCE_NAME

    normalize._NORMALIZERS.pop(SOURCE_NAME, None)
    assert registry.ensure_handler(SOURCE_NAME)
    assert (
        normalize.normalize_offer(SOURCE_NAME, {"title": "IT", "company": "School"})[
            "title"
        ]
        == "IT"
    )
