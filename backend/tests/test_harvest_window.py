"""Tests de la ventana de cosecha (V.2 + V.3 / ADR-10 rev. J1).

El test de completitud es el DoD de V.3: NINGUNA fuente registrada puede
existir sin una política explícita en `harvest_window._POLICIES` — un default
silencioso reintroduciría el problema que ADR-10 corrige.

Rev. J1: la ventana aplica en TODOS los runs y solo a ALTAS — `accepts` ya no
sabe de bootstrap; distinguir alta de re-vista es de `JobRepository.exists`.
"""

import logging
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from config import Settings, settings
from providers import _PROVIDER_CLASSES, get_all_providers
from scrapers import _SCRAPER_CLASSES, get_all_scrapers
from services import harvest_window
from services.harvest_window import (
    ACCEPT,
    FULL,
    SKIP_NO_DATE,
    SKIP_STALE,
    WINDOW,
    HarvestPolicy,
    WindowPrecheck,
)

# Reloj fijo para testear el borde exacto de la ventana sin congelar nada.
_NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)


def _live_sources() -> set[str]:
    """Claves de fuente vivas en el código: instancias + registros de clases
    (cubre las gated por API key, que get_all_providers() omite)."""
    live = {p.get_source_name() for p in get_all_providers()}
    live |= {s.get_source_name() for s in get_all_scrapers()}
    live |= set(_PROVIDER_CLASSES) | set(_SCRAPER_CLASSES)
    return live


class TestRegistroCompleto:
    """DoD de V.3 — sin default silencioso: toda fuente registrada tiene
    política explícita."""

    def test_toda_fuente_registrada_tiene_politica(self):
        missing = sorted(_live_sources() - set(harvest_window._POLICIES))
        assert not missing, (
            f"Fuentes SIN política de cosecha de bootstrap: {missing}. "
            "Añade cada una a _POLICIES en services/harvest_window.py como "
            "HarvestPolicy(WINDOW|FULL, motivo) según exponga o no "
            "published_at (ver INVENTARIO_PUBLISHED_AT). ADR-10 prohíbe el "
            "default silencioso: una fuente nueva no entra sin decisión."
        )

    def test_toda_politica_es_fuente_viva_o_predecidida(self):
        """K10 — completitud INVERSA: una fuente renombrada dejaría una
        entrada huérfana en _POLICIES sin aviso. Toda clave del registro debe
        estar en el código vivo o en la lista explícita de pre-decididas."""
        huerfanas = sorted(
            set(harvest_window._POLICIES)
            - _live_sources()
            - harvest_window.SOURCES_DECIDED_IN_ADVANCE
        )
        assert not huerfanas, (
            f"Entradas de _POLICIES sin fuente viva ni pre-decisión: {huerfanas}. "
            "Si la fuente se renombró, renombra su clave; si se deshabilitó a "
            "propósito, añádela a SOURCES_DECIDED_IN_ADVANCE."
        )

    def test_predecididas_no_solapan_con_el_codigo_vivo(self):
        """K10 — higiene de la lista: al reactivar una fuente pre-decidida hay
        que sacarla de SOURCES_DECIDED_IN_ADVANCE, o la lista se pudre."""
        solapadas = sorted(harvest_window.SOURCES_DECIDED_IN_ADVANCE & _live_sources())
        assert not solapadas, (
            f"Fuentes reactivadas que siguen en SOURCES_DECIDED_IN_ADVANCE: {solapadas}"
        )

    def test_toda_politica_lleva_motivo(self):
        # El registro es la documentación de la decisión: sin motivo no vale.
        sin_motivo = sorted(
            key
            for key, policy in harvest_window._POLICIES.items()
            if not policy.reason.strip()
        )
        assert not sin_motivo, f"Políticas sin motivo registrado: {sin_motivo}"

    def test_thehub_es_window(self):
        """VD.9 (2026-08-15): la API v2 de thehub confirmó `createdAt` como
        fecha de publicación (la página pública lo muestra como 'Posted') —
        la fuente dejó de ser INCIERTO/FULL."""
        assert harvest_window.policy_for("thehub").mode == WINDOW

    def test_gastrojob_es_window(self):
        """VD.4b (2026-08-15): el endpoint AJAX del listado imprime la primera
        activación de cada anuncio ('Erstmals aktiviert') y el detalle la
        confirma como datePosted de la microdata schema.org/JobPosting — la
        fuente dejó de ser INCIERTO/FULL."""
        assert harvest_window.policy_for("gastrojob").mode == WINDOW

    def test_colegios_son_full(self):
        """La excepción de ADR-10: los 8 swiss_schools_* se cosechan enteros
        en el bootstrap — swiss_schools_isp incluido (postedOn no es fecha)."""
        schools = [
            k for k in harvest_window._POLICIES if k.startswith("swiss_schools_")
        ]
        assert len(schools) == 8
        for key in schools:
            assert harvest_window._POLICIES[key].mode == FULL, key


