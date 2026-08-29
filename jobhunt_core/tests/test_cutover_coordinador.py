"""El coordinador del cutover: identidad canónica, raíz no configurable,
durabilidad y transiciones de fase.

Estas pruebas son de UNIDAD a propósito. El extremo a extremo (la suite de
`test_deploy_cutover_failclosed.py`) demuestra la propiedad que importa —que
ninguna variable del entorno mueve la ruta del cerrojo— pero no puede
demostrar el fallo cerrado cuando la raíz no es accesible: no hay ninguna
opción para apuntarla a otro sitio, que es precisamente el invariante. Esa rama
se prueba aquí, importando el módulo y pasándole la raíz como argumento.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_RUTA = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "cutover_coordinador.py"


def _modulo():
    assert _RUTA.is_file(), (
        f"{_RUTA} no está montado: la suite necesita el perfil de dev "
        "(docker-compose.yml + docker-compose.dev.yml)."
    )
    spec = importlib.util.spec_from_file_location("cutover_coordinador", _RUTA)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


coord = _modulo()


# --- identidad canónica ----------------------------------------------------
def test_la_clave_sale_solo_de_la_identidad_y_la_base(tmp_path):
    """Dos formas de nombrar el mismo servidor dan la MISMA ruta, y dos servidores
    distintos dan rutas distintas. No hay nada más en la clave."""
    a = coord.ruta_del_cerrojo("7610559582315749414", "swissjobhunter", tmp_path)
    b = coord.ruta_del_cerrojo(" 7610559582315749414 ", "swissjobhunter", tmp_path)
    assert a == b
    otro = coord.ruta_del_cerrojo("9999999999999999999", "swissjobhunter", tmp_path)
    assert otro != a
    otra_base = coord.ruta_del_cerrojo("7610559582315749414", "otra", tmp_path)
    assert otra_base != a


@pytest.mark.parametrize("identidad", ["", "   ", "swissjob-postgres", "postgres:5432", "12a"])
def test_una_identidad_que_no_es_canonica_para_la_maniobra(identidad, tmp_path):
    """El nombre del contenedor NO es identidad: aceptarlo como tal es exactamente
    cómo dos alias del mismo servidor tomaban dos cerrojos distintos."""
    with pytest.raises(SystemExit):
        coord.ruta_del_cerrojo(identidad, "swissjobhunter", tmp_path)


def test_sin_base_no_hay_cerrojo(tmp_path):
    with pytest.raises(SystemExit):
        coord.ruta_del_cerrojo("7610559582315749414", "  ", tmp_path)


# --- raíz no configurable --------------------------------------------------
def test_la_raiz_es_una_constante_del_modulo_y_no_se_lee_del_entorno(monkeypatch):
    """La vía de la TERCERA reapertura: mientras existiera un knob que moviera la
    raíz, dos operadores podían no verse. No debe haber ninguno."""
    import inspect

    # Se miran LAS DOS funciones que resuelven la ruta, no el fichero entero: el
    # coordinador sí lee el entorno para otras cosas legítimas —construir el del
    # proceso hijo, por ejemplo— y prohibirlo en todas partes daría un rojo falso
    # cada vez que se toque algo no relacionado. Lo que no puede haber es un knob
    # EN LA RESOLUCIÓN, que es donde estuvo la tercera reapertura.
    for fn in (coord.ruta_del_cerrojo, coord.preparar_raiz):
        cuerpo = inspect.getsource(fn)
        for sospechoso in ("LOCK_DIR", "getenv", "environ", "sys.argv"):
            assert sospechoso not in cuerpo, (
                f"{fn.__name__} lee '{sospechoso}' para resolver la raíz: eso es un knob"
            )
    # Y la constante es literal: nada de componerla desde fuera.
    fuente = _RUTA.read_text(encoding="utf-8")
    assert 'RAIZ_CERROJOS = Path("/var/lock/jobhunt-cutover")' in fuente, (
        "la raíz dejó de ser una constante literal del módulo"
    )
    # Y tampoco por línea de comandos.
    ayuda = subprocess.run(
        [sys.executable, str(_RUTA), "ejecutar", "--help"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "--raiz" not in ayuda and "--lock" not in ayuda, ayuda


def test_una_raiz_inaccesible_para_en_vez_de_elegir_otra(tmp_path):
    """Fallo cerrado: un operador sin acceso PARA. Elegir otro directorio es
    literalmente cómo se pierde la exclusión mutua entre dos operadores."""
    if os.geteuid() == 0:
        pytest.skip("root escribe en cualquier sitio: la rama no es observable")
    prohibida = tmp_path / "sin-permiso"
    prohibida.mkdir()
    prohibida.chmod(0o500)
    try:
        with pytest.raises(SystemExit):
            coord.preparar_raiz(prohibida / "cerrojos")
    finally:
        prohibida.chmod(0o700)


def test_una_raiz_accesible_se_prepara_sola(tmp_path):
    destino = tmp_path / "cerrojos"
    assert coord.preparar_raiz(destino) == destino
    assert destino.is_dir()


# --- transiciones de fase --------------------------------------------------
def _publicar(ruta: Path, fase: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_RUTA), "publicar", "--ruta", str(ruta), "--fase", fase],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize(
    ("camino", "porque"),
    [
        (["INICIO", "APARTADA", "DESTINO_CREADO", "RESTAURADO", "VERIFIED"],
         "el camino feliz"),
        (["INICIO", "APARTADA", "DESTINO_CREADO", "RESTAURADO",
          "VERIFICACION_FALLIDA", "DESTINO_CREADO", "RESTAURADO", "VERIFIED"],
         "la verificación falla y se restaura DE NUEVO hasta certificar"),
        (["INICIO", "INICIO"], "reanudar desde INICIO vuelve a medir"),
        (["INICIO", "APARTADA", "DESTINO_CREADO", "DESTINO_CREADO"],
         "un destino sin certificar se recrea las veces que haga falta"),
    ],
)
def test_los_caminos_validos_se_publican(tmp_path, camino, porque):
    ruta = tmp_path / "checkpoint"
    for fase in camino:
        p = _publicar(ruta, fase)
        assert p.returncode == 0, f"{porque}: {fase} rechazada\n{p.stdout}{p.stderr}"
    assert f"CK_FASE='{camino[-1]}'" in ruta.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("desde", "hasta"),
    [
        (None, "APARTADA"),          # sin checkpoint no se puede estar apartada
        (None, "VERIFIED"),          # ni certificada
        ("INICIO", "RESTAURADO"),    # no se restaura sin haber creado el destino
        ("APARTADA", "VERIFIED"),    # ni se certifica sin restaurar
        # LA transición que R7 tuvo que añadir, al revés: no se vuelve a
        # verificar el destino que ya se comprobó que no cuadra.
        ("VERIFICACION_FALLIDA", "VERIFIED"),
        ("VERIFICACION_FALLIDA", "RESTAURADO"),
    ],
)
def test_una_transicion_que_no_ocurrio_no_se_publica(tmp_path, desde, hasta):
    """Un checkpoint que salta una fase describe un estado que no pasó, y de ahí no
    se reanuda nada: es peor que no tener checkpoint, porque miente."""
    ruta = tmp_path / "checkpoint"
    if desde is not None:
        for fase in {"INICIO": ["INICIO"],
                     "APARTADA": ["INICIO", "APARTADA"],
                     "VERIFICACION_FALLIDA": ["INICIO", "APARTADA", "DESTINO_CREADO",
                                              "RESTAURADO", "VERIFICACION_FALLIDA"]}[desde]:
            assert _publicar(ruta, fase).returncode == 0
    p = _publicar(ruta, hasta)
    assert p.returncode != 0, f"publicó {desde} → {hasta}:\n{p.stdout}"
    assert "transición no permitida" in p.stdout + p.stderr


def test_una_fase_inventada_no_se_publica(tmp_path):
    p = _publicar(tmp_path / "checkpoint", "CASI_VERIFIED")
    assert p.returncode != 0
    assert "no es una fase" in p.stdout + p.stderr


# --- durabilidad -----------------------------------------------------------
def test_la_publicacion_no_deja_el_checkpoint_a_medias(tmp_path):
    """`os.replace` es `rename(2)`: o está el contenido viejo o el nuevo, nunca
    medio fichero. Y el temporal no sobrevive."""
    ruta = tmp_path / "checkpoint"
    assert _publicar(ruta, "INICIO").returncode == 0
    assert not (tmp_path / "checkpoint.tmp").exists()
    p = subprocess.run(
        [sys.executable, str(_RUTA), "publicar", "--ruta", str(ruta),
         "--fase", "APARTADA", "--dato", "CK_PREVIA=swissjobhunter_previa_20260829"],
        capture_output=True, text=True,
    )
    assert p.returncode == 0, p.stdout + p.stderr
    cuerpo = ruta.read_text(encoding="utf-8")
    assert "CK_PREVIA='swissjobhunter_previa_20260829'" in cuerpo
    assert cuerpo.endswith("CK_FASE='APARTADA'\n")


def test_un_sync_que_falla_impide_publicar(tmp_path):
    """La frontera entre «escrito» y «sobrevive al corte». Si `sync` no termina 0, la
    fase NO se publica: detrás va una acción destructiva."""
    falso = tmp_path / "bin"
    falso.mkdir()
    (falso / "sync").write_text("#!/bin/sh\nexit 1\n")
    (falso / "sync").chmod(0o755)
    entorno = dict(os.environ, PATH=f"{falso}:{os.environ['PATH']}")
    ruta = tmp_path / "checkpoint"
    p = subprocess.run(
        [sys.executable, str(_RUTA), "publicar", "--ruta", str(ruta), "--fase", "INICIO"],
        capture_output=True, text=True, env=entorno,
    )
    assert p.returncode != 0, p.stdout
    assert "sync" in p.stdout + p.stderr


# --- exclusión mutua -------------------------------------------------------
def test_dos_ordenes_sobre_el_mismo_recurso_no_coexisten(tmp_path):
    """La propiedad, en el propio componente: mientras una orden corre con el
    cerrojo puesto, otra sobre el MISMO recurso es rechazada."""
    testigo, fin = tmp_path / "dentro", tmp_path / "sigue"
    fin.write_text("1")
    guion = tmp_path / "espera.sh"
    guion.write_text(f"#!/bin/sh\ntouch {testigo}\nwhile [ -f {fin} ]; do sleep 0.1; done\n")
    guion.chmod(0o755)
    argv = [sys.executable, str(_RUTA), "ejecutar",
            "--identidad", "7610559582315749414", "--db", "base_de_prueba_coordinador"]
    import threading

    resultado: dict[str, subprocess.CompletedProcess] = {}

    def primera():
        resultado["a"] = subprocess.run(argv + ["--", str(guion)],
                                        capture_output=True, text=True)

    hilo = threading.Thread(target=primera)
    hilo.start()
    try:
        for _ in range(100):
            if testigo.exists():
                break
            import time

            time.sleep(0.1)
        assert testigo.exists(), "la primera orden no llegó a ejecutarse"
        b = subprocess.run(argv + ["--", "/bin/true"], capture_output=True, text=True)
        assert b.returncode != 0, "dos órdenes sobre el mismo recurso coexistieron"
        assert "otra maniobra tiene el cerrojo" in b.stdout + b.stderr
    finally:
        fin.unlink(missing_ok=True)
        hilo.join(timeout=60)
    assert resultado["a"].returncode == 0, resultado["a"].stdout + resultado["a"].stderr
