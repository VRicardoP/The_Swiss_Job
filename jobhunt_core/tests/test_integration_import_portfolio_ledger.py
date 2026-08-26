"""Integración del LEDGER del sink de la importación del portfolio (§4, parte 1).

Verifica que `synthesize_vacancies(..., ledger=[...])` registra la disposición EXACTA por
url: created (vacante nueva), reused (preexistente: ejecución previa o de OTRA fuente),
quarantine (no_url/malformed/collision_intra/collision_cross_run/collision_cross_source), con
el vacancy_id resultante. Y que `migrate_and_reconcile` lo PERSISTE en el manifiesto.

BD DESECHABLE Postgres (reutiliza `_on_disposable_db` del rehearsal). Ejecutar vía core-migrate.
"""

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa

from jobhunt_core import import_portfolio as ip
from jobhunt_core import import_portfolio_ledger as pil
from jobhunt_core.tests.test_integration_migration_rehearsal_portfolio import _on_disposable_db

pytestmark = pytest.mark.skipif(
    not os.getenv("CORE_ADMIN_DATABASE_URL"),
    reason="requiere BD (ejecutar vía core-migrate)",
)


def _by_url(ledger: list[pil.LedgerEntry]) -> dict:
    """{url: entry} para las entradas CON url (las no_url se cuentan aparte)."""
    return {e.url: e for e in ledger if e.url is not None}


