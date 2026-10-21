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
def test_toxic_identity_is_counted_and_preserves_valid_neighbor(row):
    result, _ = fetch(pages=[{"items": [raw(), row]}])
    assert len(result.listings) == 1
    # DT-148 E1: el ilegible se cuenta en el cursor, el barrido sigue completo.
    assert result.complete and result.error is None
    assert result.next_cursor["invalid"] == 1


def test_native_registry_and_admission():
    from jobhunt_core.harvest.admission import admission_window
    from jobhunt_core.harvest.providers import get_provider
    from jobhunt_core.harvest.registry import ensure_handler

    for name in ("ostjob", "zentraljob"):
        assert get_provider(name).name == name
        assert ensure_handler(name)
        assert admission_window(name, {"admission_window_days": 7}).days == 7
