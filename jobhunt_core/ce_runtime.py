"""Killable CE inference worker; JSON over pipes, no database or network IO.

One process per materialization fragment keeps model loading out of every
micro-batch. The parent owns its lifetime, including timeout and cancellation.
"""

import asyncio
import json
import math
import sys


class BudgetedScorer:
    def __init__(self, command=None):
        self.command = command or (
            sys.executable,
            "-u",
            "-m",
            "jobhunt_core.ce_runtime",
        )
        self.process = None

    async def score(self, prep, timeout):
        async with asyncio.timeout(timeout):
            if self.process is None:
                self.process = await asyncio.create_subprocess_exec(
                    *self.command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                )
            request = {
                "recipe": prep["receta_ce"],
                "queries": prep["consultas"],
                "documents": prep["documentos"],
            }
            self.process.stdin.write(json.dumps(request).encode() + b"\n")
            await self.process.stdin.drain()
            line = await self.process.stdout.readline()
            if not line:
                raise RuntimeError("CE worker exited without a response")
            response = json.loads(line)
            values = response.get("scores")
            if not isinstance(values, list) or len(values) != len(prep["misses"]):
                raise ValueError("CE worker returned an invalid batch")
            if any(
                type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1
                for v in values
            ):
                raise ValueError("CE worker returned invalid scores")
            return {c.offer_revision_id: v for c, v in zip(prep["misses"], values)}

    async def aclose(self):
        if self.process is None:
            return
        if self.process.returncode is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass
        # Reap even when the caller is being cancelled. No inference thread survives.
        reaped = asyncio.create_task(self.process.wait())
        try:
            await asyncio.shield(reaped)
        except asyncio.CancelledError:
            await reaped
            raise
        finally:
            if self.process.stdin is not None:
                self.process.stdin.close()


def main():
    from contextlib import redirect_stdout
    from jobhunt_core import cross_encoder as ce

    for line in sys.stdin:
        request = json.loads(line)
        recipe = request["recipe"]
        # Model libraries may print progress; stdout is exclusively our protocol.
        with redirect_stdout(sys.stderr):
            scores = ce.score_documents(
                recipe["model"],
                recipe["model_revision"],
                request["queries"],
                request["documents"],
                activation=recipe["activation"],
                fingerprint=recipe["model_fingerprint"],
                batch_size=ce.CE_BATCH_SIZE,
            )
        print(
            json.dumps({"scores": [float(v) for v in scores]}, allow_nan=False),
            flush=True,
        )


if __name__ == "__main__":
    main()