async def _seed_other_source(s, url: str, external_id: str) -> None:
    """Otra fuente (arbeitnow) importa `url` vía el sink real → crea una vacante NO
    portfolio-import con esa url_normalized (para los caminos reused/cross-source)."""
    import jobhunt_core.harvest.providers  # noqa: F401 — registra arbeitnow
    from jobhunt_core.harvest.sink import RawListingSink
    from jobhunt_core.harvest.types import RawListing

    # Fuente 'arbeitnow' IDEMPOTENTE: varios seeds en un mismo test comparten la fuente
    # (cada uno con su scope y su listing) sin violar UNIQUE(name).
    await s.execute(
        sa.text(
            "INSERT INTO sources (id, name, tier) VALUES (:i, 'arbeitnow', 0) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"i": uuid.uuid4()},
    )
    src = (
        await s.execute(sa.text("SELECT id FROM sources WHERE name = 'arbeitnow'"))
    ).scalar_one()
    scope = uuid.uuid4()
    await s.execute(
        sa.text(
            "INSERT INTO harvest_scopes (id, source_id, params, tier) "
            "VALUES (:i, :s, '{}'::jsonb, 0)"
        ),
        {"i": scope, "s": src},
    )
    await s.commit()
    await RawListingSink().handle(
        s,
        str(scope),
        (
            RawListing(
                external_id=external_id,
                url=url,
                payload={"title": "Other", "company_name": "X", "description": "d", "tags": []},
            ),
        ),
    )
    await s.commit()


def test_ledger_created_then_reused_prior_run():
    """Primera síntesis de una url → created; re-síntesis de la MISMA url → reused (la
    vacante ya estaba en el snapshot pre-síntesis, idempotencia)."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            url = "https://jobs.example.ch/dev-1"
            item = {"url": url, "title": "Dev", "company": "A", "description": "d"}

            led1: list = []
            await ip.synthesize_vacancies(s, scope_id, [item], ledger=led1)
            await s.commit()
            e1 = _by_url(led1)[url]
            assert e1.disposition == pil.CREATED
            assert e1.vacancy_id is not None
            assert e1.reason is None
            vid = e1.vacancy_id

            led2: list = []
            await ip.synthesize_vacancies(s, scope_id, [item], ledger=led2)
            await s.commit()
            e2 = _by_url(led2)[url]
            assert e2.disposition == pil.REUSED
            assert e2.vacancy_id == vid  # la MISMA vacante (idempotente)

    asyncio.run(_on_disposable_db(_run))


def test_ledger_reused_other_source():
    """Una url que YA existe bajo OTRA fuente → el portfolio la reutiliza (attach
    cross-source): disposition=reused, vacancy_id = la vacante preexistente."""

    async def _run(factory):
        async with factory() as s:
            url = "https://reuse.example.ch/job-9"
            await _seed_other_source(s, url, "other-9")
            other_vid = (
                await s.execute(
                    sa.text(
                        "SELECT i.vacancy_id FROM source_listing_incarnations i "
                        "JOIN source_listings sl ON sl.id = i.source_listing_id "
                        "JOIN sources s ON s.id = sl.source_id AND s.name = 'arbeitnow' "
                        "WHERE i.ended_at IS NULL"
                    )
                )
            ).scalar_one()

            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            led: list = []
            await ip.synthesize_vacancies(
                s, scope_id, [{"url": url, "title": "R", "company": "A", "description": "d"}],
                ledger=led,
            )
            await s.commit()
            e = _by_url(led)[url]
            assert e.disposition == pil.REUSED
            assert e.vacancy_id == other_vid  # reutiliza la vacante de la otra fuente

    asyncio.run(_on_disposable_db(_run))


def test_ledger_quarantine_no_url_and_malformed():
    """Item sin url → quarantine:no_url (url None); url malformada → quarantine:malformed;
    la buena del mismo lote → created."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            good = {"url": "https://ok.example.ch/x", "title": "Ok", "company": "A"}
            malformed = {"url": "https://[invalid", "title": "Rota", "company": "B"}
            no_url = {"url": None, "title": "Sin URL", "company": "C"}

            led: list = []
            await ip.synthesize_vacancies(s, scope_id, [good, malformed, no_url], ledger=led)
            await s.commit()

            by_url = _by_url(led)
            assert by_url[good["url"]].disposition == pil.CREATED
            m = by_url[malformed["url"]]
            assert m.disposition == pil.QUARANTINE and m.reason == pil.Q_MALFORMED
            assert m.vacancy_id is None and m.url_normalized is None  # no normalizable
            no_url_entries = [e for e in led if e.url is None]
            assert len(no_url_entries) == 1
            assert no_url_entries[0].reason == pil.Q_NO_URL

    asyncio.run(_on_disposable_db(_run))


def test_ledger_quarantine_collision_intra():
    """Dos urls DISTINTAS con la misma clave normalizada en el MISMO lote → ambas
    quarantine:collision_intra, ninguna sintetizada."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            a = {"url": "https://spa.example.ch/jobs#aaa", "title": "A", "company": "A"}
            b = {"url": "https://spa.example.ch/jobs#bbb", "title": "B", "company": "B"}

            led: list = []
            collided = await ip.synthesize_vacancies(s, scope_id, [a, b], ledger=led)
            await s.commit()
            assert collided == {a["url"], b["url"]}
            by_url = _by_url(led)
            for url in (a["url"], b["url"]):
                assert by_url[url].disposition == pil.QUARANTINE
                assert by_url[url].reason == pil.Q_COLLISION_INTRA
                assert by_url[url].vacancy_id is None

    asyncio.run(_on_disposable_db(_run))


def test_ledger_quarantine_collision_cross_run():
    """Run1 sintetiza una url SPA (created); run2 con OTRA url de la misma clave la detecta
    contra lo persistido → quarantine:collision_cross_run."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            u1 = {"url": "https://cross.example.ch/jobs#xxx", "title": "R1", "company": "A"}
            u2 = {"url": "https://cross.example.ch/jobs#yyy", "title": "R2", "company": "B"}

            led1: list = []
            await ip.synthesize_vacancies(s, scope_id, [u1], ledger=led1)
            await s.commit()
            assert _by_url(led1)[u1["url"]].disposition == pil.CREATED

            led2: list = []
            c2 = await ip.synthesize_vacancies(s, scope_id, [u2], ledger=led2)
            await s.commit()
            assert c2 == {u2["url"]}
            e = _by_url(led2)[u2["url"]]
            assert e.disposition == pil.QUARANTINE
            assert e.reason == pil.Q_COLLISION_CROSS_RUN

    asyncio.run(_on_disposable_db(_run))


async def _attach_extra_incarnation(s, vacancy_id, url, external_id):
    """Incarnación EXTRA (otra url_normalized, MISMA vacante) bajo arbeitnow —
    reproduce una vacante que abarca DOS claves normalizadas distintas (p.ej.
    attach por identidad título+empresa): la colisión cross-source REAL que la
    revalidación debe seguir cazando (G1-P3-6)."""
    from jobhunt_core.harvest.sink import normalize_url

    src = (
        await s.execute(sa.text("SELECT id FROM sources WHERE name = 'arbeitnow'"))
    ).scalar_one()
    lid = uuid.uuid4()
    await s.execute(
        sa.text(
            "INSERT INTO source_listings (id, source_id, external_id, url_normalized) "
            "VALUES (:i, :s, :e, :u)"
        ),
        {"i": lid, "s": src, "e": external_id, "u": normalize_url(url)},
    )
    await s.execute(
        sa.text(
            "INSERT INTO source_listing_incarnations "
            "(id, source_listing_id, vacancy_id, seq, url) "
            "VALUES (:i, :l, :v, 1, :u)"
        ),
        {"i": uuid.uuid4(), "l": lid, "v": vacancy_id, "u": url},
    )
    await s.commit()


async def _other_source_vacancy(s, url_normalized):
    return (
        await s.execute(
            sa.text(
                "SELECT i.vacancy_id FROM source_listing_incarnations i "
                "JOIN source_listings sl ON sl.id = i.source_listing_id "
                "JOIN sources src ON src.id = sl.source_id AND src.name = 'arbeitnow' "
                "WHERE sl.url_normalized = :u AND i.ended_at IS NULL"
            ),
            {"u": url_normalized},
        )
    ).scalar_one()


def test_ledger_quarantine_collision_cross_source(caplog):
    """La vacante de OTRA fuente abarca DOS claves normalizadas distintas (la
    colisión cross-source REAL, G1-P3-6): la revalidación post-attach revierte
    la cadena → quarantine:collision_cross_source, sin vacante. El log de
    resumen NO cuenta la cadena revertida como sintetizada (G1 H-14a)."""
    import logging as _logging

    caplog.set_level(_logging.INFO, logger="jobhunt_core.import_portfolio")

    async def _run(factory):
        async with factory() as s:
            url_a = "https://multi.example.ch/jobs/a"
            await _seed_other_source(s, url_a, "multi-a")
            vid = await _other_source_vacancy(s, url_a)
            # La MISMA vacante tiene además otra url con clave DISTINTA.
            await _attach_extra_incarnation(
                s, vid, "https://multi.example.ch/jobs/b", "multi-b"
            )
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()

            led: list = []
            collided = await ip.synthesize_vacancies(
                s, scope_id, [{"url": url_a, "title": "A", "company": "A"}], ledger=led
            )
            await s.commit()
            assert collided == {url_a}
            e = _by_url(led)[url_a]
            assert e.disposition == pil.QUARANTINE
            assert e.reason == pil.Q_COLLISION_CROSS_SOURCE
            assert e.vacancy_id is None
            # No quedó vínculo portfolio-import (cadena revertida).
            assert await ip.resolve_vacancy_by_url(s, url_a) is None

    asyncio.run(_on_disposable_db(_run))
    # G1 H-14a: 1 item, cadena revertida ⇒ "0 sintetizadas" (antes contaba 1).
    assert any(
        "1 items → 0 sintetizadas" in r.getMessage() for r in caplog.records
    ), [r.getMessage() for r in caplog.records if "sintetizadas" in r.getMessage()]


def test_ledger_cross_source_trivial_spelling_attach_kept():
    """Regresión G1-P3-6: la MISMA oferta ya cosechada por otra fuente con
    grafía trivialmente distinta (host en mayúsculas + barra final) — el sink
    adjunta por url_normalized y la revalidación comparaba urls CRUDAS byte a
    byte: el attach LEGÍTIMO se revertía como collision_cross_source y el caso
    NORMAL entre portales acababa en reconciliación manual. Ahora compara
    normalizadas: disposition=reused sobre la vacante preexistente."""

    async def _run(factory):
        async with factory() as s:
            raw_other = "https://Example.com/job/1/"  # grafía del otro portal
            await _seed_other_source(s, raw_other, "other-graf-1")
            other_vid = await _other_source_vacancy(s, "https://example.com/job/1")
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()

            url = "https://example.com/job/1"  # la MISMA oferta, grafía limpia
            led: list = []
            collided = await ip.synthesize_vacancies(
                s, scope_id, [{"url": url, "title": "Dev", "company": "A"}], ledger=led
            )
            await s.commit()
            assert collided == set()  # NADA revertido
            e = _by_url(led)[url]
            assert e.disposition == pil.REUSED
            assert e.vacancy_id == other_vid  # C-4 solo añadió el enlace
            assert await ip.resolve_vacancy_by_url(s, url) == other_vid

    asyncio.run(_on_disposable_db(_run))


def test_ledger_multibyte_url_over_limit_reason_is_limit():
    """Regresión G1-P3-5: una url con ≤2048 CHARS pero >2048 BYTES (multibyte)
    pasaba la comprobación en caracteres de _synthesizable y caía en
    _preprocess ⇒ el ledger registraba 'malformed'. El sink mide BYTES
    (C6-P2-2): la razón PRECISA es 'limit'. La partición sintetizable/no ya
    coincidía (nada se pierde) — lo que mentía era la razón auditable."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            url = "https://x.example.ch/" + "ü" * 1200  # 1221 chars, 2421 bytes
            assert len(url) <= 2048 < len(url.encode())
            led: list = []
            await ip.synthesize_vacancies(
                s, scope_id, [{"url": url, "title": "U", "company": "A"}], ledger=led
            )
            await s.commit()
            e = _by_url(led)[url]
            assert e.disposition == pil.QUARANTINE
            assert e.reason == pil.Q_LIMIT  # antes: malformed (razón falseada)
            assert await ip.resolve_vacancy_by_url(s, url) is None

    asyncio.run(_on_disposable_db(_run))


def test_ledger_non_ascii_url_is_created_not_over_quarantined():
    """ANÁLISIS 2 (frontera del fix normalized_key): una URL no-ASCII VÁLIDA (IDN + CJK)
    es codificable en utf-8 → NO debe caer como malformed. normalized_key solo rechaza lo
    NO codificable (surrogates), nunca Unicode válido. Se sintetiza (created) y resuelve."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            url = "https://例え.example.ch/求人-42"  # IDN + path CJK, utf-8 válido
            led: list = []
            await ip.synthesize_vacancies(
                s, scope_id, [{"url": url, "title": "仕事", "company": "会社"}], ledger=led
            )
            await s.commit()
            e = _by_url(led)[url]
            assert e.disposition == pil.CREATED  # NO malformed
            assert e.vacancy_id is not None
            assert e.url_normalized is not None and e.external_id is not None
            assert await ip.resolve_vacancy_by_url(s, url) == e.vacancy_id
            assert ip.normalized_key(url) is not None  # clave usable

    asyncio.run(_on_disposable_db(_run))


def test_ledger_no_title_and_over_limit_quarantined():
    """REGRESIÓN P1/P3 rev. externa: un durable SIN título normalizable (→ el sink no crearía
    canónica, vacante impresentable) y uno con url > MAX_URL_LEN (→ el sink la cuarentena) se
    cuarentenan ANTES de sintetizar (no crean vacante), con razón real en el ledger."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            long_url = "https://x.example.ch/" + "a" * 2100  # > 2048 (core0028)
            items = [
                {"url": "https://ok.example.ch/1", "title": "Ok", "company": "A"},
                {"url": "https://nt.example.ch/2", "title": "   ", "company": "B"},  # solo espacios
                {"url": long_url, "title": "Long", "company": "C"},
            ]
            led: list = []
            await ip.synthesize_vacancies(s, scope_id, items, ledger=led)
            await s.commit()
            by = _by_url(led)
            assert by["https://ok.example.ch/1"].disposition == pil.CREATED
            nt = by["https://nt.example.ch/2"]
            assert nt.disposition == pil.QUARANTINE and nt.reason == pil.Q_NO_TITLE
            assert nt.vacancy_id is None
            lim = by[long_url]
            assert lim.disposition == pil.QUARANTINE and lim.reason == pil.Q_LIMIT
            # Ninguna de las cuarentenadas creó vacante.
            assert await ip.resolve_vacancy_by_url(s, "https://nt.example.ch/2") is None
            assert await ip.resolve_vacancy_by_url(s, long_url) is None
            n_vac = (await s.execute(sa.text("SELECT count(*) FROM vacancies"))).scalar_one()
            assert n_vac == 1  # solo la buena

    asyncio.run(_on_disposable_db(_run))


def test_ledger_non_str_title_quarantined_not_crash():
    """REGRESIÓN P1 (verificación de fixes): un título truthy NO-str (int/list del feed) NO debe
    reventar el lote (AttributeError en `.strip()`) — se cuarentena no_title como cualquier
    título no normalizable (el sink degrada un no-str a None; aquí se replica con isinstance)."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            items = [
                {"url": "https://ok.example.ch/1", "title": "Ok", "company": "A"},
                {"url": "https://n1.example.ch/2", "title": 123, "company": "B"},  # int
                {"url": "https://n2.example.ch/3", "title": ["x"], "company": "C"},  # list
            ]
            led: list = []
            await ip.synthesize_vacancies(s, scope_id, items, ledger=led)  # NO debe lanzar
            await s.commit()
            by = _by_url(led)
            assert by["https://ok.example.ch/1"].disposition == pil.CREATED
            for u in ("https://n1.example.ch/2", "https://n2.example.ch/3"):
                assert by[u].disposition == pil.QUARANTINE and by[u].reason == pil.Q_NO_TITLE

    asyncio.run(_on_disposable_db(_run))


