"""API /v1 (A-09): unit sin BD — cursor opaco y ETag."""

import uuid
from decimal import Decimal

import pytest

from jobhunt_core.api import v1
from jobhunt_core.api.deps import ApiError


def test_cursor_roundtrip_exact():
    vid = uuid.uuid4()
    cur = v1.encode_cursor(Decimal("87.50"), vid)
    score, vid2 = v1.decode_cursor(cur)
    assert (score, vid2) == (Decimal("87.50"), vid)  # sin pérdida (NUMERIC)


def test_cursor_invalid_is_contract_error():
    import base64

    bads = ["no-base64!", "bm9waXBl"]
    # Auditoría A-09: Decimals NO finitos son parseables y esquivaban el guard
    # (NaN = mayor numeric en PG → primera página en bucle; -Infinity vacía).
    for score in ("NaN", "Infinity", "-Infinity", "sNaN"):
        bads.append(
            base64.urlsafe_b64encode(f"{score}|{uuid.uuid4()}".encode()).decode()
        )
    for bad in bads:
        with pytest.raises(ApiError) as exc:
            v1.decode_cursor(bad)
        assert exc.value.status_code == 400
        assert exc.value.code == "invalid_cursor"


def test_etag_deterministic_and_sensitive():
    a = {"id": "1", "title": "Dev", "tags": ["a", "b"]}
    assert v1._etag_of(a) == v1._etag_of(dict(reversed(list(a.items()))))
    assert v1._etag_of(a) != v1._etag_of({**a, "title": "Otra"})
    assert v1._etag_of(a).startswith('"') and v1._etag_of(a).endswith('"')
