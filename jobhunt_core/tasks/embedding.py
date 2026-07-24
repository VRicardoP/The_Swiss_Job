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
from jobhunt_core import profiles as core_profiles
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
    profiles_embedded: dict[str, int] = {}
    async with task_session_factory() as session_factory:
        async with session_factory() as session:
            models = await embeddings.active_models(session)
        for model in models:
            if model.dim != embeddings.EMBED_DIM:
                # Registro protegido por register_model; cinturón por si el
                # dato entró por otra vía: JAMÁS embeber a otra dimensión
                # (ni ofertas ni perfiles).
                logger.error(
                    "embedding: modelo %s/%s dim=%d != %d — saltado",
                    model.name, model.version, model.dim, embeddings.EMBED_DIM,
                )
                continue
            key = f"{model.name}/{model.version}"
            # Backend POR MODELO (rev. A-06 #3), resuelto de forma perezosa:
            # model_id identifica al encoder real. A-07 (perfiles) reutiliza
            # EXACTAMENTE esta resolución.
            backend = None

            async with session_factory() as session:
                pending = await embeddings.pending_offer_texts(
                    session, model.id, limit=limit
                )
            n = 0
            if pending:
                texts = [build_offer_text(r.content) for r in pending]
                # ENCODE fuera de transacción (CPU): la sesión no queda colgada.
                backend = embeddings.get_backend(model.name, model.version)
                vectors = backend.encode_batch(texts)
                items = [
                    {"text_hash": r.text_hash, "vector": v}
                    for r, v in zip(pending, vectors)
                ]
                async with session_factory() as session:
                    n = await embeddings.store_offer_embeddings(session, model.id, items)
                    await session.commit()
            embedded[key] = n

            # Perfiles (A-07): revisión VIGENTE de cada perfil, mismo backend.
            async with session_factory() as session:
                pending_p = await embeddings.pending_profile_revisions(
                    session, model.id, limit=limit
                )
            n_p = 0
            if pending_p:
                # 1) Reutilización por text_hash (rev. A-07 #2): lo ya
                #    embebido se COPIA sin re-encodear.
                async with session_factory() as session:
                    copied, remaining = await embeddings.copy_profile_vectors_by_text(
                        session, model.id, pending_p
                    )
                    await session.commit()
                n_p += copied
                if remaining:
                    # 2) Dedup del lote por text_hash: UN encode por texto
                    #    único, el vector se distribuye a sus revisiones.
                    by_th: dict[str, list] = {}
                    for r in remaining:
                        by_th.setdefault(r.text_hash, []).append(r)
                    ths = sorted(by_th)
                    texts_p = [
                        core_profiles.build_profile_text(by_th[th][0].content)
                        for th in ths
                    ]
                    backend = backend or embeddings.get_backend(model.name, model.version)
                    vectors_p = backend.encode_batch(texts_p)
                    items_p = [
                        {"revision_id": r.id, "profile_id": r.profile_id, "vector": v}
                        for th, v in zip(ths, vectors_p)
                        for r in by_th[th]
                    ]
                    async with session_factory() as session:
                        n_p += await embeddings.store_profile_embeddings(
                            session, model.id, items_p
                        )
                        await session.commit()
            profiles_embedded[key] = n_p
    return {"embedded": embedded, "profiles_embedded": profiles_embedded}