def test_ledger_surrogate_url_does_not_abort_migration():
    """REGRESIÓN análisis 1 (P1): una URL con surrogate suelto pasa normalize_url pero su
    .encode() estricto lanza UnicodeEncodeError. Antes, build_ledger crasheaba FUERA del
    guard y abortaba TODO el cutover. Ahora _safe_key la trata como sin-clave (url_normalized
    /external_id None) y la migración TERMINA; el manifiesto se persiste (persist_manifest
    sanea el campo url crudo)."""
    from jobhunt_core import import_portfolio_manifest as man

    bad = "https://x.ch/\ud800job"  # estructura válida, code point NO codificable en utf-8
    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": "https://good.example.ch/a", "status": "applied", "title": "A",
                 "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
                {"url": bad, "status": "saved", "title": "Mojibake",
                 "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
            ],
            "saved_searches": [],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)  # NO debe lanzar
            assert manifest["verdict"] == "ok", manifest["divergences"]
            bad_entries = [e for e in manifest["ledger"] if e["url"] == bad]
            assert len(bad_entries) == 1
            e = bad_entries[0]
            assert e["disposition"] == pil.QUARANTINE and e["reason"] == pil.Q_MALFORMED
            assert e["url_normalized"] is None and e["external_id"] is None
            assert any(
                x["disposition"] == pil.CREATED and x["url"] == "https://good.example.ch/a"
                for x in manifest["ledger"]
            )
            await s.commit()  # el manifiesto persiste pese al surrogate (saneo del persist)

    asyncio.run(_on_disposable_db(_run))


