"""Retención de las tablas de trabajo terminado (O-4, jobhunt_core/retention.py).

Cuatro tablas crecían sin cota (28,9 MB en 28 días ⇒ ~377 MB/año medidos en el
clúster). Lo difícil de una purga no es borrar: es NO borrar lo que el gate o
una auditoría van a necesitar. Estos tests fijan exactamente eso — cada uno
pone delante de la purga una fila que NO debe desaparecer.

Sin residuo: cada test trabaja en una transacción que se deshace al final (el
patrón de test_integration_schema.py). Ejecutar vía core-migrate.
"""

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from jobhunt_core.config import settings
from jobhunt_core.retention import purge_retention

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


def _en_transaccion_deshecha(escenario):
    """Corre `escenario(session)` y hace ROLLBACK: la suite no ve residuo."""
    engine = create_async_engine(
        settings.CORE_DATABASE_URL, poolclass=sa.pool.NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run():
        try:
            async with factory() as s:
                try:
                    return await escenario(s)
                finally:
                    await s.rollback()
        finally:
            await engine.dispose()

    return asyncio.run(run())


async def _evento(s, edad_dias: int, event_id=None) -> uuid.UUID:
    event_id = event_id or uuid.uuid4()
    await s.execute(
        sa.text(
            "INSERT INTO integration_outbox "
            "(event_id, aggregate, aggregate_id, version, type, payload, created_at) "
            "VALUES (:e, 'match', :a, 1, 'match.created', '{}'::jsonb, "
            "        now() - make_interval(days => :d))"
        ),
        {"e": event_id, "a": event_id.hex, "d": edad_dias},
    )
    return event_id


async def _entrega(s, event_id, destino: str, estado: str, edad_dias: int) -> None:
    await s.execute(
        sa.text(
            "INSERT INTO integration_outbox_deliveries "
            "(event_id, destination, state, ack_at, dead_at) "
            "VALUES (:e, :d, CAST(:st AS delivery_state), "
            "        CASE WHEN :st = 'delivered' "
            "             THEN now() - make_interval(days => :dias) END, "
            "        CASE WHEN :st = 'dead' "
            "             THEN now() - make_interval(days => :dias) END)"
        ),
        {"e": event_id, "d": destino, "st": estado, "dias": edad_dias},
    )


async def _cuenta(s, tabla: str, **filtro) -> int:
    where = " AND ".join(f"{k} = :{k}" for k in filtro) or "true"
    return (
        await s.execute(sa.text(f"SELECT count(*) FROM {tabla} WHERE {where}"), filtro)
    ).scalar_one()


def test_purga_entregas_reconocidas_y_conserva_dead_pendientes_y_recientes():
    """El gate `outbox_dead` cuenta `state = 'dead'` SIN cota temporal, así que
    una dead antigua purgada le cambiaría el número. Las pendientes son trabajo
    por hacer. Solo cae lo `delivered` con `ack_at` fuera de retención."""

    async def escenario(s):
        vieja = await _evento(s, 400)
        await _entrega(s, vieja, "d-vieja", "delivered", 400)
        muerta = await _evento(s, 400)
        await _entrega(s, muerta, "d-muerta", "dead", 400)
        pendiente = await _evento(s, 400)
        await _entrega(s, pendiente, "d-pend", "pending", 400)
        reciente = await _evento(s, 1)
        await _entrega(s, reciente, "d-nueva", "delivered", 1)

        await purge_retention(s)

        assert await _cuenta(s, "integration_outbox_deliveries", event_id=vieja) == 0
        assert await _cuenta(s, "integration_outbox_deliveries", event_id=muerta) == 1
        assert await _cuenta(s, "integration_outbox_deliveries", event_id=pendiente) == 1
        assert await _cuenta(s, "integration_outbox_deliveries", event_id=reciente) == 1

    _en_transaccion_deshecha(escenario)


def test_el_evento_solo_cae_cuando_se_queda_sin_entregas():
    """TRAMPA del CASCADE: la FK entrega→evento es ON DELETE CASCADE, así que
    borrar el evento por edad a secas se habría llevado por delante sus
    entregas `dead` —las que el gate cuenta— sin que nadie lo viera. Por eso el
    barrido de eventos es una anti-unión y no un filtro por fecha."""

    async def escenario(s):
        huerfano = await _evento(s, 400)
        await _entrega(s, huerfano, "d1", "delivered", 400)
        con_dead = await _evento(s, 400)
        await _entrega(s, con_dead, "d1", "delivered", 400)
        await _entrega(s, con_dead, "d2", "dead", 400)

        await purge_retention(s)

        # Al primero se le fue su única entrega ⇒ ya no lo necesita nadie.
        assert await _cuenta(s, "integration_outbox", event_id=huerfano) == 0
        # Al segundo le queda la dead ⇒ el evento SIGUE, con su payload, que es
        # lo que hace auditable una dead-letter.
        assert await _cuenta(s, "integration_outbox", event_id=con_dead) == 1
        assert await _cuenta(s, "integration_outbox_deliveries", event_id=con_dead) == 1

    _en_transaccion_deshecha(escenario)


def test_purga_el_inbox_sombra_por_edad():
    """`shadow_inbox` es la evidencia del consumo idempotente de ADR-06. Se
    purga por edad, y la retención (30 d) es órdenes de magnitud mayor que la
    ventana de re-entrega (lease + backoff + dead-letter, minutos)."""

    async def escenario(s):
        viejo, nuevo = uuid.uuid4(), uuid.uuid4()
        for eid, dias in ((viejo, 400), (nuevo, 1)):
            await s.execute(
                sa.text(
                    "INSERT INTO shadow_inbox "
                    "(consumer_id, event_id, payload, received_at) "
                    "VALUES ('retencion-test', :e, '{}'::jsonb, "
                    "        now() - make_interval(days => :d))"
                ),
                {"e": eid, "d": dias},
            )

        await purge_retention(s)

        assert await _cuenta(s, "shadow_inbox", event_id=viejo) == 0
        assert await _cuenta(s, "shadow_inbox", event_id=nuevo) == 1

    _en_transaccion_deshecha(escenario)


async def _grafo_evaluable(s) -> dict:
    """Mínimo grafo FK para poder insertar evaluaciones."""
    ids = {
        k: uuid.uuid4()
        for k in (
            "source", "listing", "vacancy", "inc", "offrev",
            "consumer", "profile", "prev", "model", "policy",
        )
    }
    await s.execute(
        sa.text("INSERT INTO sources (id, name, tier) VALUES (:id, :n, 1)"),
        {"id": ids["source"], "n": f"ret-{ids['source'].hex[:8]}"},
    )
    await s.execute(
        sa.text(
            "INSERT INTO source_listings (id, source_id, external_id, url_normalized) "
            "VALUES (:id, :s, :e, :u)"
        ),
        {
            "id": ids["listing"], "s": ids["source"],
            "e": ids["listing"].hex, "u": f"https://ret/{ids['listing'].hex}",
        },
    )
    await s.execute(
        sa.text("INSERT INTO vacancies (id) VALUES (:id)"), {"id": ids["vacancy"]}
    )
    await s.execute(
        sa.text(
            "INSERT INTO source_listing_incarnations "
            "(id, source_listing_id, vacancy_id, seq, url) "
            "VALUES (:id, :l, :v, 1, 'https://ret/1')"
        ),
        {"id": ids["inc"], "l": ids["listing"], "v": ids["vacancy"]},
    )
    await s.execute(
        sa.text(
            "INSERT INTO offer_revisions (id, vacancy_id, content_hash, text_hash, content) "
            "VALUES (:id, :v, 'ch-ret', 'th-ret', '{}'::jsonb)"
        ),
        {"id": ids["offrev"], "v": ids["vacancy"]},
    )
    await s.execute(
        sa.text(
            "UPDATE vacancies SET current_offer_revision_id = :o, "
            "primary_incarnation_id = :i WHERE id = :v"
        ),
        {"o": ids["offrev"], "i": ids["inc"], "v": ids["vacancy"]},
    )
    await s.execute(
        sa.text("INSERT INTO consumers (id, name) VALUES (:id, :n)"),
        {"id": ids["consumer"], "n": f"ret-{ids['consumer'].hex[:8]}"},
    )
    await s.execute(
        sa.text(
            "INSERT INTO profiles (id, consumer_id, external_ref) VALUES (:id, :c, 'ret')"
        ),
        {"id": ids["profile"], "c": ids["consumer"]},
    )
    await s.execute(
        sa.text(
            "INSERT INTO profile_revisions "
            "(id, profile_id, content, content_hash, text_hash) "
            "VALUES (:id, :p, '{}'::jsonb, 'ch-ret', 'th-ret')"
        ),
        {"id": ids["prev"], "p": ids["profile"]},
    )
    await s.execute(
        sa.text(
            "INSERT INTO embedding_models (id, name, version, dim) "
            "VALUES (:id, :n, '1', 384)"
        ),
        {"id": ids["model"], "n": f"ret-{ids['model'].hex[:8]}"},
    )
    await s.execute(
        sa.text(
            "INSERT INTO scoring_policies (id, name, prompt_version) "
            "VALUES (:id, :n, 'v1')"
        ),
        {"id": ids["policy"], "n": f"ret-{ids['policy'].hex[:8]}"},
    )
    return ids


async def _evaluacion(s, ids: dict, edad_dias: int) -> uuid.UUID:
    eval_id = uuid.uuid4()
    await s.execute(
        sa.text(
            "INSERT INTO match_evaluations "
            "(id, profile_id, vacancy_id, offer_revision_id, profile_revision_id, "
            " model_id, scoring_policy_id, eval_key, score_final, scores, created_at) "
            "VALUES (:id, :p, :v, :o, :pr, :m, :pol, :k, 80.5, '{}'::jsonb, "
            "        now() - make_interval(days => :d))"
        ),
        {
            "id": eval_id, "p": ids["profile"], "v": ids["vacancy"],
            "o": ids["offrev"], "pr": ids["prev"], "m": ids["model"],
            "pol": ids["policy"], "k": eval_id.hex, "d": edad_dias,
        },
    )
    return eval_id


def test_las_evaluaciones_solo_caen_si_estan_superadas_y_sin_puntero():
    """Las dos guardas de `match_evaluations`, cada una con su contraejemplo:

    - la evaluación VIGENTE de un par (la apuntada por
      `profile_vacancy_state.current_eval_id`) no se toca;
    - la ÚLTIMA de un par tampoco, aunque nadie la apunte — `matching` pone el
      puntero a NULL cuando la vacante sale del feed, y sin esta guarda una
      pareja perfectamente viva se quedaría sin ninguna evaluación.

    Solo cae la evaluación antigua que ADEMÁS tiene una más nueva detrás."""

    async def escenario(s):
        ids = await _grafo_evaluable(s)
        superada = await _evaluacion(s, ids, 400)
        ultima = await _evaluacion(s, ids, 399)  # más nueva: supera a la anterior
        assert superada != ultima

        await purge_retention(s)
        # La última del par sobrevive SIN puntero (nadie la apunta todavía).
        assert await _cuenta(s, "match_evaluations", id=superada) == 0
        assert await _cuenta(s, "match_evaluations", id=ultima) == 1

        # Ahora la vigente pasa a ser una nueva y `ultima` queda superada...
        vigente = await _evaluacion(s, ids, 398)
        await s.execute(
            sa.text(
                "INSERT INTO profile_vacancy_state "
                "(profile_id, vacancy_id, current_eval_id) VALUES (:p, :v, :e)"
            ),
            {"p": ids["profile"], "v": ids["vacancy"], "e": ultima},
        )
        # ...pero está APUNTADA: la purga no puede llevársela.
        await purge_retention(s)
        assert await _cuenta(s, "match_evaluations", id=ultima) == 1
        assert await _cuenta(s, "match_evaluations", id=vigente) == 1

        # Sin el puntero, y ya superada por `vigente`, sí cae.
        await s.execute(
            sa.text(
                "UPDATE profile_vacancy_state SET current_eval_id = NULL "
                "WHERE profile_id = :p AND vacancy_id = :v"
            ),
            {"p": ids["profile"], "v": ids["vacancy"]},
        )
        await purge_retention(s)
        assert await _cuenta(s, "match_evaluations", id=ultima) == 0
        assert await _cuenta(s, "match_evaluations", id=vigente) == 1

    _en_transaccion_deshecha(escenario)


def test_la_purga_es_idempotente_y_esta_acotada_por_pasada(monkeypatch):
    """Idempotente (la 2ª pasada no encuentra nada) y ACOTADA: con el tope a 1
    cada pasada se lleva una fila y hacen falta tantas pasadas como filas. El
    tope existe para no coger un lock largo sobre tablas que el dispatcher está
    usando; que converja es lo que lo hace admisible."""

    async def escenario(s):
        eventos = [await _evento(s, 400) for _ in range(3)]
        for e in eventos:
            await _entrega(s, e, "d1", "delivered", 400)

        monkeypatch.setattr(settings, "CORE_RETENTION_MAX_ROWS", 1)
        primera = await purge_retention(s)
        assert primera["entregas"] == 1 and primera["tope_por_pasada"] == 1

        monkeypatch.setattr(settings, "CORE_RETENTION_MAX_ROWS", 20_000)
        segunda = await purge_retention(s)
        assert segunda["entregas"] == 2  # lo que quedaba, en una pasada
        tercera = await purge_retention(s)
        assert tercera["entregas"] == 0 and tercera["eventos"] == 0

        for e in eventos:
            assert await _cuenta(s, "integration_outbox", event_id=e) == 0

    _en_transaccion_deshecha(escenario)
