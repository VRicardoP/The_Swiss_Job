from datetime import datetime, timezone


def test_native_nav_date_is_read_from_raw_envelope_without_rewriting():
    from jobhunt_core.harvest.admission import publication_date, admission_window
    from jobhunt_core.harvest.providers import get_provider
    value = {"_source": {"published": "2026-09-19T12:00:00Z"}}
    assert publication_date("nav_arbeidsplassen", value) == datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    assert publication_date("nav_arbeidsplassen", {"_source": None}) is None
    assert admission_window("nav_arbeidsplassen", {"admission_window_days": 7}).days == 7
    assert get_provider("nav_arbeidsplassen").name == "nav_arbeidsplassen"
    assert get_provider("thehub").name == "thehub"