def test_ledger_nul_field_does_not_abort_migration():
    """Regresión G1-P2-1 (gemelo del test de surrogates): un durable con NUL en un
    campo de texto viaja en el JSON del manifiesto como la secuencia escapada
    \\u0000 (6 chars ASCII que el encode/replace de surrogates NO toca) y el
    CAST(:m AS jsonb) la rechaza → antes moría la transacción ENTERA del cutover
    al registrar la cuarentena que el staging ya había aislado. persist_manifest
    sanea ahora los VALORES recursivamente (NUL → U+FFFD) y la migración TERMINA
    con el manifiesto persistido."""
    from jobhunt_core import import_portfolio_manifest as man

    bad_title = "Job\x00Tóxico"  # NUL: el sink lo cuarentena (Q_MALFORMED)
    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": "https://good.example.ch/n1", "status": "applied", "title": "A",
                 "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
                {"url": "https://nul.example.ch/n2", "status": "saved", "title": bad_title,
                 "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
            ],
            "saved_searches": [],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)  # NO debe lanzar
            assert manifest["verdict"] == "ok", manifest["divergences"]
            e = next(
                x for x in manifest["ledger"]
                if x["url"] == "https://nul.example.ch/n2"
            )
            assert e["disposition"] == pil.QUARANTINE
            assert e["reason"] == pil.Q_MALFORMED
            await s.commit()  # el manifiesto PERSISTE pese al NUL (saneo G1-P2-1)
            stored = (
                await s.execute(
                    sa.text(
                        "SELECT manifest FROM portfolio_migration_manifest "
                        "WHERE id = :i"
                    ),
                    {"i": uuid.UUID(manifest["id"])},
                )
            ).scalar_one()
            blob = json.dumps(stored, ensure_ascii=False)
            assert "\\u0000" not in blob and "\x00" not in blob
            assert "Job�Tóxico" in blob  # el valor saneado, no perdido

    asyncio.run(_on_disposable_db(_run))


