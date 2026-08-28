"""Sonda /v1/ready (rev. externa #6): estados y NO-fuga de internals."""

from pathlib import Path
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
    """El texto de la excepción (host/usuario/SQL) NO debe llegar al cliente, pero la
    IDENTIDAD de la release sí: G10 P3-2 — de los dos 503 de esta sonda, el de head
    desalineado llevaba `release` + `authoritative` y este no llevaba ninguno de los dos,
    justo cuando el operador está diagnosticando qué proceso tiene delante."""
    boom = RuntimeError('connection to server at "postgres" failed for user "jobhunt_core"')
    monkeypatch.setattr(api, "engine", _engine_yielding(boom))
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    r = TestClient(api.app).get("/v1/ready")
    assert r.status_code == 503
    body = r.json()
    assert body == {
        "status": "not_ready",
        "reason": "database_unavailable",
        "release": "abc1234",
        "authoritative": api._authoritative(),
    }
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
    # El árbol de la suite SÍ va montado (perfil de desarrollo): este test fija las
    # OTRAS condiciones de la marca, y la del montaje tiene test propio (G11 P2-2).
    monkeypatch.setattr(api, "_code_is_mounted", lambda: False)
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
    # El árbol de la suite SÍ va montado (perfil de desarrollo): este test fija las
    # OTRAS condiciones de la marca, y la del montaje tiene test propio (G11 P2-2).
    monkeypatch.setattr(api, "_code_is_mounted", lambda: False)
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
    # El árbol de la suite SÍ va montado (perfil de desarrollo): este test fija las
    # OTRAS condiciones de la marca, y la del montaje tiene test propio (G11 P2-2).
    monkeypatch.setattr(api, "_code_is_mounted", lambda: False)
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
    """`/opt/jobhunt-release/RELEASE` lo escribe el Dockerfile con el MISMO build arg que
    el ENV, fuera de `/app` entero (que un bind mount sustituye): es de la imagen."""
    fichero = tmp_path / "RELEASE"
    fichero.write_text("450c561\n")
    monkeypatch.setattr(api, "_BAKED_RELEASE_PATH", fichero)
    assert api._read_baked_release() == "450c561"
    monkeypatch.setattr(api, "_BAKED_RELEASE_PATH", tmp_path / "no-existe")
    assert api._read_baked_release() is None


def test_el_codigo_montado_quita_la_autoritatividad_aunque_nadie_ponga_la_variable(
    tmp_path, monkeypatch
):
    """REGRESIÓN auditoría G11 P2-2: la primera condición de la marca era una SUPOSICIÓN.

    `CORE_CODE_MUTABLE` no observa nada: dice lo que alguien escribió en el compose. El
    docstring de `_authoritative` y `docs/DEPLOY_NAS.md` lo elevaban a hecho («es false si
    el código va montado»), y no lo era. Reproducido contra la imagen viva: sustituyendo
    `/app/jobhunt_core` por un bind mount SIN poner la variable, la sonda publicaba la
    release verdadera `1d686a4` con `authoritative: true` mientras respondía código del
    auditor. Es una vía MÁS fuerte que la que cerró G10: no falsifica el nombre de la
    release, falsifica el código que responde bajo un nombre verdadero. El montaje SÍ es
    observable desde dentro del proceso — `/proc/self/mountinfo` lo lista.
    """
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    monkeypatch.setattr(api, "CODE_MUTABLE", False)      # nadie puso la variable
    monkeypatch.setattr(api, "_BAKED_RELEASE", "abc1234")
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    raiz = str(Path(jobhunt_core.__file__).parent)

    limpio = tmp_path / "sin-montaje"
    limpio.write_text("31 25 0:29 / /proc/sys rw,relatime shared:16 - proc proc rw\n")
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", limpio)
    assert api._code_is_mounted() is False
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is True

    montado = tmp_path / "con-montaje"
    montado.write_text(
        "31 25 0:29 / /proc/sys rw,relatime shared:16 - proc proc rw\n"
        f"812 745 0:64 /home/x/SwissJob/jobhunt_core {raiz} rw,relatime - ext4 /dev/sda1 rw\n"
    )
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", montado)
    assert api._code_is_mounted() is True
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False
    assert TestClient(api.app).get("/v1/health").json()["authoritative"] is False

    # Un fichero SUELTO montado dentro del paquete sustituye el código que responde
    # igual de bien: comprobar solo la raíz lo daba por limpio (verificado en vivo).
    dentro = tmp_path / "un-fichero"
    dentro.write_text(
        f"812 745 0:64 /home/x/main.py {raiz}/api/main.py rw,relatime - ext4 /dev/sda1 rw\n"
    )
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", dentro)
    assert api._code_is_mounted() is True
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False

    # …y un montaje que solo COMPARTE PREFIJO con la raíz no es el código.
    vecino = tmp_path / "vecino"
    vecino.write_text(
        f"812 745 0:64 /home/x/otro {raiz}_backup rw,relatime - ext4 /dev/sda1 rw\n"
    )
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", vecino)
    assert api._code_is_mounted() is False

    # Sin `/proc` legible no se puede DESCARTAR el montaje: dirección segura.
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", tmp_path / "no-existe")
    assert api._code_is_mounted() is True
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False


