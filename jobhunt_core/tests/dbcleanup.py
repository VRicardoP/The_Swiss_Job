"""Limpieza FK-safe COMPARTIDA por los fixtures `db` de los tests de integración.

Funciones composables extraídas como unión exacta de los bloques cleanup que
cada fixture duplicaba: mismas tablas, mismo orden FK-seguro. Cada fixture
llama solo a las que necesita, en su orden histórico. Borrar de una tabla
vacía es inocuo; saltarse una rompe el SEGUNDO run de la suite.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from jobhunt_core.config import settings


async def purge_runs(s: AsyncSession, run_ids: Sequence[uuid.UUID]) -> None:
    """Runs de cosecha: source_harvest_runs ANTES que harvest_runs (FK run_id)."""
    runs = list(run_ids)
    if not runs:
        return
    await s.execute(
        sa.text("DELETE FROM source_harvest_runs WHERE run_id = ANY(:r)"),
        {"r": runs},
    )
    await s.execute(
        sa.text("DELETE FROM harvest_runs WHERE id = ANY(:r)"), {"r": runs}
    )


async def purge_consumer_graph(
    s: AsyncSession, consumer_ids: Sequence[uuid.UUID]
) -> None:
    """Todo lo que cuelga de los consumers, en orden FK-seguro.

    Outbox por subject_profile (integration_outbox_deliveries cae por
    ON DELETE CASCADE); estado ANTES que evaluaciones (FK RESTRICT del
    current_eval).
    """
    cons = list(consumer_ids)
    if not cons:
        return
    await s.execute(
        sa.text("DELETE FROM consumer_credentials WHERE consumer_id = ANY(:c)"),
        {"c": cons},
    )
    await s.execute(
        sa.text(
            "DELETE FROM integration_outbox WHERE subject_profile_id IN "
            "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
        ),
        {"c": cons},
    )
    for tbl, col in (
        ("profile_vacancy_state", "profile_id"),
        ("match_evaluations", "profile_id"),
        ("profile_embeddings", "profile_id"),
        ("profile_revision_activations", "profile_id"),
        ("profile_revisions", "profile_id"),
    ):
        await s.execute(
            sa.text(
                f"DELETE FROM {tbl} WHERE {col} IN "
                "(SELECT id FROM profiles WHERE consumer_id = ANY(:c))"
            ),
            {"c": cons},
        )
    await s.execute(
        sa.text("DELETE FROM profiles WHERE consumer_id = ANY(:c)"), {"c": cons}
    )
    await s.execute(sa.text("DELETE FROM consumers WHERE id = ANY(:c)"), {"c": cons})


async def purge_source_graph(
    s: AsyncSession,
    source_ids: Sequence[uuid.UUID | None],
    scope_ids: Sequence[uuid.UUID],
    extra_vac_ids: Sequence[uuid.UUID] = (),
) -> None:
    """Grafo completo de cosecha/corpus por fuente, en orden FK-seguro.

    `extra_vac_ids`: vacantes creadas a mano (fuera del sink) que entran en el
    MISMO barrido que las derivadas de incarnaciones — dedup_candidates y
    canónica incluidas; el DELETE conjunto tolera merged_into entre ellas.
    """
    srcs = [x for x in source_ids if x is not None]
    vac_ids: list[uuid.UUID] = []
    if srcs:
        vac_ids = list(
            (
                await s.execute(
                    sa.text(
                        "SELECT DISTINCT i.vacancy_id FROM source_listing_incarnations i "
                        "JOIN source_listings l ON l.id = i.source_listing_id "
                        "WHERE l.source_id = ANY(:srcs)"
                    ),
                    {"srcs": srcs},
                )
            ).scalars().all()
        )
    vac_ids += list(extra_vac_ids)
    if vac_ids:
        await s.execute(
            sa.text(
                "DELETE FROM dedup_candidates "
                "WHERE vacancy_a = ANY(:v) OR vacancy_b = ANY(:v)"
            ),
            {"v": vac_ids},
        )
        # A-06: el puntero FK-compuesto bloquea el borrado de revisiones —
        # canónicas fuera ANTES de borrar vacantes.
        await s.execute(
            sa.text(
                "UPDATE vacancies SET current_offer_revision_id = NULL "
                "WHERE id = ANY(:v)"
            ),
            {"v": vac_ids},
        )
        await s.execute(
            sa.text("DELETE FROM offer_revision_sources WHERE vacancy_id = ANY(:v)"),
            {"v": vac_ids},
        )
        await s.execute(
            sa.text("DELETE FROM offer_revisions WHERE vacancy_id = ANY(:v)"),
            {"v": vac_ids},
        )
    if srcs:
        await s.execute(
            sa.text(
                "DELETE FROM link_evidence WHERE source_listing_id IN "
                "(SELECT id FROM source_listings WHERE source_id = ANY(:srcs))"
            ),
            {"srcs": srcs},
        )
        await s.execute(
            sa.text(
                "DELETE FROM source_listing_revisions WHERE incarnation_id IN ("
                "SELECT i.id FROM source_listing_incarnations i "
                "JOIN source_listings l ON l.id = i.source_listing_id "
                "WHERE l.source_id = ANY(:srcs))"
            ),
            {"srcs": srcs},
        )
        await s.execute(
            sa.text(
                "DELETE FROM source_listing_incarnations WHERE source_listing_id IN "
                "(SELECT id FROM source_listings WHERE source_id = ANY(:srcs))"
            ),
            {"srcs": srcs},
        )
    if vac_ids:
        await s.execute(
            sa.text("DELETE FROM vacancies WHERE id = ANY(:v)"), {"v": vac_ids}
        )
    if srcs:
        await s.execute(
            sa.text("DELETE FROM source_listings WHERE source_id = ANY(:srcs)"),
            {"srcs": srcs},
        )
    for sid in scope_ids:
        await s.execute(
            sa.text("DELETE FROM source_scope_state WHERE scope_id=:i"), {"i": sid}
        )
        await s.execute(sa.text("DELETE FROM harvest_scopes WHERE id=:i"), {"i": sid})
    if srcs:
        await s.execute(
            sa.text("DELETE FROM sources WHERE id = ANY(:srcs)"), {"srcs": srcs}
        )


async def purge_policies(s: AsyncSession, policy_ids: Sequence[uuid.UUID]) -> None:
    if not policy_ids:
        return
    await s.execute(
        sa.text("DELETE FROM scoring_policies WHERE id = ANY(:p)"),
        {"p": list(policy_ids)},
    )


async def purge_model(s: AsyncSession, model_id: uuid.UUID) -> None:
    """Vectores + partición por-modelo + registro del modelo, en ese orden."""
    await s.execute(
        sa.text("DELETE FROM offer_embeddings WHERE model_id = :m"), {"m": model_id}
    )
    await s.execute(
        sa.text(
            f"DROP TABLE IF EXISTS {settings.CORE_DB_SCHEMA}."
            f"offer_embeddings_{model_id.hex[:16]}"
        )
    )
    await s.execute(
        sa.text("DELETE FROM embedding_models WHERE id = :m"), {"m": model_id}
    )