def test_persist_manifest_nul_in_staged_value_persists():
    """Regresión G1-P2-1 (repro DIRECTA del informe): un manifiesto con un durable
    staged conteniendo '\\x00' debe poder persistirse tal cual — es exactamente la
    ruta de defensa (staging) diseñada para que el dato tóxico NO abortara el lote."""
    from jobhunt_core import import_portfolio_manifest as man

    manifest = {
        "verdict": "ok",
        "staging": [{"kind": "application", "reason": "malformed",
                     "durable": {"url": "https://x.ch/a", "notes": "a\x00b"}}],
    }

    async def _run(factory):
        async with factory() as s:
            mid = await man.persist_manifest(s, manifest)  # antes: DBAPIError (jsonb)
            await s.commit()
            stored = (
                await s.execute(
                    sa.text(
                        "SELECT manifest FROM portfolio_migration_manifest "
                        "WHERE id = :i"
                    ),
                    {"i": mid},
                )
            ).scalar_one()
            assert stored["staging"][0]["durable"]["notes"] == "a�b"

    asyncio.run(_on_disposable_db(_run))


def test_ledger_persisted_in_manifest():
    """migrate_and_reconcile PERSISTE el ledger en el manifiesto: una entrada por url que
    ENTRÓ en síntesis, created con vacancy_id y la url malformada como quarantine. Los
    durables SIN url no llegan a síntesis (migrate_portfolio los filtra) — se contabilizan
    en el staging, no en el ledger de corpus."""
    from jobhunt_core import import_portfolio_manifest as man

    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": "https://m.example.ch/a", "status": "applied", "title": "A",
                 "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
                {"url": "https://[invalid", "status": "saved", "title": "Rota",
                 "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
            ],
            "saved_searches": [],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)
            assert manifest["verdict"] == "ok", manifest["divergences"]
            ledger = manifest["ledger"]
            assert isinstance(ledger, list) and ledger
            created = [e for e in ledger if e["disposition"] == pil.CREATED]
            assert any(e["url"] == "https://m.example.ch/a" for e in created)
            assert all(e["vacancy_id"] for e in created)
            assert any(
                e["disposition"] == pil.QUARANTINE and e["reason"] == pil.Q_MALFORMED
                for e in ledger
            )
            await s.commit()

    asyncio.run(_on_disposable_db(_run))


