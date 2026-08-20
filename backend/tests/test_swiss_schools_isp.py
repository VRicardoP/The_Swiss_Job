"""Tests para SwissSchoolsISPScraper (Workday API del grupo ISP).

Regresión clave: ISP extiende `BaseJobProvider` (no `BaseScraper`), así que el
presupuesto de páginas (`_pages_budget`) debe existir en la base común. Antes
vivía solo en `BaseScraper` y `_fetch_workday` crasheaba con AttributeError.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from scrapers.swiss_schools_isp import SwissSchoolsISPScraper
from utils import fetch_diagnostics as diag


def _workday_response(postings: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value={"jobPostings": postings})
    return resp


class TestISPPagesBudget:
    """El presupuesto heredado de BaseJobProvider funciona en ISP."""

    def test_pages_budget_available(self):
        scraper = SwissSchoolsISPScraper()
        # Sin inyección → MAX_PAGES (regresión del AttributeError).
        assert scraper._pages_budget() == SwissSchoolsISPScraper.MAX_PAGES

    def test_injected_budget_clamped_to_max_pages(self):
        scraper = SwissSchoolsISPScraper()
        scraper._max_pages_this_run = 99
        assert scraper._pages_budget() == SwissSchoolsISPScraper.MAX_PAGES

    def test_injected_budget_limits_workday_pages(self):
        scraper = SwissSchoolsISPScraper()
        scraper._max_pages_this_run = 1
        # Página llena (PAGE_SIZE postings) → sin presupuesto pediría la 2ª.
        full_page = [
            {
                "title": f"Teacher {i}",
                "locationsText": "Mosaic School / Ecole Mosaic",
                "externalPath": f"/job/{i}",
                "bulletFields": [f"JR{i}"],
            }
            for i in range(SwissSchoolsISPScraper.PAGE_SIZE)
        ]

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(full_page),
        ) as mock_call:
            import asyncio

            jobs = asyncio.run(scraper.fetch_jobs(""))

        # Con presupuesto=1, cada colegio pide exactamente 1 página (no la 2ª
        # pese a venir llena). Sin acoplar al nº de colegios de la config.
        assert mock_call.await_count == len(scraper._schools)
        assert len(jobs) >= 1


class TestISPFetch:
    @pytest.mark.asyncio
    async def test_fetch_normalizes_mosaic_jobs(self):
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": "Primary Teacher",
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/Primary-Teacher_JR123",
                "bulletFields": ["JR123"],
            }
        ]

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        job = jobs[0]
        assert job["source"] == "swiss_schools_isp"
        assert job["title"] == "Primary Teacher"
        assert job["language"] == "en"
        assert "myworkdayjobs.com" in job["url"]
        assert job["hash"]

    @pytest.mark.asyncio
    async def test_fetch_filters_out_other_schools(self):
        # locationsText sin el school_filter ("mosaic") se descarta.
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": "Teacher Elsewhere",
                "locationsText": "Some Other Campus, Zurich",
                "externalPath": "/job/x",
                "bulletFields": ["JR999"],
            }
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        # G2: el tenant de Workday es COMPARTIDO — objetos válidos con 0 matches
        # del filtro de colegio es lo normal, no un fallo. Por eso el guard de
        # "página sin objetos" debe mirar el TIPO (`parseable`) y NUNCA el
        # filtro (`results`): si mirara el filtro, este vacío legítimo saldría
        # `error` con issue y la fuente seca no podría rehabilitarse.
        assert diag.issues() == []
        assert diag.classify(0, diag.issues()) == "empty"


class TestISPFetchDiagnostics:
    """VD.10 (H1) — ISP no hereda de BaseScraper y hacía bypass de la costura
    `_request_with_retry`: sus fallos de descarga no registraban NADA y
    `classify(0, [])` devolvía `empty` — la fuente ROTA se presentaba como
    fuente SECA. Cada test afirma el veredicto final, no solo el issue."""

    @pytest.mark.asyncio
    async def test_network_error_records_issue_and_is_error(self):
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Workday down"),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        # Un issue por colegio roto (cada uno corta con `break` tras registrar).
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "myworkdayjobs.com" in issues[0].url
        assert "ConnectError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_http_500_records_issue_and_is_error(self):
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 500
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_HTTP
        assert issues[0].status == 500
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_invalid_json_body_records_issue_and_is_error(self):
        # 200 con cuerpo no-JSON (redeploy/WAF sirviendo HTML): antes lanzaba
        # sin proteger; ahora es fallo visible de descarga, no un board vacío.
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            side_effect=json.JSONDecodeError("Expecting value", "<html>", 0)
        )
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "JSONDecodeError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_non_utf8_body_records_issue_and_is_error(self):
        """Fase 3 r3/H4: un 200 cuyo cuerpo no es UTF-8 (b'\\xff') hace que
        resp.json() lance UnicodeDecodeError — subclase de ValueError pero NO
        de JSONDecodeError — que ESCAPABA del parser: 0 issues y el run salía
        `empty` (falso vacío, G1). Misma rama y mismo veredicto que el JSON
        malformado."""
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(
            side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        )
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "UnicodeDecodeError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_anidamiento_extremo_registra_issue_y_es_error(self):
        """No bloqueante r6: la pata RecursionError del except estaba sin
        fijar — estrecharla a solo ValueError sobrevivía a toda la batería.
        Un 200 cuyo cuerpo son 100k corchetes anidados hace que el scanner C
        de json lance RecursionError (NO ValueError): no debe escapar y
        registra exactamente un issue por colegio, veredicto `error` (G1)."""
        scraper = SwissSchoolsISPScraper()
        nested_body = '{"jobPostings": ' + "[" * 100_000 + "]" * 100_000 + "}"
        resp = MagicMock()
        resp.status_code = 200
        # side_effect con json REAL: el RecursionError sale del scanner C,
        # no de un mock que lo simule.
        resp.json = MagicMock(side_effect=lambda: json.loads(nested_body))
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "RecursionError" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [None, ["not", "an", "object"]])
    async def test_json_no_objeto_registra_issue_y_es_error(self, body):
        """Fase 4 r4/R3-2: un 200 con JSON válido pero NO-objeto (`null`, una
        lista) hacía que `data.get(...)` lanzara AttributeError que escapaba
        de fetch_jobs con 0 issues (letra de G1). `utils.http` ya trata el
        "200 con cuerpo JSON null"; este es su camino gemelo — mismo guard
        que financejobs y thehub."""
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=body)
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "not an object" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "postings",
        [
            None,  # data.get devuelve el null EXISTENTE, no el default
            "maintenance",  # string donde va la lista
            {"nested": True},  # objeto donde va la lista
        ],
    )
    async def test_job_postings_degenerado_registra_issue_y_es_error(self, postings):
        """Fase 5 r5/H2: el isinstance del nivel superior no cubría
        `jobPostings` degenerado — el TypeError/AttributeError del bucle
        ESCAPABA de fetch_jobs con 0 issues (letra de G1). La API real de
        Workday devuelve lista SIEMPRE (sonda: incluida la búsqueda vacía)."""
        scraper = SwissSchoolsISPScraper()
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"jobPostings": postings})
        diag.begin()

        with patch.object(
            scraper._circuit, "call", new_callable=AsyncMock, return_value=resp
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "jobPostings is not a list" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_job_postings_sin_objetos_registra_issue_y_es_error(self):
        """Fase 5 r5/H2: lista NO vacía sin un solo objeto ([1, 2]) — antes
        `p.get` lanzaba AttributeError que escapaba; con solo el `continue`
        saldría `empty` con 0 issues (material). El guard mira el TIPO, no el
        filtro de colegio: 0 matches con objetos reales sigue siendo legítimo."""
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response([1, 2]),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == len(scraper._schools)
        assert issues[0].kind == diag.KIND_NETWORK
        assert "ninguno" in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_item_degenerado_degrada_el_item_no_la_pagina(self):
        """Fase 5 r5/H2: un elemento no-objeto entre objetos reales degrada
        ESE item, nunca la página — las ofertas válidas se conservan."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            "garbage",
            {
                "title": "Primary Teacher",
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/Primary-Teacher_JR123",
                "bulletFields": ["JR123"],
            },
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Primary Teacher"
        assert diag.issues() == []

    @pytest.mark.asyncio
    async def test_job_postings_vacio_es_empty_sin_issue(self):
        """G2 (NO puede romperse): `jobPostings: []` es la búsqueda vacía REAL
        de Workday (`total: 0`) — vacío legítimo, CERO issues."""
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response([]),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "empty"