class TestPolicyFor:
    def test_fuente_registrada_devuelve_su_politica(self):
        assert harvest_window.policy_for("ostjob").mode == WINDOW
        assert harvest_window.policy_for("swiss_schools_nae").mode == FULL

    def test_fuente_no_registrada_full_y_error_logueado(self, caplog):
        """Seguro por defecto (FULL: no perder nada) pero NUNCA en silencio."""
        with caplog.at_level(logging.ERROR, logger="services.harvest_window"):
            policy = harvest_window.policy_for("fuente_fantasma")
        assert policy.mode == FULL
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errores, "policy_for no logueó ERROR para la fuente sin registrar"
        assert any("fuente_fantasma" in r.getMessage() for r in errores)


class TestAcceptsMatriz:
    """`accepts` en su matriz: WINDOW/FULL × fecha dentro/fuera/ausente,
    incluido el borde exacto de la ventana. Rev. J1: sin noción de bootstrap
    — el veredicto es el mismo en cualquier run."""

    def test_window_fecha_dentro_entra(self):
        reciente = _NOW - timedelta(days=2)
        assert harvest_window.accepts("ostjob", reciente, now=_NOW)

    def test_window_fecha_fuera_no_entra(self):
        """J1 — la ventana ya no es del bootstrap: una oferta vieja no entra,
        punto (que la fuente tenga o no corpus es asunto del pipeline)."""
        vieja = _NOW - timedelta(days=30)
        assert not harvest_window.accepts("ostjob", vieja, now=_NOW)

    def test_window_sin_fecha_no_entra(self):
        """Política WINDOW sin published_at = anomalía ⇒ no entra."""
        assert not harvest_window.accepts("ostjob", None, now=_NOW)

    def test_borde_exacto_justo_dentro_y_justo_fuera(self, monkeypatch):
        monkeypatch.setattr(settings, "HARVEST_WINDOW_DAYS", 7)
        borde = _NOW - timedelta(days=7)
        # El borde exacto (now - 7d) queda DENTRO (>= cutoff)...
        assert harvest_window.accepts("ostjob", borde, now=_NOW)
        # ...y un microsegundo más viejo, FUERA.
        justo_fuera = borde - timedelta(microseconds=1)
        assert not harvest_window.accepts("ostjob", justo_fuera, now=_NOW)

    def test_full_entra_todo(self):
        """FULL (colegios / sin fecha): entra todo, incluso sin fecha."""
        vieja = _NOW - timedelta(days=365)
        assert harvest_window.accepts("swiss_schools_nae", vieja, now=_NOW)
        assert harvest_window.accepts("swiss_schools_nae", None, now=_NOW)

    def test_ventana_configurable(self, monkeypatch):
        """La ventana lee HARVEST_WINDOW_DAYS, no un 7 cableado."""
        monkeypatch.setattr(settings, "HARVEST_WINDOW_DAYS", 30)
        hace_20_dias = _NOW - timedelta(days=20)
        assert harvest_window.accepts("ostjob", hace_20_dias, now=_NOW)

    def test_interruptor_apagado_acepta_todo(self, monkeypatch):
        """HARVEST_WINDOW_ENABLED=False ⇒ comportamiento de siempre: entra
        todo, también lo viejo y lo sin fecha de una fuente WINDOW."""
        monkeypatch.setattr(settings, "HARVEST_WINDOW_ENABLED", False)
        vieja = _NOW - timedelta(days=365)
        assert harvest_window.accepts("ostjob", vieja, now=_NOW)
        assert harvest_window.accepts("ostjob", None, now=_NOW)

    def test_fecha_naive_se_trata_como_sin_fecha(self):
        """K9 — una datetime SIN zona no puede compararse con el corte aware:
        antes lanzaba TypeError FUERA del savepoint por-oferta y perdía el
        lote entero de la fuente. Se trata como "sin fecha": no entra en una
        WINDOW (anomalía) y sí en una FULL."""
        naive = datetime(2026, 8, 13, 12, 0, 0)  # sin tzinfo, reciente
        assert not harvest_window.accepts("ostjob", naive, now=_NOW)
        assert harvest_window.accepts("swiss_schools_nae", naive, now=_NOW)

    def test_valor_no_datetime_se_trata_como_sin_fecha(self):
        """K9 — cualquier valor que no sea datetime aware (p. ej. un string
        que se coló sin parsear) tampoco revienta la comparación."""
        assert not harvest_window.accepts("ostjob", "2026-08-13", now=_NOW)