def test_ledger_group_reason_is_order_independent():
    """REGRESIÓN P2 ronda 8: varios durables NO sintetizables con la MISMA url pero razones
    DISTINTAS (uno sin título → no_title, otro con surrogate → malformed) daban una razón de
    cuarentena dependiente del ORDEN del lote (se guardaba la del primero). Ahora se acumulan y
    se elige por precedencia determinista (malformed > limit > no_title): el ledger auditable es
    reproducible al invertir el lote."""
    url = "https://ord.example.ch/1"
    no_title = {"url": url, "title": "   ", "company": "A"}          # solo espacios → no_title
    malformed = {"url": url, "title": "Job \ud800", "company": "B"}  # surrogate → malformed

    async def _reason_for(items: list) -> pil.LedgerEntry:
        async def _run(factory):
            async with factory() as s:
                scope_id = await ip.ensure_import_scope(s)
                await s.commit()
                led: list = []
                await ip.synthesize_vacancies(s, scope_id, items, ledger=led)
                await s.rollback()
                return _by_url(led)[url]

        return await _on_disposable_db(_run)

    forward = asyncio.run(_reason_for([no_title, malformed]))
    reverse = asyncio.run(_reason_for([malformed, no_title]))
    # Misma razón en ambos órdenes: la del MÁS severo (malformed), no la del primero del lote.
    assert forward.disposition == pil.QUARANTINE and forward.reason == pil.Q_MALFORMED, forward
    assert reverse.disposition == pil.QUARANTINE and reverse.reason == pil.Q_MALFORMED, reverse
    assert forward.reason == reverse.reason