class TestISPOfertaCoincidenteIlegible:
    """r7/H1 (G1 material + G3): el cierre anterior solo validaba
    locationsText — una oferta QUE SÍ coincide con el colegio pero con
    `title` no-string o `bulletFields` escalar llegaba a normalize_job y
    `_process_raw_jobs` la descartaba con log SIN issue (evidencia ejecutada:
    `'list' object has no attribute 'strip'` / `'int' object is not
    subscriptable` con class `empty`). Y un `externalPath=""` emitía la
    página de carreras del tenant como URL de oferta (G3: no es una URL
    propia)."""

    MATCHING_VALID = {
        "title": "Primary Teacher",
        "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
        "externalPath": "/job/Primary-Teacher_JR123",
        "bulletFields": ["JR123"],
    }

    @pytest.mark.asyncio
    async def test_title_no_string_degrada_item_con_issue_y_es_error(self):
        """El vector EXACTO de la evidencia (`title=[]`): oferta coincidente
        ilegible ⇒ 1 issue y veredicto `error`, nunca `empty` silencioso."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": [],
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/x_JR1",
                "bulletFields": ["JR1"],
            }
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        # El detail nombra SOLO el campo roto: aquí falla title (una lista)
        # y externalPath es válido, así que no debe aparecer.
        assert "title=list" in issues[0].detail
        assert "externalPath" not in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_external_path_vacio_degrada_item_no_emite_pagina_de_carreras(self):
        """G3: `externalPath=""` producía la página de carreras
        (https://...myworkdayjobs.com/en-US/{site}) como URL de la oferta —
        una URL de listado, no propia. Ahora degrada el item con issue."""
        scraper = SwissSchoolsISPScraper()
        postings = [dict(self.MATCHING_VALID, externalPath="")]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        # Ni oferta con URL de listado, ni `empty` silencioso.
        assert jobs == []
        issues = diag.issues()
        assert len(issues) == 1
        # Solo externalPath (str vacío) está roto; title es válido y no
        # debe aparecer en el detail.
        assert "externalPath=str" in issues[0].detail
        assert "title" not in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.asyncio
    async def test_title_y_external_path_rotos_un_unico_issue(self):
        """Un elemento coincidente inválido registra UN único issue, tenga
        uno o los dos campos rotos — sin duplicar ruido por campo."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": [],
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "",
            }
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == 1
        # Con los dos campos rotos, el ÚNICO issue señala a ambos.
        assert "title=list" in issues[0].detail
        assert "externalPath=str" in issues[0].detail

    @pytest.mark.asyncio
    async def test_bullet_fields_escalar_se_repara_con_external_path(self):
        """El otro vector de la evidencia (`bulletFields=7`): es solo material
        de source_id — la oferta coincidente se EMITE con externalPath como
        identidad, sin issue (la fuente no está rota, el campo es reparable)."""
        scraper = SwissSchoolsISPScraper()
        postings = [dict(self.MATCHING_VALID, bulletFields=7)]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Primary Teacher"
        assert jobs[0]["source_id"] == "/job/Primary-Teacher_JR123"
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "ok"

    @pytest.mark.asyncio
    async def test_item_invalido_no_arrastra_al_valido_coincidente(self):
        """El ilegible degrada SU item: la oferta válida del mismo colegio en
        la misma página sobrevive, con el issue del degradado registrado."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": [],
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/broken",
            },
            self.MATCHING_VALID,
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Primary Teacher"
        assert len(diag.issues()) == 1

    @pytest.mark.asyncio
    async def test_g2_item_invalido_de_otro_colegio_no_se_diagnostica(self):
        """G2 (NO puede romperse): el guard vive DESPUÉS del filtro — un
        elemento con title roto de OTRO colegio del tenant compartido no es
        asunto nuestro: 0 matches con objetos válidos ⇒ `empty` con 0 issues."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": [],
                "locationsText": "Other ISP School, Madrid",
                "externalPath": "",
            },
            {
                "title": "Maths Teacher",
                "locationsText": "Other ISP School, Madrid",
                "externalPath": "/job/Maths_JR9",
                "bulletFields": ["JR9"],
            },
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "empty"

    @pytest.mark.asyncio
    async def test_g2_board_vacio_sigue_siendo_empty_sin_issue(self):
        """G2 (NO puede romperse): `jobPostings: []` sigue siendo el vacío
        legítimo de Workday tras el guard — CERO issues."""
        scraper = SwissSchoolsISPScraper()
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response([]),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "empty"