def _precheck(
    *,
    accepted: int = 0,
    skipped_by_date: int = 0,
    skipped_no_date: int = 0,
    saw_published_at: bool = False,
    recognized: int = 0,
    policy_mode: str = WINDOW,
) -> WindowPrecheck:
    """WindowPrecheck sintético con veredictos coherentes con los contadores."""
    verdicts = (
        [ACCEPT] * accepted
        + [SKIP_STALE] * skipped_by_date
        + [SKIP_NO_DATE] * skipped_no_date
    )
    return WindowPrecheck(
        verdicts,
        skipped_by_date,
        skipped_no_date,
        saw_published_at,
        recognized,
        policy_mode,
    )


class TestGuardarrailNoDate:
    """Guardarraíl de visibilidad (rev. J1: evalúa en TODOS los runs): fuente
    WINDOW cuyo run acaba sin que NINGUNA oferta trajera fecha ⇒ ERROR de
    política mal asignada (inventario obsoleto)."""

    def test_window_todo_sin_fecha_loguea_error(self, caplog):
        # Lote >= HARVEST_ALERT_MIN_BATCH (L4): el guardarraíl ahora exige
        # tamaño mínimo para no gritar por un hipo en un lote de 1-2.
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                _precheck(skipped_no_date=12, saw_published_at=False),
                new_count=0,
            )
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errores, "faltó el ERROR de política mal asignada"
        assert any("ostjob" in r.getMessage() for r in errores)

    def test_revistas_sin_fecha_tambien_alertan(self, caplog):
        """J1 — fuera del bootstrap las ofertas sin fecha ya en el corpus se
        ACEPTAN (re-vistas), así que la señal no puede ser "todo lo descartado
        iba sin fecha": tiene que mirar el lote ENTERO. Un run con re-vistas
        aceptadas pero NINGUNA fecha en el lote sigue gritando."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                # 9 re-vistas sin fecha que pasaron por el upsert + 3 altas
                # sin fecha rechazadas (lote >= HARVEST_ALERT_MIN_BATCH, L4).
                _precheck(accepted=9, skipped_no_date=3, saw_published_at=False),
                new_count=0,
            )
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errores, "faltó el ERROR con el lote entero sin fechas"

    def test_window_con_alguna_fecha_no_alerta(self, caplog):
        """Si al menos una oferta trajo fecha y el run consiguió altas, la
        política sigue siendo plausible: solo la línea informativa, sin ERROR
        y sin el WARNING de K2."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                # Lote >= HARVEST_ALERT_MIN_BATCH (L4): el silencio se debe a
                # las fechas y a las altas, no al tamaño del lote.
                _precheck(
                    accepted=4,
                    skipped_by_date=5,
                    skipped_no_date=3,
                    saw_published_at=True,
                ),
                new_count=1,
            )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert [r for r in caplog.records if r.levelno == logging.INFO]

    def test_full_sin_fechas_no_alerta(self, caplog):
        """Un colegio sin fechas es lo esperado: nada de ERROR."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "swiss_schools_nae",
                _precheck(accepted=4, saw_published_at=False, policy_mode=FULL),
                new_count=2,
            )
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    def test_interruptor_apagado_sin_guardarrail(self, monkeypatch, caplog):
        """Con la ventana apagada no hay política que vigilar: sin ERROR."""
        monkeypatch.setattr(settings, "HARVEST_WINDOW_ENABLED", False)
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                # Lote >= HARVEST_ALERT_MIN_BATCH: el silencio se debe SOLO
                # al interruptor, no al tamaño del lote (L4).
                _precheck(skipped_no_date=12, saw_published_at=False),
                new_count=0,
            )
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]


class TestGuardarrailParcialFechas:
    """K2 — el escalón intermedio: el guardarraíl total (ERROR) solo cubre el
    lote SIN NINGUNA fecha; el fallo probable es el parcial (rediseño que
    rompe el JSON-LD solo en las páginas nuevas de schuljobs): las re-vistas
    traen fecha (el ERROR calla) y TODAS las altas caen a window_no_date."""

    def test_fallo_parcial_todas_las_altas_sin_fecha_warning(self, caplog):
        """El escenario schuljobs: hubo fechas en el lote (re-vistas), hubo
        descartes por falta de fecha y NI UN alta ⇒ WARNING desde el primer
        run, no ERROR."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "schuljobs",
                _precheck(accepted=8, skipped_no_date=3, saw_published_at=True),
                new_count=0,
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "faltó el WARNING del fallo parcial de fechas (K2)"
        assert any(
            "schuljobs" in r.getMessage() and "falta de fecha" in r.getMessage()
            for r in warnings
        )
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    def test_caso_sano_con_altas_en_silencio(self, caplog):
        """Anti-falso-positivo: si el run SÍ consiguió altas, un descarte
        suelto por falta de fecha no grita."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "schuljobs",
                # Lote y descartes por encima de los umbrales de L4: el
                # silencio se debe a que HUBO altas, no a los mínimos.
                _precheck(accepted=8, skipped_no_date=3, saw_published_at=True),
                new_count=3,
            )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_caso_sano_descartes_solo_por_fecha_en_silencio(self, caplog):
        """Anti-falso-positivo: una fuente WINDOW sana que descarta el 80 % de
        su listado POR FECHA sin conseguir altas es lo normal en régimen
        estacionario — silencio (solo la línea INFO)."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                _precheck(accepted=2, skipped_by_date=8, saw_published_at=True),
                new_count=0,
            )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert [r for r in caplog.records if r.levelno == logging.INFO]


