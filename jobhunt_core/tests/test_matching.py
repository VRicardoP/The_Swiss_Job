"""Matching (A-08): unit sin BD."""

import uuid

import jobhunt_core.tasks.matching  # noqa: F401 — registra la tarea en la app
from jobhunt_core import matching
from jobhunt_core.celery_app import celery_app


def test_eval_key_deterministic_and_component_sensitive():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    k1 = matching.eval_key(a, b, c, d)
    assert k1 == matching.eval_key(a, b, c, d)  # determinista
    assert len(k1) == 64
    for other in (
        matching.eval_key(uuid.uuid4(), b, c, d),
        matching.eval_key(a, uuid.uuid4(), c, d),
        matching.eval_key(a, b, uuid.uuid4(), d),
        matching.eval_key(a, b, c, uuid.uuid4()),
    ):
        assert other != k1  # CUALQUIER componente cambia la clave


def test_matching_task_registered_on_core_queue():
    assert "jobhunt.matching.run_profile" in celery_app.tasks
    assert celery_app.conf.task_routes["jobhunt.matching.*"] == {"queue": "core.matching"}