def test_un_montaje_ANCESTRO_del_paquete_tambien_quita_la_autoritatividad(
    tmp_path, monkeypatch
):
    """REGRESIÓN auditoría externa R2 P1-1: el montaje que MÁS sustituye era el único
    que no se miraba.

    `_code_is_mounted` solo consideraba peligroso el punto de montaje IGUAL a la raíz
    del paquete o situado DEBAJO. Un bind mount en `/app` es ANCESTRO de
    `/app/jobhunt_core`: sustituye el paquete entero —y de paso el marcador horneado—,
    y la función lo daba por limpio. Con el marcador del árbol montado igual al
    `RELEASE_SHA` del entorno, la sonda publicaba `authoritative: true` sobre código
    ajeno. Reproducido por el auditor con un `mountinfo` sintético cuyo 5º campo es
    `/app`: `mount_at_parent_app_detected = False`, `authoritative = True`.

    El rootfs `/` es ancestro de TODO y NO debe marcar mutable ninguna imagen: es el
    montaje normal de cualquier contenedor.
    """
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    monkeypatch.setattr(api, "_BAKED_RELEASE", "abc1234")
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    raiz = str(Path(jobhunt_core.__file__).parent)
    padre = str(Path(raiz).parent)          # `/app` en la imagen

    ancestro = tmp_path / "montaje-en-el-padre"
    ancestro.write_text(
        "31 25 0:29 / / rw,relatime shared:1 - overlay overlay rw\n"
        f"812 745 0:64 /home/x/SwissJob {padre} rw,relatime - ext4 /dev/sda1 rw\n"
    )
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", ancestro)
    assert api._code_is_mounted() is True
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False
    assert TestClient(api.app).get("/v1/health").json()["authoritative"] is False

    # …y el rootfs de cualquier contenedor NO puede marcar mutable a toda la imagen:
    # `/` es ancestro de todo, así que la regla de ancestros lo excluye a propósito.
    solo_rootfs = tmp_path / "solo-rootfs"
    solo_rootfs.write_text(
        "31 25 0:29 / / rw,relatime shared:1 - overlay overlay rw\n"
        "32 31 0:30 / /proc rw,nosuid,nodev,noexec,relatime - proc proc rw\n"
        "33 31 0:31 / /etc/hosts rw,relatime - ext4 /dev/sda1 rw\n"
    )
    monkeypatch.setattr(api, "_MOUNTINFO_PATH", solo_rootfs)
    assert api._code_is_mounted() is False
    assert api._release_marker_is_mounted() is False
    assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is True


def test_el_marcador_de_release_montado_quita_la_autoritatividad(tmp_path, monkeypatch):
    """REGRESIÓN auditoría externa R2 P1-1 (segunda mitad): el marcador vive FUERA del
    árbol de código, y ese sitio nuevo también hay que observarlo.

    Mover el marcador a `/opt/jobhunt-release/RELEASE` lo saca del alcance de un
    montaje sobre el código, pero montarlo A ÉL falsificaría el ancla contra la que se
    contrasta `RELEASE_SHA` (el mismo agujero que cerró G10, en la ruta nueva). El
    valor se lee al IMPORTAR, así que un montaje presente al arrancar ya lo habría
    envenenado: la marca solo puede afirmar si tampoco hay montaje aquí.
    """
    monkeypatch.setattr(api, "engine", _engine_yielding(api._expected_head()))
    monkeypatch.setattr(api, "CODE_MUTABLE", False)
    monkeypatch.setattr(api, "_BAKED_RELEASE", "abc1234")
    monkeypatch.setattr(api, "__release_sha__", "abc1234")
    marcador = str(api._BAKED_RELEASE_PATH)

    for punto in (marcador, str(Path(marcador).parent)):
        montado = tmp_path / "marcador-montado"
        montado.write_text(
            "31 25 0:29 / / rw,relatime shared:1 - overlay overlay rw\n"
            f"812 745 0:64 /home/x/falso {punto} rw,relatime - ext4 /dev/sda1 rw\n"
        )
        monkeypatch.setattr(api, "_MOUNTINFO_PATH", montado)
        assert api._release_marker_is_mounted() is True, punto
        assert TestClient(api.app).get("/v1/ready").json()["authoritative"] is False


def test_el_marcador_horneado_vive_fuera_del_arbol_de_codigo():
    """El marcador NO puede vivir bajo el árbol que el perfil de desarrollo monta ni
    bajo `/app` (auditoría externa R2 P1-1): si estuviera dentro, sustituir el árbol
    sustituiría también el ancla contra la que se compara el `RELEASE_SHA` del entorno.
    """
    marcador = str(api._BAKED_RELEASE_PATH)
    arbol = str(Path(jobhunt_core.__file__).parent)
    assert not marcador.startswith(arbol + "/")
    assert not marcador.startswith("/app/")