class TestUmbralMinimoDeLote:
    """L4 — los dos escalones de alerta de fechas exigen tamaño mínimo de
    lote (`HARVEST_ALERT_MIN_BATCH`, compartido con el detector de deriva) y
    el WARNING exige además >= 3 descartes sin fecha. Sin esto: un lote de
    1-2 ofertas de un scraper con early-stop cuyo parseo de fecha falle de
    forma transitoria disparaba el ERROR de "política mal asignada", y una
    sola oferta perenne sin fecha producía el WARNING cada run."""

    def test_lote_pequeno_sin_fechas_no_dispara_error(self, caplog):
        """Hipo transitorio: 2 ofertas sin fecha (early-stop) ⇒ silencio,
        no el diagnóstico grave de inventario obsoleto."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                _precheck(skipped_no_date=2, saw_published_at=False),
                new_count=0,
            )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_lote_pequeno_con_descartes_sin_fecha_no_dispara_warning(self, caplog):
        """Lote pequeño con descartes por falta de fecha y 0 altas ⇒ ninguno
        de los dos escalones grita."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "schuljobs",
                _precheck(accepted=2, skipped_no_date=4, saw_published_at=True),
                new_count=0,
            )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_lote_grande_sin_fechas_sigue_gritando(self, caplog):
        """Con lote >= HARVEST_ALERT_MIN_BATCH el ERROR grita igual que
        antes: el umbral no acalla el caso real."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "ostjob",
                _precheck(skipped_no_date=10, saw_published_at=False),
                new_count=0,
            )
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("ostjob" in r.getMessage() for r in errores)

    def test_lote_grande_fallo_parcial_sigue_gritando(self, caplog):
        """Con lote grande y >= 3 sin fecha el WARNING de K2 grita igual."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "schuljobs",
                _precheck(accepted=8, skipped_no_date=3, saw_published_at=True),
                new_count=0,
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("schuljobs" in r.getMessage() for r in warnings)

    def test_menos_de_tres_sin_fecha_no_dispara_warning(self, caplog):
        """Residual documentado de L4: la oferta perenne sin fecha (1-2 por
        listado) en un run tranquilo YA no produce el WARNING indefinido —
        el mínimo es >= 3 descartes sin fecha, no "más de cero"."""
        with caplog.at_level(logging.INFO, logger="services.harvest_window"):
            harvest_window.log_window_summary(
                "schuljobs",
                _precheck(accepted=10, skipped_no_date=2, saw_published_at=True),
                new_count=0,
            )
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def _repo_job(h: str, source: str, url: str) -> dict:
    return {
        "hash": h,
        "source": source,
        "title": "Editor",
        "company": "Acme",
        "url": url,
        "description": "Edit content for our Swiss audience.",
    }


