"""Guarda EJECUTABLE del orden de la maniobra de despliegue del NAS.

REGRESIÓN auditoría externa R2 P1-3. El defecto no vivía en el código sino en el ORDEN
del único procedimiento ejecutable documentado: `docs/DEPLOY_NAS.md` mandaba **Recreate**
en §5.3 y solo DESPUÉS, en §5.4, avisaba de que la canonización de identidad tiene que
hacerse con los workers parados. `Recreate` autoarranca `worker` y `core-worker`
(`restart: unless-stopped` en `docker-compose.qnap.yml`), y el propio documento afirma
que una cosecha del código nuevo sobre identidades viejas causa a la vez pérdida
silenciosa de ofertas y duplicación del corpus. El auditor lo reprodujo con un parser:

    {'recreate_before_canonization': True, 'recreate_line': 596,
     'canonization_line': 606, 'worker_auto_restart': True, 'core_worker_present': True}

Un documento no se puede «testear» como el código, pero su ORDEN sí: es lo único que un
operador ejecuta. Esto fija ese orden para que no se pueda invertir en silencio otra vez.

Los ficheros se leen del árbol del repo montado en `/app` (perfil de dev, ver
`docker-compose.yml`: `docs/` y `docker-compose.qnap.yml`, ambos en solo lectura). Si falta, la guarda FALLA en vez de saltarse: una comprobación que
no corre es exactamente como el defecto sobrevivió a varias revisiones.
"""

import re
from pathlib import Path

import pytest
import yaml

_REPO = Path("/app")
_DEPLOY_NAS = _REPO / "docs" / "DEPLOY_NAS.md"
_QNAP = _REPO / "docker-compose.qnap.yml"

# Los siete pasos de la secuencia única, en el orden que exige la maniobra: parar los
# escritores → copia de seguridad → imagen cargada sin arrancar nada → las dos copias SQL
# → la OTRA mitad (`canonical_refs`) → verificación → y solo entonces arrancar.
_PASOS_ESPERADOS = (
    ("parar", ("parar", "detener")),
    ("backup", ("backup", "copia", "pg_dump")),
    ("cargar", ("cargar", "imagen")),
    ("sql", ("sql", "copias")),
    ("canonical_refs", ("canonical_refs", "etiquetas")),
    ("verificar", ("verificar", "comprobar")),
    ("arrancar", ("recrear", "arrancar", "smoke")),
)


def _seccion_5() -> list[str]:
    """Las líneas de `## 5. …` hasta la siguiente sección de primer nivel."""
    assert _DEPLOY_NAS.is_file(), (
        f"{_DEPLOY_NAS} no está montado: esta guarda NO puede saltarse. Ejecuta la suite "
        "con el perfil de dev (docker-compose.yml + docker-compose.dev.yml)."
    )
    lineas = _DEPLOY_NAS.read_text(encoding="utf-8").splitlines()
    inicio = next(i for i, l in enumerate(lineas) if l.startswith("## 5."))
    fin = next(
        (i for i, l in enumerate(lineas[inicio + 1 :], inicio + 1) if l.startswith("## ")),
        len(lineas),
    )
    return lineas[inicio:fin]


def _primera(lineas: list[str], patron: str) -> int | None:
    for i, linea in enumerate(lineas):
        if re.search(patron, linea):
            return i
    return None


def test_la_seccion_5_tiene_UNA_secuencia_de_siete_pasos_en_orden():
    """Siete pasos numerados, no dos notas ordenadas al revés."""
    lineas = _seccion_5()
    pasos = [l for l in lineas if l.startswith("#### Paso ")]
    assert len(pasos) == len(_PASOS_ESPERADOS), pasos
    for i, (titulo, (_, claves)) in enumerate(zip(pasos, _PASOS_ESPERADOS), start=1):
        assert titulo.startswith(f"#### Paso {i} "), titulo
        minus = titulo.lower()
        assert any(c in minus for c in claves), (titulo, claves)


def test_ningun_Recreate_antes_de_la_canonizacion():
    """LA reproducción del auditor, invertida.

    `**Recreate**` en negrita es la ACCIÓN de la UI de Container Station —la que
    autoarranca los workers—, y en §5 no puede aparecer antes de `canonical_refs`, que es
    la segunda mitad de la canonización. En la prosa de aviso se escribe «recrear» en
    minúscula justamente para que la negrita marque el único punto donde se pulsa.
    """
    lineas = _seccion_5()
    recreate = _primera(lineas, r"\*\*Recreate\*\*")
    canonizacion = _primera(lineas, r"canonical_refs")
    assert canonizacion is not None, "§5 ya no menciona la segunda mitad de la maniobra"
    assert recreate is not None, "§5 debe decir dónde se pulsa Recreate"
    assert canonizacion < recreate, (
        f"§5 manda Recreate (línea relativa {recreate}) ANTES de canonizar "
        f"({canonizacion}): arranca escritores del código nuevo sobre identidades viejas"
    )


def test_el_cuerpo_de_la_secuencia_respeta_el_mismo_orden_que_sus_titulos():
    """Los títulos podrían estar en orden y los comandos no. Se comprueban los tres
    hitos irreversibles: parar los escritores, la copia de seguridad y el `COMMIT`."""
    lineas = _seccion_5()
    parar = _primera(lineas, r"docker stop")
    dump = _primera(lineas, r"pg_dump")
    commit = _primera(lineas, r"COMMIT")
    canonizacion = _primera(lineas, r"canonical_refs")
    assert None not in (parar, dump, commit, canonizacion)
    assert parar < dump < commit < canonizacion, (parar, dump, commit, canonizacion)


