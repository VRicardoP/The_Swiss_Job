"""Baja real del BFF, con transporte al core caído y base de tests."""

import uuid

import pytest

from models.job_filter import JobFilter
from services import exclusions_sync as sync
from tests.test_analytics_router import _auth
from tests.test_exclusions_sync import _corutina


@pytest.mark.asyncio
async def test_delete_reports_success_despite_failed_projection(
    client, db_session, monkeypatch
):
    headers, uid = await _auth(client)
    rule = JobFilter(
        user_id=uid, filter_type="title_contains", pattern="director", source="manual"
    )
    db_session.add(rule)
    await db_session.commit()
    fid = rule.id
    monkeypatch.setattr(sync, "resolve_mode", _corutina("core_primary"))
    monkeypatch.setattr(sync, "resolve_core_profile_id", _corutina(uuid.uuid4()))
    attempts = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def put(self, url, json=None):
            attempts.append(json)
            raise ConnectionError("corte controlado del transporte")

    monkeypatch.setattr(sync, "default_client_factory", Client)
    response = await client.delete(f"/api/v1/analytics/filters/{fid}", headers=headers)
    await db_session.refresh(rule)
    assert len(attempts) == 1 and attempts[0] == {"exclusions": []}
    assert not rule.is_active
    assert response.status_code != 204, (
        "BAJA confirmada 204 aunque su proyección falló; no hay entrega pendiente persistida"
    )