class TestKnownHashes:
    """K5 — el filtro solo puede rechazar ALTAS: distinguir alta de re-vista
    es UNA búsqueda por lote de claves primarias (`JobRepository.known_hashes`,
    sustituye al `exists` por-oferta)."""

    async def test_known_hashes_devuelve_solo_los_existentes(self, db_session):
        from services.job_repository import JobRepository

        repo = JobRepository(db_session)
        h1 = "h_window_k5_a".ljust(32, "0")
        h2 = "h_window_k5_b".ljust(32, "0")
        desconocido = "h_window_k5_x".ljust(32, "0")

        await repo.upsert_job(_repo_job(h1, "fuente_k5", "http://k5.test/1"))
        await repo.upsert_job(_repo_job(h2, "fuente_k5", "http://k5.test/2"))
        await db_session.commit()

        assert await repo.known_hashes({h1, h2, desconocido}) == {h1, h2}
        # Conjunto vacío: sin consulta y sin resultado.
        assert await repo.known_hashes(set()) == set()

    async def test_count_jobs_por_fuente(self, db_session):
        """K1/L1 — `count_jobs` (sustituye a `has_any_job`): la deriva exige
        un corpus COMPARABLE al lote, no un "hay alguna fila"."""
        from services.job_repository import JobRepository

        repo = JobRepository(db_session)
        assert await repo.count_jobs("fuente_k1") == 0

        for i in range(2):
            await repo.upsert_job(
                _repo_job(
                    f"h_window_k1_{i}".ljust(32, "0"),
                    "fuente_k1",
                    f"http://k1.test/{i}",
                )
            )
        await db_session.commit()

        assert await repo.count_jobs("fuente_k1") == 2
        assert await repo.count_jobs("otra_fuente_sin_filas") == 0


