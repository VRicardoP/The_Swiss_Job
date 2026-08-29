#!/usr/bin/env python3
"""Coordinación crítica del cutover del NAS: identidad del recurso, exclusión
mutua y publicación durable de las fases de la marcha atrás.

POR QUÉ EXISTE ESTE FICHERO
---------------------------
El invariante «una sola maniobra por base» se reabrió TRES veces en Bash:

  R6  el cerrojo protegía el fichero de copia, no la base;
  R7  su ruta salía de `BACKUP_DIR`, así que cambiar de directorio de copias
      abría una segunda maniobra — y el repuesto por `mkdir` tenía una carrera
      entre dos herederos;
  R8  la ruta seguía siendo configurable (`LOCK_DIR`) y la escalera de
      directorios dependía de los permisos del operador: dos restauraciones
      llegaron a `VERIFIED` a la vez sobre la misma base.

El criterio acordado con el propietario era explícito: a la tercera, la
coordinación deja de parchearse en Bash. Esto es esa extracción, y solo esa —
G3/G6, manifiestos, consultas y smoke siguen donde estaban.

QUÉ GARANTIZA
-------------
1. **Identidad canónica.** La clave del cerrojo es `system_identifier` del
   servidor —que PostgreSQL genera al inicializar el clúster y es el mismo se
   le nombre como se le nombre— más el nombre de la base. Ni el alias del
   contenedor, ni el DSN, ni el operador entran en ella.
2. **Raíz no configurable.** Vive bajo una constante del módulo. No hay opción
   de línea de comandos ni variable de entorno que la mueva: esa era,
   literalmente, la vía de la tercera reapertura.
3. **Durabilidad.** El checkpoint se publica con `os.replace` (atómico) y se
   sincronizan el fichero **y su directorio** — sin lo segundo, el nombre nuevo
   puede no sobrevivir al corte aunque el contenido sí.
4. **Transiciones válidas.** Las fases de la marcha atrás son un conjunto
   cerrado con una tabla de transiciones permitidas; una fase que no puede
   seguir a la anterior es un fallo, no un dato.

Solo biblioteca estándar: se ejecuta en el QNAP, donde no hay entorno virtual.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
from pathlib import Path

# La raíz de los cerrojos. Constante del módulo A PROPÓSITO: es lo único que
# garantiza que dos invocaciones cualesquiera del mismo host resuelvan el mismo
# fichero. No se expone como flag ni se lee del entorno. El padre (`/var/lock`)
# es estándar y de escritura compartida; el subdirectorio se crea al vuelo, lo
# que no es configurabilidad sino instalación perezosa de una ruta fija.
RAIZ_CERROJOS = Path("/var/lock/jobhunt-cutover")

# Las fases de la marcha atrás y qué puede seguir a qué. `None` es «todavía no
# hay checkpoint». Cada transición está permitida porque el programa la
# necesita, no por simetría: lo que no está aquí, no ocurre.
FASES = ("INICIO", "APARTADA", "DESTINO_CREADO", "RESTAURADO",
         "VERIFICACION_FALLIDA", "VERIFIED")
TRANSICIONES: dict[str | None, tuple[str, ...]] = {
    None: ("INICIO",),
    # Reanudar desde INICIO vuelve a medir y a elegir nombre: se reescribe.
    "INICIO": ("INICIO", "APARTADA"),
    # Con la base ya apartada, lo siguiente es crear el destino vacío.
    "APARTADA": ("DESTINO_CREADO",),
    # Un destino sin certificar se recrea cuantas veces haga falta.
    "DESTINO_CREADO": ("DESTINO_CREADO", "RESTAURADO"),
    # La copia entró entera: o certifica, o no cuadra. Y si la base desapareció
    # entre medias, se recrea.
    "RESTAURADO": ("DESTINO_CREADO", "VERIFIED", "VERIFICACION_FALLIDA"),
    # No se re-verifica lo que ya se comprobó que no cuadra: se restaura de nuevo.
    "VERIFICACION_FALLIDA": ("DESTINO_CREADO",),
    # Certificada. Si el estado se rompe otra vez, empieza un ciclo NUEVO.
    "VERIFIED": ("INICIO",),
}

_SEGURO = re.compile(r"[^A-Za-z0-9._-]")


def _morir(mensaje: str) -> None:
    print(f"\n### PARAR — {mensaje}", file=sys.stderr)
    raise SystemExit(1)


def ruta_del_cerrojo(identidad: str, base: str, raiz: Path | None = None) -> Path:
    """El fichero de cerrojo de un recurso. `identidad` es el `system_identifier`
    del servidor; `base`, el nombre de la base. Nada más entra aquí."""
    identidad = identidad.strip()
    if not identidad or not identidad.isdigit():
        _morir(
            f"la identidad del servidor ('{identidad}') no es un system_identifier: "
            "sin identidad canónica no se puede saber si dos maniobras hablan del "
            "MISMO servidor, y ese es justo el fallo que este cerrojo evita"
        )
    if not base.strip():
        _morir("no se puede tomar un cerrojo sin nombre de base")
    return (raiz or RAIZ_CERROJOS) / f"{identidad}.{_SEGURO.sub('_', base)}.cerrojo"


def preparar_raiz(raiz: Path | None = None) -> Path:
    """La raíz tiene que existir y ser escribible por QUIEN EJECUTA. Si no lo es,
    se PARA: elegir otro directorio es exactamente cómo se pierde la exclusión
    mutua entre dos operadores con permisos distintos."""
    destino = raiz or RAIZ_CERROJOS
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        _morir(
            f"no se pudo preparar la raíz de cerrojos {destino} ({e}). NO se elige "
            "otra: dos operadores con directorios distintos no se verían entre sí. "
            f"Créala y dale permiso de escritura al operador: mkdir -p {destino}"
        )
    if not os.access(destino, os.W_OK | os.X_OK):
        _morir(
            f"la raíz de cerrojos {destino} no es escribible por este usuario "
            f"(uid {os.geteuid()}). NO hay alternativa a la que caer: se PARA. "
            "Ajusta los permisos como parte de la instalación"
        )
    return destino


def _ejecutar(args: argparse.Namespace) -> int:
    """Toma el cerrojo del recurso y ejecuta la orden con él puesto. El cerrojo se
    suelta al terminar el proceso —también si muere de `SIGKILL`, porque lo suelta
    el núcleo—, así que no hay huérfanos ni limpieza que inventar."""
    cerrojo = ruta_del_cerrojo(args.identidad, args.db, preparar_raiz())
    fd = os.open(cerrojo, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _morir(
            f"otra maniobra tiene el cerrojo {cerrojo}: NO se lanzan dos a la vez "
            f"sobre la base {args.db} (ni dos restauraciones con copias distintas, "
            "ni un cutover y una restauración). Si estás seguro de que ninguna "
            "sigue viva, comprueba a mano qué proceso lo retiene antes de tocar nada"
        )
    print(f"cerrojo (flock): {cerrojo}", flush=True)
    # El hijo hereda el descriptor: el cerrojo dura lo que dure la maniobra.
    os.set_inheritable(fd, True)
    return subprocess.call(args.orden, close_fds=False)


def _leer_fase(destino: Path) -> str | None:
    if not destino.is_file():
        return None
    for linea in destino.read_text(encoding="utf-8").splitlines():
        if linea.startswith("CK_FASE="):
            return linea.split("=", 1)[1].strip().strip("'")
    return None


def _publicar(args: argparse.Namespace) -> int:
    """Publica una fase nueva de forma atómica y DURABLE, validando antes que la
    transición existe."""
    destino = Path(args.ruta)
    if args.fase not in FASES:
        _morir(f"'{args.fase}' no es una fase de la marcha atrás: {', '.join(FASES)}")
    actual = _leer_fase(destino)
    permitidas = TRANSICIONES.get(actual, ())
    if args.fase not in permitidas:
        _morir(
            f"transición no permitida: {actual or '(sin checkpoint)'} → {args.fase}. "
            f"Desde {actual or '(sin checkpoint)'} solo puede seguir "
            f"{', '.join(permitidas) or '(nada)'}. Un checkpoint que salta una fase "
            "describe un estado que no ocurrió, y de ahí no se reanuda nada"
        )
    cuerpo = "".join(f"{clave}='{valor}'\n" for clave, valor in
                     (d.split("=", 1) for d in args.dato))
    cuerpo += f"CK_FASE='{args.fase}'\n"
    tmp = destino.with_name(destino.name + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, cuerpo.encode("utf-8"))
        os.fsync(fd)                      # el CONTENIDO, en el disco
    finally:
        os.close(fd)
    os.replace(tmp, destino)              # el NOMBRE, de una pieza
    # Y el DIRECTORIO: sin esto el contenido puede estar y el nombre no, que es
    # el caso que deja el estado previo en una base que nadie sabe nombrar.
    dfd = os.open(destino.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    # Y `sync` encima, como PRECONDICIÓN. `fsync` cubre este fichero y su
    # directorio; `sync` cubre el almacén entero, que es donde el NAS falla de
    # verdad (E/S, cuota). Si no se puede sincronizar, lo que viene detrás es
    # DESTRUCTIVO y se para aquí, antes de esa acción.
    try:
        rc = subprocess.call(["sync"])
    except OSError as e:
        _morir(
            f"no se pudo ejecutar 'sync' ({e}): un checkpoint que no se puede "
            "sincronizar no sobrevive a un corte, y lo que viene detrás es "
            "DESTRUCTIVO. NO se continúa"
        )
    if rc != 0:
        _morir(
            f"'sync' falló al persistir el checkpoint {destino} (fase {args.fase}): "
            "puede no haber llegado al disco y lo que viene detrás es DESTRUCTIVO. "
            "Se PARA aquí a propósito, ANTES de esa acción: corrige el almacén "
            "(espacio, E/S) y vuelve a lanzar el MISMO comando"
        )
    print(f"checkpoint {args.fase} → {destino}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="orden_", required=True)

    e = sub.add_parser("ejecutar", help="toma el cerrojo del recurso y ejecuta una orden")
    e.add_argument("--identidad", required=True, help="system_identifier del servidor")
    e.add_argument("--db", required=True, help="nombre de la base protegida")
    e.add_argument("orden", nargs=argparse.REMAINDER)
    e.set_defaults(fn=_ejecutar)

    c = sub.add_parser("publicar", help="publica una fase de la marcha atrás")
    c.add_argument("--ruta", required=True)
    c.add_argument("--fase", required=True)
    c.add_argument("--dato", action="append", default=[], metavar="CLAVE=VALOR")
    c.set_defaults(fn=_publicar)

    args = p.parse_args(argv)
    if getattr(args, "orden", None) == []:
        _morir("`ejecutar` necesita una orden después de --")
    if getattr(args, "orden", None) and args.orden[0] == "--":
        args.orden = args.orden[1:]
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
