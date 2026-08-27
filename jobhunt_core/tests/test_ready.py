"""Sonda /v1/ready (rev. externa #6): estados y NO-fuga de internals."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import jobhunt_core
import jobhunt_core.api.main as api


def _engine_yielding(version_or_exc):
    """Engine falso cuyo connect() entra en un conn que devuelve la versión o revienta."""
    conn = AsyncMock()
    if isinstance(version_or_exc, Exception):
        conn.execute.side_effect = version_or_exc
    else:
        conn.execute.return_value = SimpleNamespace(scalar=lambda: version_or_exc)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = cm
    return engine


def test_ready_ok(monkeypatch):
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_ready_wrong_head_is_503(monkeypatch):
    monkeypatch.setattr(api, "engine", _engine_yielding("deadbeef0000"))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 503
    assert r.json()["expected"] == api._expected_head()


def test_ready_db_down_is_generic_503(monkeypatch):
    # El texto de la excepción (host/usuario/SQL) NO debe llegar al cliente.
    boom = RuntimeError('connection to server at "postgres" failed for user "jobhunt_core"')
    monkeypatch.setattr(api, "engine", _engine_yielding(boom))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 503
    body = r.json()
    assert body == {"status": "not_ready", "reason": "database_unavailable"}
    assert "postgres" not in r.text and "jobhunt_core" not in r.text


def test_expected_head_se_fija_al_arrancar(monkeypatch):
    """REGRESIÓN auditoría externa 2026-08-27 P1-3.

    CORRIGE `test_expected_head_no_se_congela_cuando_la_cadena_crece`, que fijaba el
    comportamiento opuesto: releer el directorio de revisiones en caliente. Aquella prueba
    nació para cerrar un falso ROJO (dos días de 503 con la BD sana, commit bf3fbfd) pero
    abrió el falso VERDE simétrico: los ficheros del volumen podían pasar a la release B
    mientras los handlers en memoria seguían siendo los de la A, y `/v1/ready` certificaba
    la B. El head esperado tiene que venir de la MISMA imagen que sirve las peticiones, y
    por eso se lee UNA vez al importar.

    El falso rojo ya no puede volver: sin bind mount de código (docker-compose.dev.yml es
    el único que lo monta), la única forma de cambiar la cadena de migraciones es cambiar
    la imagen, y eso recrea el proceso.
    """
    congelado = api._EXPECTED_HEAD
    # El sistema de ficheros cambia bajo el proceso: la respuesta NO puede moverse.
    monkeypatch.setattr(api, "_read_expected_head", lambda: "core9999")
    assert api._expected_head() == congelado != "core9999"


def test_el_head_congelado_es_el_de_la_imagen():
    """El valor congelado no es un artefacto: coincide con la cadena real del paquete
    (una congelación equivocada sería un 503 permanente, el falso rojo por otra vía)."""
    assert api._EXPECTED_HEAD == api._read_expected_head()


def test_ready_publica_la_release_del_proceso(monkeypatch):
    """P1-3: sin SHA en la sonda, un operador no puede distinguir releases — es lo que
    dejó pasar dos incidentes (capturador cinco días con código viejo, API dos días en
    503). `/v1/ready` publica la release del proceso que responde."""
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 200
    assert r.json()["release"] == jobhunt_core.__release_sha__


def test_ready_declara_si_autoriza_operaciones(monkeypatch):
    """P1-3 (perfil de desarrollo): con el código montado como volumen el readiness NO
    autoriza operaciones — el proceso puede estar sirviendo código distinto del de la
    imagen. La sonda lo dice en vez de dejarlo a la memoria del operador."""
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    # Release NOMBRABLE: la otra condición de la autoritatividad se prueba aparte
    # (test_no_hay_autoritatividad_sin_SHA_de_release, G9 P2-B).
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    monkeypatch.setattr(api, "_BAKED_RELEASE", "abc1234")  # la que hornea la imagen (G10 P2-2)
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is True
    monkeypatch.setattr(api, "CODE_MUTABLE", True)
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False


def test_ready_503_por_head_tambien_lleva_la_marca(monkeypatch):
    """G9 P2-A: la rama 503 por head desalineado publicaba `release` SIN la marca de
    autoritatividad — el estado en el que más importa saber si el SHA es de fiar."""
    monkeypatch.setattr(api, "engine", _engine_yielding("deadbeef0000"))
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    monkeypatch.setattr(api, "CODE_MUTABLE", True)
    body = TestClient(api.app).get("/v1/ready").json()
    assert body["release"] == "abc1234" and body["authoritative"] is False


def test_no_hay_autoritatividad_sin_SHA_de_release(monkeypatch):
    """REGRESIÓN auditoría G9 P2-B(b): en el NAS los composes de producción no pasan el
    build arg, así que la imagen hornea `RELEASE_SHA=unknown` y allí `CORE_CODE_MUTABLE`
    no está puesto — las sondas se declaraban `authoritative: true` sobre una release que
    no saben nombrar, y el paso «todos publican el mismo SHA» se cumplía trivialmente
    (`unknown == unknown == unknown`). Autoritativo exige las DOS cosas: código inmutable
    y release NOMBRABLE."""
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    monkeypatch.setattr(api, "_BAKED_RELEASE", jobhunt_core.UNKNOWN_RELEASE)
    monkeypatch.setattr(api, "__release_sha__", jobhunt_core.UNKNOWN_RELEASE)
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False
    assert TestClient(api.app).get("/v1/health").json()["authoritative"] is False
    monkeypatch.setattr(api, "_BAKED_RELEASE", "abc1234")
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is True


def test_un_RELEASE_SHA_del_entorno_no_puede_hacer_autoritativa_la_sonda(monkeypatch):
    """REGRESIÓN auditoría G10 P2-2: a la marca se la hacía mentir con una variable.

    `RELEASE_SHA` es un ENV horneado por el Dockerfile, pero el ENV de una imagen lo pisa
    cualquier `environment:`/`env_file:` del contenedor — y los tres servicios del core
    arrancan con `env_file: .env.core.prod`. Reproducido contra la imagen viva:
    `docker run -e RELEASE_SHA=deadbee swissjob-core:dev` publicaba `deadbee` con
    `authoritative: true` mientras el código era el de `450c561`. La marca certificaba dos
    cosas comprobables (código no montado, SHA no-`unknown`) y ninguna que atara el SHA al
    código que responde, justo donde `docs/DEPLOY_NAS.md` la convierte en la autorización
    para operar (flip, maniobras de datos).
    """
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    monkeypatch.setattr(api, "_BAKED_RELEASE", "450c561")   # lo que la IMAGEN hornea
    monkeypatch.setattr(api, "__release_sha__", "deadbee")  # lo que el entorno inyecta
    body = TestClient(api.app).get("/v1/ready").json()
    assert body["release"] == "deadbee"          # se sigue publicando lo que corre
    assert body["authoritative"] is False        # pero ya no autoriza nada
    assert TestClient(api.app).get("/v1/health").json()["authoritative"] is False
    # Y coincidiendo con la copia horneada, la marca vuelve a valer.
    monkeypatch.setattr(api, "__release_sha__", "450c561")
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is True


def test_sin_release_horneada_en_la_imagen_no_hay_autoritatividad(monkeypatch):
    """La otra dirección de G10 P2-2: sin ancla con la que contrastar el ENV, la sonda no
    afirma nada — el único dato que queda es precisamente el que se puede inyectar."""
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    monkeypatch.setattr(api, "_BAKED_RELEASE", None)
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False


def test_la_release_horneada_sale_del_fichero_de_la_imagen(tmp_path, monkeypatch):
    """`/app/RELEASE` lo escribe el Dockerfile con el MISMO build arg que el ENV, fuera de
    `/app/jobhunt_core` (que el perfil de desarrollo monta): es de la imagen, no del árbol."""
    fichero = tmp_path / "RELEASE"
    fichero.write_text("450c561\n")
    monkeypatch.setattr(api, "_BAKED_RELEASE_PATH", fichero)
    assert api._read_baked_release() == "450c561"
    monkeypatch.setattr(api, "_BAKED_RELEASE_PATH", tmp_path / "no-existe")
    assert api._read_baked_release() is None