class TestPrecheckBatch:
    """K5/K6/K8 — la pre-pasada por lote: una consulta por fuente, veredictos
    explícitos y la política resuelta una sola vez."""

    async def test_veredictos_y_una_sola_consulta(self, db_session, monkeypatch):
        """Lote mixto de una fuente WINDOW: la re-vista fuera de ventana se
        reconoce (ACCEPT) con UNA única llamada a known_hashes que solo lleva
        los hashes candidatos a descarte."""
        from services.job_repository import JobRepository

        repo = JobRepository(db_session)
        h_revista = "h_precheck_revista".ljust(32, "0")
        await repo.upsert_job(_repo_job(h_revista, "ostjob", "http://pre.test/revista"))
        await db_session.commit()

        calls: list[set[str]] = []
        original = JobRepository.known_hashes

        async def counting(self, hashes):
            calls.append(set(hashes))
            return await original(self, hashes)

        monkeypatch.setattr(JobRepository, "known_hashes", counting)

        jobs = [
            {"hash": "h_precheck_in".ljust(32, "0"), "published_at": _NOW},
            {
                "hash": "h_precheck_stale".ljust(32, "0"),
                "published_at": _NOW - timedelta(days=30),
            },
            {"hash": h_revista, "published_at": _NOW - timedelta(days=30)},
            {"hash": "h_precheck_nodate".ljust(32, "0"), "published_at": None},
        ]
        precheck = await harvest_window.precheck_batch("ostjob", jobs, repo, now=_NOW)

        assert precheck.verdicts == [ACCEPT, SKIP_STALE, ACCEPT, SKIP_NO_DATE]
        assert precheck.skipped_by_date == 1
        assert precheck.skipped_no_date == 1
        assert precheck.recognized == 1
        assert precheck.saw_published_at is True
        assert precheck.policy_mode == WINDOW
        # UNA consulta, y solo con los hashes que caerían descartados.
        assert len(calls) == 1
        assert calls[0] == {
            "h_precheck_stale".ljust(32, "0"),
            h_revista,
            "h_precheck_nodate".ljust(32, "0"),
        }

    async def test_matriz_de_accepts_via_precheck_batch(self, monkeypatch):
        """L6 — la matriz de la regla atacando la ruta de PRODUCCIÓN
        (`precheck_batch`), no solo el predicado documental `accepts`:
        WINDOW × (dentro / borde exacto / justo fuera / sin fecha / naive) y
        FULL × (vieja / sin fecha). Repo sin corpus: ninguna re-vista."""
        monkeypatch.setattr(settings, "HARVEST_WINDOW_DAYS", 7)

        class _RepoVacio:
            async def known_hashes(self, hashes):
                return set()

        borde = _NOW - timedelta(days=7)
        window_jobs = [
            {"hash": "h_m_in".ljust(32, "0"), "published_at": _NOW - timedelta(days=2)},
            {"hash": "h_m_borde".ljust(32, "0"), "published_at": borde},
            {
                "hash": "h_m_fuera".ljust(32, "0"),
                "published_at": borde - timedelta(microseconds=1),
            },
            {"hash": "h_m_nodate".ljust(32, "0"), "published_at": None},
            # K9 — naive se trata como "sin fecha" también en esta ruta.
            {
                "hash": "h_m_naive".ljust(32, "0"),
                "published_at": datetime(2026, 8, 13, 12, 0, 0),
            },
        ]
        precheck = await harvest_window.precheck_batch(
            "ostjob", window_jobs, _RepoVacio(), now=_NOW
        )
        assert precheck.verdicts == [
            ACCEPT,
            ACCEPT,
            SKIP_STALE,
            SKIP_NO_DATE,
            SKIP_NO_DATE,
        ]
        assert precheck.skipped_by_date == 1
        assert precheck.skipped_no_date == 2

        full_jobs = [
            {
                "hash": "h_m_full_old".ljust(32, "0"),
                "published_at": _NOW - timedelta(days=365),
            },
            {"hash": "h_m_full_nd".ljust(32, "0"), "published_at": None},
        ]
        precheck_full = await harvest_window.precheck_batch(
            "swiss_schools_nae", full_jobs, _RepoVacio(), now=_NOW
        )
        assert precheck_full.verdicts == [ACCEPT, ACCEPT]
        assert precheck_full.policy_mode == FULL

    async def test_fuente_sin_registrar_un_solo_error_por_lote(self, caplog):
        """K8 — la política se resuelve UNA vez por fuente: una fuente sin
        registrar con un lote de 500 emitía ~501 ERRORs por run (fatiga de
        alertas); ahora emite exactamente uno."""

        class _RepoNuncaLlamado:
            async def known_hashes(self, hashes):  # pragma: no cover
                raise AssertionError("FULL no consulta la BD")

        jobs = [
            {"hash": f"h_k8_{i}".ljust(32, "0"), "published_at": None}
            for i in range(500)
        ]
        with caplog.at_level(logging.ERROR, logger="services.harvest_window"):
            precheck = await harvest_window.precheck_batch(
                "fuente_fantasma_k8", jobs, _RepoNuncaLlamado(), now=_NOW
            )
        assert precheck.verdicts == [ACCEPT] * 500  # FULL: no perder nada
        errores = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errores) == 1, (
            f"la política debe resolverse una vez por fuente, no {len(errores)}"
        )

    async def test_interruptor_apagado_acepta_sin_consultar(self, monkeypatch):
        monkeypatch.setattr(settings, "HARVEST_WINDOW_ENABLED", False)

        class _RepoNuncaLlamado:
            async def known_hashes(self, hashes):  # pragma: no cover
                raise AssertionError("con la ventana apagada no hay consulta")

        jobs = [{"hash": "h_off".ljust(32, "0"), "published_at": None}]
        precheck = await harvest_window.precheck_batch(
            "ostjob", jobs, _RepoNuncaLlamado(), now=_NOW
        )
        assert precheck.verdicts == [ACCEPT]
        assert precheck.skipped_by_date == 0
        assert precheck.skipped_no_date == 0


class TestConfigVentana:
    """K4 — HARVEST_WINDOW_DAYS validado: con 0 el corte es "ahora" (solo
    entrarían ofertas del futuro) y con negativo ni esas — un typo en el .env
    dejaría TODAS las fuentes WINDOW sin altas, con un contador como único
    rastro."""

    def test_dias_cero_rechazado(self):
        with pytest.raises(ValidationError):
            Settings(HARVEST_WINDOW_DAYS=0)

    def test_dias_negativos_rechazados(self):
        with pytest.raises(ValidationError):
            Settings(HARVEST_WINDOW_DAYS=-3)

    def test_un_dia_es_valido(self):
        assert Settings(HARVEST_WINDOW_DAYS=1).HARVEST_WINDOW_DAYS == 1

    def test_umbral_alertas_validado(self):
        # L4 — renombrado a HARVEST_ALERT_MIN_BATCH: ahora sirve a las tres
        # alertas (deriva + los dos escalones de fechas).
        with pytest.raises(ValidationError):
            Settings(HARVEST_ALERT_MIN_BATCH=0)


def test_harvest_policy_forma():
    """La política lleva el motivo en el propio dato (NamedTuple mode+reason)."""
    policy = HarvestPolicy(WINDOW, "motivo")
    assert policy.mode == WINDOW
    assert policy.reason == "motivo"