def test_vac_key_determinista_con_dos_incarnaciones_activas():
    """Regresión G1 H-11: con DOS incarnaciones activas de portfolio-import
    sobre la MISMA vacante (fusión del pipeline dedup — ninguna UNIQUE lo
    impide), la clave portable de vacante usaba LIMIT 1 SIN ORDER BY: el
    checksum quedaba a merced del plan de ejecución. Ahora ORDER BY
    url_normalizada: SIEMPRE la menor."""
    from jobhunt_core import import_portfolio_manifest as man
    from jobhunt_core import import_portfolio_migrate as ipm

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            led: list = []
            await ip.synthesize_vacancies(
                s, scope_id,
                # La 'b' primero: sin ORDER BY, un scan por orden de inserción
                # devolvería 'b' — la aserción exige la MENOR ('a').
                [{"url": "https://h11.example.ch/b", "title": "B", "company": "B"}],
                ledger=led,
            )
            await s.commit()
            vid = led[0].vacancy_id
            src = (
                await s.execute(
                    sa.text("SELECT id FROM sources WHERE name = :n"),
                    {"n": ip.PORTFOLIO_IMPORT_SOURCE},
                )
            ).scalar_one()
            lid = uuid.uuid4()
            await s.execute(
                sa.text(
                    "INSERT INTO source_listings (id, source_id, external_id, url_normalized) "
                    "VALUES (:i, :s, 'h11-a', 'https://h11.example.ch/a')"
                ),
                {"i": lid, "s": src},
            )
            await s.execute(
                sa.text(
                    "INSERT INTO source_listing_incarnations "
                    "(id, source_listing_id, vacancy_id, seq, url) "
                    "VALUES (:i, :l, :v, 1, 'https://h11.example.ch/a')"
                ),
                {"i": uuid.uuid4(), "l": lid, "v": vid},
            )
            await s.commit()
            # Literal inline (sin binds: los ':' del propio SQL — 'vid:',
            # ::text — confunden a sa.text con parámetros).
            col = f"'{vid}'::uuid"
            for expr in (
                man._VAC_URLN.format(col=col),
                ipm._vac_key(col),  # el coalesce entero: aquí resuelve la subquery
            ):
                key = (
                    await s.execute(sa.text(f"SELECT {expr}"))
                ).scalar_one()
                assert key == "https://h11.example.ch/a", expr  # la MENOR, siempre

    asyncio.run(_on_disposable_db(_run))


def test_persist_manifest_nan_in_staged_value_persists():
    """Regresión G2-P2-1 (repro DIRECTA del informe): el saneo de G1-P2-1 cubría
    el NUL pero NO los floats no finitos — `json.dumps` (allow_nan por defecto)
    emite los tokens `NaN`/`Infinity`, que no son JSON y el CAST a jsonb rechaza:
    la transacción ENTERA del cutover moría justo al registrar la cuarentena que
    el staging ya había aislado. Ahora el no finito se persiste como texto."""
    from jobhunt_core import import_portfolio_manifest as man

    manifest = {
        "verdict": "ok",
        "staging": [{
            "kind": "application", "reason": "malformed",
            "durable": {"url": "https://x.ch/a", "salary_min": float("nan"),
                        "salary_max": float("inf")},
        }],
    }

    async def _run(factory):
        async with factory() as s:
            mid = await man.persist_manifest(s, manifest)  # antes: DBAPIError (jsonb)
            await s.commit()
            stored = (
                await s.execute(
                    sa.text(
                        "SELECT manifest FROM portfolio_migration_manifest "
                        "WHERE id = :i"
                    ),
                    {"i": mid},
                )
            ).scalar_one()
            durable = stored["staging"][0]["durable"]
            assert durable["salary_min"] == "nan"  # visible en la auditoría
            assert durable["salary_max"] == "inf"

    asyncio.run(_on_disposable_db(_run))


