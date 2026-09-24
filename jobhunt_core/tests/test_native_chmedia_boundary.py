import pytest

from jobhunt_core.tests.test_native_chmedia import fetch, raw


@pytest.mark.parametrize(
    "row",
    [
        raw(id="\ud800"),
        raw(id=True),
        raw(id=-1),
    ],
)
def test_toxic_identity_is_partial_and_preserves_valid_neighbor(row):
    result, _ = fetch(pages=[{"items": [raw(), row]}])
    assert len(result.listings) == 1
    assert not result.complete and result.error == "invalid_chmedia_items"


def test_native_registry_and_admission():
    from jobhunt_core.harvest.admission import admission_window
    from jobhunt_core.harvest.providers import get_provider
    from jobhunt_core.harvest.registry import ensure_handler

    for name in ("ostjob", "zentraljob"):
        assert get_provider(name).name == name
        assert ensure_handler(name)
        assert admission_window(name, {"admission_window_days": 7}).days == 7