def test_hay_que_parar_todos_los_escritores_que_el_compose_del_NAS_autoarranca():
    """La lista de contenedores del Paso 1 no puede quedarse corta cuando el compose de
    producción gane un servicio: cualquier contenedor con `restart: unless-stopped` que
    escriba o proyecte tiene que aparecer, o el Recreate lo levantará solo."""
    qnap = yaml.safe_load(_QNAP.read_text(encoding="utf-8"))
    escritores = {
        s["container_name"]
        for nombre, s in qnap["services"].items()
        if s.get("restart") == "unless-stopped"
        # Lista explícita: `postgres`, `redis*` y `frontend` también autoarrancan, y
        # parar el Postgres en mitad de la maniobra sería el error contrario.
        and nombre in {"backend", "worker", "core-api", "core-worker", "core-capture"}
    }
    assert escritores, "el compose del NAS ya no declara escritores con autoarranque"
    # Solo las líneas del comando, no toda §5: `swissjob-backend` aparece también en
    # `swissjob-backend.tar` y esa coincidencia daba por parado lo que nadie para.
    lineas = _seccion_5()
    inicio = _primera(lineas, r"docker stop")
    assert inicio is not None, "§5 no manda parar nada"
    orden = " ".join(
        l for l in lineas[inicio:] if l.startswith(("docker stop", " ")) and "swissjob-" in l
    )
    faltan = sorted(c for c in escritores if c not in orden.split())
    assert not faltan, f"el `docker stop` de §5 no incluye: {faltan}"


@pytest.mark.parametrize("servicio", ["worker", "core-worker"])
def test_el_compose_del_NAS_sigue_autoarrancando_los_escritores(servicio):
    """La premisa del hallazgo, fijada: si algún día dejan de autoarrancar, la guarda de
    arriba deja de proteger nada y hay que revisarla en vez de creerla."""
    qnap = yaml.safe_load(_QNAP.read_text(encoding="utf-8"))
    assert qnap["services"][servicio]["restart"] == "unless-stopped"


# --------------------------------------------------------------------------
# El cuerpo EJECUTABLE de la secuencia (auditoría externa R3 P1-1 y P2-1).
#
# La guarda de arriba fija el ORDEN. Estas fijan que ese orden lo ejecute un script
# versionado y que sus postcondiciones sean condiciones de ejecución. Que cada etapa rota
# detenga la secuencia se demuestra en `test_deploy_cutover_failclosed.py`, con dobles de
# `docker` y `psql`.
# --------------------------------------------------------------------------
_CUTOVER = _REPO / "backend" / "scripts" / "nas_cutover.sh"


def _bloques_bash(lineas: list[str]) -> list[str]:
    """Las líneas DENTRO de las vallas de código de §5: lo que un operador ejecuta.
    La prosa que EXPLICA una tubería rota (`pg_dump | gzip` devolvía el estado de gzip)
    no puede confundirse con la tubería misma."""
    dentro, cuerpo = False, []
    for linea in lineas:
        if linea.startswith("```"):
            dentro = not dentro
            continue
        if dentro:
            cuerpo.append(linea)
    return cuerpo


def test_la_secuencia_la_ejecuta_un_script_versionado():
    """§5 tiene que invocar el script, y §5.2 tiene que llevárselo al NAS: un script que
    no se copia no se ejecuta, y la maniobra volvería a ser copiar y pegar prosa."""
    assert _CUTOVER.is_file(), (
        f"{_CUTOVER} no está montado: esta guarda NO puede saltarse. Ejecuta la suite con "
        "el perfil de dev (docker-compose.yml + docker-compose.dev.yml)."
    )
    guion = _CUTOVER.read_text(encoding="utf-8")
    assert guion.startswith("#!/usr/bin/env bash"), "el cutover no declara su intérprete"
    assert "set -Eeuo pipefail" in guion, "sin pipefail una tubería vuelve a tragarse el error"
    lineas = _seccion_5()
    assert _primera(lineas, r"nas_cutover\.sh cutover") is not None, "§5 no invoca el script"
    assert _primera(lineas, r"nas_cutover\.sh smoke") is not None, "§5 no invoca el smoke"
    assert _primera(lineas, r"scp .*nas_cutover\.sh") is not None or any(
        "nas_cutover.sh" in l for l in lineas[: _primera(lineas, r"#### Paso 1")]
    ), "§5.2 no copia el script al NAS"


def test_ningun_comando_de_la_seccion_5_pierde_el_estado_de_salida():
    """LA reproducción de shell del auditor, invertida: `false | tee /dev/null` → 0. Los
    bloques ejecutables no pueden volver a canalizar `psql` ni `pg_dump` a otro comando,
    porque el estado que sobrevive es el del ÚLTIMO de la tubería."""
    for linea in _bloques_bash(_seccion_5()):
        assert not re.search(r"\bpsql\b[^|]*\|", linea), f"psql canalizado en §5: {linea}"
        assert not re.search(r"\bpg_dump\b[^|]*\|", linea), f"pg_dump canalizado en §5: {linea}"
