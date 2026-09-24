"""Real subprocess checks; no model artefacts or production corpus required."""

import asyncio
import json
import signal
import sys
from types import SimpleNamespace

import pytest

from jobhunt_core.ce_runtime import BudgetedScorer


def _prep():
    return {
        "receta_ce": {},
        "consultas": ["synthetic"],
        "documentos": ["synthetic"],
        "misses": [SimpleNamespace(offer_revision_id="one")],
    }


def test_process_protocol_and_reuse():
    code = "import sys,json\nfor line in sys.stdin:\n json.loads(line); print(json.dumps({'scores':[0.75]}),flush=True)"

    async def check():
        worker = BudgetedScorer((sys.executable, "-u", "-c", code))
        try:
            assert await worker.score(_prep(), 10) == {"one": 0.75}
            pid = worker.process.pid
            assert await worker.score(_prep(), 10) == {"one": 0.75}
            assert worker.process.pid == pid
        finally:
            await worker.aclose()
        assert worker.process.returncode is not None

    asyncio.run(check())


def test_timed_out_inference_is_killed_and_reaped():
    code = "import sys,time\nsys.stdin.readline(); time.sleep(60)"

    async def check():
        worker = BudgetedScorer((sys.executable, "-u", "-c", code))
        try:
            with pytest.raises(TimeoutError):
                await worker.score(_prep(), 0.3)
        finally:
            await worker.aclose()
        assert worker.process.returncode == -signal.SIGKILL

    asyncio.run(check())


def test_external_cancellation_reaps_process():
    code = "import sys,time\nsys.stdin.readline(); time.sleep(60)"

    async def check():
        worker = BudgetedScorer((sys.executable, "-u", "-c", code))

        async def run():
            try:
                await worker.score(_prep(), 60)
            finally:
                await worker.aclose()

        task = asyncio.create_task(run())
        async with asyncio.timeout(10):
            while worker.process is None:
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker.process.returncode is not None

    asyncio.run(check())


@pytest.mark.parametrize("scores", [[], [float("nan")], [True], [1.2]])
def test_invalid_worker_scores_fail_closed(scores):
    code = f"import sys\nsys.stdin.readline(); print({json.dumps({'scores': scores})!r},flush=True)"

    async def check():
        worker = BudgetedScorer((sys.executable, "-u", "-c", code))
        try:
            with pytest.raises(ValueError):
                await worker.score(_prep(), 10)
        finally:
            await worker.aclose()

    asyncio.run(check())