def test_migration_survives_nan_durable_and_nan_min_score():
    """Regresión G2-P2-1 (extremo a extremo): un export con un NaN en un campo
    del durable (Postgres numeric ADMITE NaN; json.loads materializa el token)
    y otro en `min_score` mataba el cutover por dos vías — el CAST del manifiesto
    y `int(float('nan'))` → ValueError, este último ANTES de veredicto. La
    migración termina ahora con veredicto y el manifiesto persistido; min_score
    degrada al default con el MISMO helper en ambos lados (sin falso divergent)."""
    from jobhunt_core import import_portfolio_manifest as man

    users = [
        {
            "external_ref": 1,
            "applications": [
                {"url": "https://good.example.ch/nan1", "status": "applied",
                 "title": "A", "company": "Acme",
                 "created_at": datetime(2026, 6, 1, tzinfo=timezone.utc)},
                # company NaN: el sink lo cuarentena (canonical_payload usa
                # allow_nan=False) ⇒ el durable ÍNTEGRO —con el NaN— va a staging
                # y de ahí al manifiesto.
                {"url": "https://nan.example.ch/nan2", "status": "saved",
                 "title": "B", "company": float("nan"),
                 "created_at": datetime(2026, 6, 2, tzinfo=timezone.utc)},
            ],
            "saved_searches": [
                {"name": "búsqueda con min_score tóxico", "filters": {"q": "dev"},
                 "min_score": float("nan"), "is_active": True},
            ],
        }
    ]

    async def _run(factory):
        async with factory() as s:
            manifest = await man.migrate_and_reconcile(s, users)  # NO debe lanzar
            assert manifest["verdict"] == "ok", manifest["divergences"]
            await s.commit()
            stored = (
                await s.execute(
                    sa.text(
                        "SELECT manifest FROM portfolio_migration_manifest "
                        "WHERE id = :i"
                    ),
                    {"i": uuid.UUID(manifest["id"])},
                )
            ).scalar_one()
            assert "NaN" not in json.dumps(stored, ensure_ascii=False)
            min_score = (
                await s.execute(
                    sa.text("SELECT min_score FROM saved_searches")
                )
            ).scalar_one()
            assert min_score == 0  # degradado al default, no un crash

    asyncio.run(_on_disposable_db(_run))


def test_ledger_over_limit_url_with_surrogate_reason_is_malformed():
    """Regresión G2-P3-2 (residual de G1-P3-5): una url que ADEMÁS de superar
    los 2048 bytes porta un surrogate en el FRAGMENTO (que normalize_url
    descarta, así que no revienta antes) quedaba registrada como 'limit'. El
    sink real la cuarentena como TÓXICA (encode estricto en _preprocess) y la
    precedencia declarada del propio módulo dice malformed > limit: la razón
    AUDITABLE mentía en la esquina compuesta. Ahora la toxicidad se comprueba
    ANTES del límite, en el MISMO orden que el sink."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            url = "https://x.example.ch/" + "a" * 2100 + "#\ud800"
            assert len(url.encode("utf-8", "surrogatepass")) > 2048
            led: list = []
            await ip.synthesize_vacancies(
                s, scope_id, [{"url": url, "title": "U", "company": "A"}], ledger=led
            )
            await s.commit()
            e = _by_url(led)[url]
            assert e.disposition == pil.QUARANTINE
            assert e.reason == pil.Q_MALFORMED  # antes: limit (razón falseada)
            # Y la precedencia del módulo coincide con la razón registrada.
            assert ip._group_reason({pil.Q_LIMIT, pil.Q_MALFORMED}) == pil.Q_MALFORMED
            assert ip.durable_synthesizable({"url": url, "title": "U"}) == (
                False, pil.Q_MALFORMED
            )

    asyncio.run(_on_disposable_db(_run))


def test_ledger_payload_toxico_con_url_larga_reason_is_malformed():
    """Misma familia (G2-P3-2): el payload TÓXICO (título con surrogate) sobre
    una url excesiva también daba 'limit'. El sink serializa el payload antes de
    medir los límites, así que la razón real es malformed."""

    async def _run(factory):
        async with factory() as s:
            scope_id = await ip.ensure_import_scope(s)
            await s.commit()
            url = "https://y.example.ch/" + "b" * 2100
            led: list = []
            await ip.synthesize_vacancies(
                s, scope_id,
                [{"url": url, "title": "T\ud800", "company": "A"}],
                ledger=led,
            )
            await s.commit()
            e = _by_url(led)[url]
            assert e.disposition == pil.QUARANTINE
            assert e.reason == pil.Q_MALFORMED  # antes: limit

    asyncio.run(_on_disposable_db(_run))
