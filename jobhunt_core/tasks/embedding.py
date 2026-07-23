"""Tarea Celery de embeddings de ofertas (A-06) — cola core.embedding.

Convención del repo: `def` + asyncio.run(_impl()). El ENCODE corre FUERA de
toda transacción (CPU, potencialmente lento); la escritura es optimista
(text_hash, model_id) — dos workers sobre el mismo texto no chocan ni
duplican. Sin trabajo pendiente la tarea es un no-op barato.
"""

import asyncio
import logging
from typing import Any

from jobhunt_core import embeddings
from jobhunt_core.celery_app import celery_app
from jobhunt_core.database import task_session_factory
from jobhunt_core.harvest.normalize import build_offer_text

logger = logging.getLogger(__name__)


@celery_app.task(name="jobhunt.embedding.run_pending", bind=True, max_retries=1)
def run_pending_task(self, limit: int = 200) -> dict[str, Any]:
    try:
        return asyncio.run(_run_pending_impl(limit))
    except Exception as exc:
        # Transitorios (BD, descarga del modelo): retry. No hay config de
        # usuario aquí que clasificar como permanente.
        logger.error("embedding.run_pending falló: %s", exc)
        raise self.retry(exc=exc, countdown=120)


async def _run_pending_impl(limit: int) -> dict[str, Any]:
    embedded: dict[str, int] = {}
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            models = await embeddings.active_models(session)
        for model in models:
            if model.dim != embeddings.EMBED_DIM:
                # Registro protegido por register_model; cinturón por si el
                # dato entró por otra vía: JAMÁS embeber a otra dimensión.
                logger.error(
                    "embedding: modelo %s/%s dim=%d != %d — saltado",
                    model.name, model.version, model.dim, embeddings.EMBED_DIM,
                )
                continue
            async with session_factory() as session:
                pending = await embeddings.pending_offer_texts(
                    session, model.id, limit=limit
                )
            if not pending:
                embedded[f"{model.name}/{model.version}"] = 0
                continue
            texts = [build_offer_text(r.content) for r in pending]
            # ENCODE fuera de transacción (CPU): la sesión no queda colgada.
            # Backend POR MODELO (rev. A-06 #3): model_id identifica al
            # encoder real — nunca un backend global compartido.
            backend = embeddings.get_backend(model.name, model.version)
            vectors = backend.encode_batch(texts)
            items = [
                {"text_hash": r.text_hash, "vector": v}
                for r, v in zip(pending, vectors)
            ]
            async with session_factory() as session:
                n = await embeddings.store_offer_embeddings(session, model.id, items)
                await session.commit()
            embedded[f"{model.name}/{model.version}"] = n
    return {"embedded": embedded}