class TestISPLocationsTextDegenerado:
    """r6/H3: `locationsText` no-string tumbaba el LOTE entero — evidencia
    ejecutada: `{"jobPostings": [{"locationsText": ["Mosaic"]}]}` lanzaba
    AttributeError con issues=0. Un item estructuralmente inválido debe
    degradar ESE item (con issue) y seguir con los objetos válidos."""

    @pytest.mark.asyncio
    async def test_locations_text_no_string_degrada_item_no_pagina(self):
        scraper = SwissSchoolsISPScraper()
        postings = [
            # El vector EXACTO de la evidencia ejecutada.
            {"locationsText": ["Mosaic"]},
            {
                "title": "Primary Teacher",
                "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
                "externalPath": "/job/Primary-Teacher_JR123",
                "bulletFields": ["JR123"],
            },
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        # El objeto válido sobrevive; el degenerado registra SU issue.
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Primary Teacher"
        issues = diag.issues()
        assert len(issues) == 1
        assert "locationsText" in issues[0].detail

    @pytest.mark.asyncio
    async def test_locations_text_ausente_o_none_sigue_siendo_vacio_legitimo(self):
        """G2 (NO puede romperse): el tenant de Workday es COMPARTIDO — cero
        matches con objetos válidos (locationsText ausente/None incluido) es
        un vacío legítimo, jamás un issue. El guard mira el TIPO, no el nº de
        coincidencias del filtro."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            # Posting válido de OTRO colegio del tenant: no matchea el filtro.
            {
                "title": "Maths Teacher",
                "locationsText": "Other ISP School, Madrid",
                "externalPath": "/job/Maths_JR9",
                "bulletFields": ["JR9"],
            },
            # locationsText ausente y None explícito: formas normales de la API.
            {"title": "No Location", "externalPath": "/job/x", "bulletFields": ["J1"]},
            {
                "title": "Null Location",
                "locationsText": None,
                "externalPath": "/job/y",
                "bulletFields": ["J2"],
            },
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "empty"


class TestISPExternalPathShape:
    """Sexta revisión / H1: el guard de tipo dejaba pasar externalPath que NO
    identifican una oferta — la sonda en vivo con externalPath="?job=123"
    persistió una oferta con URL .../ISPCareers?job=123 y outcome=ok. La
    validación ahora exige la FORMA real de Workday (ruta relativa bajo
    /job/, verificada contra la API en vivo), no solo el tipo."""

    MATCHING_VALID = {
        "title": "Primary Teacher",
        "locationsText": "Mosaic School / Ecole Mosaic, Geneva",
        "externalPath": "/job/Primary-Teacher_JR123",
        "bulletFields": ["JR123"],
    }

    @pytest.mark.parametrize(
        ("path", "reason_fragment"),
        [
            ("?job=123", "no empieza por /job/"),  # vector de la sonda en vivo
            ("#job", "no empieza por /job/"),
            ("/not-a-job", "no empieza por /job/"),
            ("job/x_JR1", "no empieza por /job/"),  # relativa sin barra inicial
            ("https://evil.example/job/x", "esquema"),
            ("mailto:hr@example.com", "esquema"),
            ("//evil.example/job/x", "autoridad"),
            ("/job/x?see=1", "query"),
            ("/job/x#frag", "fragmento"),
            ("/job/x\\y", "barra invertida"),
            ("/job/x%5Cy", "barra invertida"),
            ("/job/x%5cy", "barra invertida"),  # forma escapada en minúscula
            ("/job/x\ty", "caracteres de control"),
            ("/job/x\x00y", "caracteres de control"),
            ("/job/x y", "espacios internos"),
            ("/job/../admin", "travesía"),
            ("/job/..", "travesía"),
            ("/job/./x", "travesía"),
            ("/job/x/../y", "travesía"),
            ("/job/%2e%2e/admin", "travesía"),
            ("/job/%2E%2E/admin", "travesía"),
            ("/job/", "segmento vacío"),
            ("/job//x", "segmento vacío"),
            ("/job/x/", "segmento vacío"),
            # 7ª revisión: el encoding se validaba solo en crudo — todos estos
            # pasaban enteros y la API real los responde con 400/404.
            ("/job/x;y", "parámetro de path"),
            ("/job/x%00y", "caracteres de control"),
            ("/job/x%0Ay", "caracteres de control"),
            ("/job/x%7Fy", "caracteres de control"),
            ("/job/x%", "malformado"),
            ("/job/x%2", "malformado"),
            ("/job/x%GG", "malformado"),
            ("/job/%252E%252E/admin", "travesía"),
            ("/job/x%255Cy", "barra invertida"),
            ("/job/x%2Fy", "separador de ruta"),
            ("/job/x%252Fy", "separador de ruta"),
            # json.loads acepta '"\\ud800"': sin el guard, el sustituto suelto
            # combinado con '%' crasheaba el encode de la inspección (clase G1).
            ("/job/x\ud800y", "sustitutos UTF-16"),
            ("/job/x%20\ud800y", "sustitutos UTF-16"),
        ],
        ids=[
            "query_sin_ruta",
            "fragmento_sin_ruta",
            "ruta_fuera_de_job",
            "relativa_sin_barra",
            "esquema_https",
            "esquema_mailto",
            "autoridad",
            "query_tras_job",
            "fragmento_tras_job",
            "backslash",
            "backslash_escapado",
            "backslash_escapado_minuscula",
            "tabulador",
            "nul",
            "espacio_interno",
            "travesia_directorio",
            "travesia_final",
            "segmento_punto",
            "travesia_intermedia",
            "travesia_codificada",
            "travesia_codificada_mayuscula",
            "indice_sin_oferta",
            "segmento_vacio_intermedio",
            "barra_final",
            "parametro_de_path",
            "nul_codificado",
            "salto_de_linea_codificado",
            "del_codificado",
            "porcentaje_suelto",
            "triplete_incompleto",
            "triplete_no_hexadecimal",
            "travesia_doble_codificada",
            "backslash_doble_codificado",
            "barra_codificada",
            "barra_doble_codificada",
            "sustituto_utf16",
            "sustituto_utf16_con_encoding",
        ],
    )
    @pytest.mark.asyncio
    async def test_forma_invalida_degrada_item_con_issue_y_es_error(
        self, path, reason_fragment
    ):
        """Cada forma inválida pasa el guard de TIPO (str no vacío) pero debe
        degradarse con issue descriptivo — nunca persistirse como URL."""
        scraper = SwissSchoolsISPScraper()
        postings = [dict(self.MATCHING_VALID, externalPath=path)]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        issues = diag.issues()
        assert len(issues) == 1
        assert issues[0].kind == diag.KIND_NETWORK
        assert "externalPath sin forma de ruta de oferta" in issues[0].detail
        assert reason_fragment in issues[0].detail
        assert diag.classify(len(jobs), issues) == "error"

    @pytest.mark.parametrize(
        "path",
        [
            # Las DOS rutas reales de ISP verificadas en vivo (200 OK).
            "/job/Mosaic-School--Ecole-Mosaic-Switzerland-Geneva/Teacher-Assistant_JR210499",
            "/job/Mosaic-School--Ecole-Mosaic-Switzerland-Geneva/Primary-Teacher---Maternity-cover_JR209013",
            # Percent-encoding LEGÍTIMO (decisión mantenida por el revisor):
            # rechazar sintaxis rota y controles, nunca el encoding en sí.
            "/job/x%20y",  # espacio codificado
            "/job/Caf%C3%A9-Teacher_JR1",  # UTF-8 multi-byte
            "/job/x%25y",  # '%' literal codificado
            "/job/x%2Ey",  # '.' dentro de segmento: dato, no travesía
            "/job/x%3By",  # ';' codificado es dato literal, no delimitador
        ],
        ids=[
            "ruta_real_teacher_assistant",
            "ruta_real_primary_teacher",
            "espacio_codificado",
            "utf8_codificado",
            "porcentaje_literal",
            "punto_codificado_en_segmento",
            "punto_y_coma_codificado",
        ],
    )
    @pytest.mark.asyncio
    async def test_forma_valida_emite_oferta_con_url_original(self, path):
        """Anti-falso-positivo (tan grave como el falso negativo — kill-switch):
        las rutas reales y el encoding legítimo emiten oferta SIN issue, y la
        URL se construye con la ruta ORIGINAL — la decodificación de la
        inspección jamás se persiste."""
        scraper = SwissSchoolsISPScraper()
        postings = [dict(self.MATCHING_VALID, externalPath=path)]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert diag.issues() == []
        assert len(jobs) == 1
        assert jobs[0]["url"].endswith(path)
        assert diag.classify(len(jobs), diag.issues()) == "ok"

    @pytest.mark.asyncio
    async def test_forma_valida_con_espacios_de_borde_se_canoniza(self):
        """'Aplica strip': los espacios de borde no invalidan la ruta, pero la
        URL construida no debe arrastrarlos (ni el source_id de fallback)."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            dict(
                self.MATCHING_VALID,
                externalPath="  /job/Primary-Teacher_JR123  ",
                bulletFields=7,  # escalar → source_id cae al externalPath
            )
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert diag.issues() == []
        assert len(jobs) == 1
        assert jobs[0]["url"].endswith("/job/Primary-Teacher_JR123")
        assert jobs[0]["source_id"] == "/job/Primary-Teacher_JR123"

    @pytest.mark.asyncio
    async def test_item_invalido_no_arrastra_al_valido_de_la_pagina(self):
        """La forma inválida degrada SOLO su item: el resto de la página se
        emite con normalidad."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            dict(self.MATCHING_VALID, externalPath="?job=123"),
            dict(self.MATCHING_VALID, title="Teacher Assistant"),
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert len(jobs) == 1
        assert jobs[0]["title"] == "Teacher Assistant"
        assert len(diag.issues()) == 1

    @pytest.mark.asyncio
    async def test_g2_forma_invalida_de_otro_colegio_no_se_diagnostica(self):
        """G2 (NO puede romperse): el guard de forma vive DESPUÉS del filtro
        de colegio — un externalPath basura en un posting de OTRO colegio del
        tenant compartido no genera issue, y 0 matches sigue siendo vacío
        legítimo (`empty`, nunca `error`)."""
        scraper = SwissSchoolsISPScraper()
        postings = [
            {
                "title": "Maths Teacher",
                "locationsText": "Other ISP School, Madrid",
                "externalPath": "?job=999",
                "bulletFields": ["JR9"],
            }
        ]
        diag.begin()

        with patch.object(
            scraper._circuit,
            "call",
            new_callable=AsyncMock,
            return_value=_workday_response(postings),
        ):
            jobs = await scraper.fetch_jobs("")

        assert jobs == []
        assert diag.issues() == []
        assert diag.classify(len(jobs), diag.issues()) == "empty"
