"""El cutover del NAS FALLA CERRADO: cada etapa rota tiene que salir distinto de cero.

REGRESIÓN auditoría externa R3 P1-1. El runbook decía «si no cuadra, se PARA» y sus
postcondiciones eran prosa: `docker ps` no comprobaba nada, `pg_dump | gzip` devolvía el
estado de `gzip` y `psql | tee` el de `tee`. El auditor lo ejecutó literalmente con los
cinco escritores vivos y obtuvo `POSTCOND_EXIT=0`; con la SEGUNDA copia SQL abortada tras
confirmar la primera, la secuencia seguía hasta el Paso 5 — el estado que el propio
documento declara IRREPARABLE (`core0025` sella las cohortes).

La guarda de orden (`test_deploy_order.py`) protege la SECUENCIA; no puede demostrar que
una etapa rota la detenga. Esto sí: ejecuta `backend/scripts/nas_cutover.sh` con dobles de
`docker` y `psql` en el PATH (el doble de `docker` delega en el de `psql`, igual que el
runbook, que invoca `psql` a través de `docker exec`), inyecta un fallo por etapa y exige
salida no cero. El camino feliz sale 0: sin eso, «todo rojo» no demostraría nada.

`gzip` NO se dobla —es el real— así que `gzip -t` se ejercita en el camino feliz; lo que
se inyecta del Paso 2 es el fallo de `pg_dump` y la ausencia de cada esquema en el volcado.
"""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_REPO = Path("/app")
_SCRIPT = _REPO / "backend" / "scripts" / "nas_cutover.sh"

# Cifras del corpus SIMULADO. No son las del NAS ni las locales: el script no lleva
# ninguna constante de corpus, mide antes y aserta contra lo que él mismo midió, y estas
# cifras solo tienen que ser COHERENTES entre sí:
#   slots después = 100 + 2 informes × 4 slots de clones = 108
#   jobs  después = 1000 − 2 informes × 3 clones fusionados = 994
_ANTES = {"slots": 100, "jobs": 1000, "pares": 10, "juicios": "5 5"}
_DESPUES = {"slots": 108, "jobs": 994, "pares": 10, "juicios": "5 5"}
_RELEASE = "deadbee"
_HEAD = "core0035"

_DOBLE_DOCKER = r"""#!/usr/bin/env bash
# DOBLE de `docker`. $ROMPER nombra la etapa que se rompe.
set -u
d=$DOBLES_DIR
r=${ROMPER:-}
case "$1" in
  stop) : >"$d/parados"; exit 0 ;;
  ps)
    if [ ! -f "$d/parados" ] || [ "$r" = paso1 ]; then
      vivos="swissjob-backend swissjob-worker swissjob-core-api swissjob-core-worker swissjob-core-capture"
    elif [ -f "$d/recreado" ]; then
      vivos="swissjob-backend swissjob-worker swissjob-core-api swissjob-core-worker swissjob-core-capture"
      [ "$r" = smoke_escritor_caido ] && vivos="swissjob-backend swissjob-worker"
    else
      vivos="swissjob-postgres swissjob-redis"
    fi
    for v in $vivos; do
      case "$*" in *Status*) printf '%s\t%s\n' "$v" "Up 3 minutes (healthy)" ;;
                   *)        printf '%s\n' "$v" ;; esac
    done
    exit 0 ;;
  rmi|logs) exit 0 ;;
  load) [ "$r" = paso3_load ] && exit 1; exit 0 ;;
  image) exit 0 ;;
  inspect) echo "swissjob_core-net "; exit 0 ;;
  exec)
    shift
    while [ "${1#-}" != "$1" ]; do shift; done
    contenedor=$1; shift
    case "$1" in
      pg_dump)
        [ "$r" = paso2_pg_dump ] && { echo "pg_dump: error: connection to server failed" >&2; exit 1; }
        [ "$r" != paso2_sin_public ]  && echo "CREATE TABLE public.jobs ();"
        [ "$r" != paso2_sin_jobhunt ] && echo "CREATE TABLE jobhunt.sources ();"
        exit 0 ;;
      psql) shift; exec psql "$@" ;;
      python)
        # El smoke: el programa embebido en el script se ejecuta DE VERDAD contra el
        # servidor HTTP que levanta el test en 127.0.0.1:8000.
        shift; [ "$1" = "-" ] && shift
        cat >"$d/smoke.py"; exec python3 "$d/smoke.py" "$@" ;;
    esac
    exit 0 ;;
  run)
    case "$*" in
      *"/opt/jobhunt-release/RELEASE"*)
        [ "$r" = paso3_release_unknown ] && { echo unknown; exit 0; }
        echo "RELEASE_SIMULADA"; exit 0 ;;
      *canonical_refs*)
        n=$(cat "$d/canon" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$d/canon"
        juicios=2
        case "$*" in *--dry-run*) seco=true ;; *) seco=false ;; esac
        filas=6
        [ "$n" = 3 ] && filas=0                                  # idempotente: ceros
        [ "$n" = 3 ] && [ "$r" = paso5_idempotencia ] && filas=6
        [ "$n" = 2 ] && [ "$r" = paso5_json ] && juicios=3
        printf '{\n  "filas_canonizadas_en_legacy": %s,\n  "juicios_remapeados": %s,\n  "pares_remapeados": 1,\n  "dry_run": %s\n}\n' \
          "$filas" "$juicios" "$seco"
        exit 0 ;;
    esac
    exit 0 ;;
esac
exit 0
"""

_DOBLE_PSQL = r"""#!/usr/bin/env bash
# DOBLE de `psql`. El de `docker` delega aquí, igual que el runbook via `docker exec`.
set -u
d=$DOBLES_DIR
r=${ROMPER:-}

sql=""
esperando=0
for a in "$@"; do
  [ "$esperando" = 1 ] && { sql=$a; esperando=0; }
  [ "$a" = "-c" ] && esperando=1
done

if [ -n "$sql" ]; then                      # consultas escalares (medición / 4b)
  fase=antes; [ -f "$d/firme" ] && fase=despues
  case "$sql" in
    *labeled_dedup_cohorts*)
      [ "$r" = paso4b_enclavamiento ] && { echo 67; exit 0; }; echo 0 ;;
    *"j.hash IS NULL"*)
      if [ "$fase" = antes ]; then echo __SLOTS_ANTES__
      elif [ "$r" = paso6_slots ]; then echo 999
      else echo __SLOTS_DESPUES__; fi ;;
    *"FROM public.jobs"*)
      if [ "$fase" = antes ]; then echo __JOBS_ANTES__
      elif [ "$r" = paso6_jobs ]; then echo __JOBS_ANTES__
      else echo __JOBS_DESPUES__; fi ;;
    *labeled_judgments*)
      if [ "$fase" = antes ]; then echo "__JUICIOS_ANTES__"
      elif [ "$r" = paso6_juicios ]; then echo "5 4"
      else echo "__JUICIOS_DESPUES__"; fi ;;
    *labeled_dedup_pairs*)
      if [ "$fase" = antes ]; then echo __PARES_ANTES__
      elif [ "$r" = paso6_pares ]; then echo 9
      else echo __PARES_DESPUES__; fi ;;
  esac
  exit 0
fi

cuerpo=$(cat)                               # las dos copias, por `-f -`
case "$cuerpo" in *"COMMIT;"*) modo=firme ;; *) modo=seco ;; esac
case "$cuerpo" in *"-- G6"*) copia=segunda ;; *) copia=primera ;; esac
[ "$modo" = firme ] && : >"$d/firme"

if [ "$r" = "paso4a_$copia" ] && [ "$modo" = seco ]; then
  echo "psql:-:215: ERROR:  no se pudo bloquear la fila" >&2; exit 3
fi
if [ "$r" = "paso4c_$copia" ] && [ "$modo" = firme ]; then
  echo "psql:-:930: ERROR:  cohorte sellada: el par es inmutable" >&2; exit 3
fi

senal=0; [ "$r" = paso4b_senal ] && senal=2
clones=3; [ "$r" = paso4c_informe ] && [ "$modo" = firme ] && clones=4
cat <<EOF
BEGIN
reescritas|12
clones fusionados|$clones
match_results descartados por la fusion|$senal
  ... de ellos CON senal del usuario|$senal
sombra: slots reapuntados al hash canonico|9
sombra: slots de clones (los cierra el op=D del PASO 6)|4
EOF
exit 0
"""


def _plantar_dobles(raiz: Path) -> Path:
    """Escribe los dos dobles y devuelve el directorio que va al frente del PATH."""
    binarios = raiz / "bin"
    binarios.mkdir(parents=True, exist_ok=True)
    psql = _DOBLE_PSQL
    for clave, valor in (
        ("__SLOTS_ANTES__", _ANTES["slots"]), ("__SLOTS_DESPUES__", _DESPUES["slots"]),
        ("__JOBS_ANTES__", _ANTES["jobs"]), ("__JOBS_DESPUES__", _DESPUES["jobs"]),
        ("__PARES_ANTES__", _ANTES["pares"]), ("__PARES_DESPUES__", _DESPUES["pares"]),
        ("__JUICIOS_ANTES__", _ANTES["juicios"]), ("__JUICIOS_DESPUES__", _DESPUES["juicios"]),
    ):
        psql = psql.replace(clave, str(valor))
    for nombre, cuerpo in (("docker", _DOBLE_DOCKER), ("psql", psql)):
        destino = binarios / nombre
        destino.write_text(cuerpo, encoding="utf-8")
        destino.chmod(0o755)
    return binarios


def _montar_nas(raiz: Path) -> dict[str, str]:
    """Un NAS de mentira: los tres tar, las dos copias SQL y los directorios."""
    base, scripts = raiz / "nas", raiz / "nas" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for tar in ("swissjob-core.tar", "swissjob-backend.tar", "swissjob-frontend.tar"):
        (base / tar).write_text("tar", encoding="utf-8")
    for marca, fichero in (("G3", "g3.sql"), ("G6", "g6.sql")):
        (scripts / fichero).write_text(f"-- {marca}\nBEGIN;\nROLLBACK;\n", encoding="utf-8")
    return {
        "BASE_DIR": str(base),
        "SCRIPTS_DIR": str(scripts),
        "BACKUP_DIR": str(raiz / "backups"),
        "WORK_DIR": str(raiz / "trabajo"),
        "COPIAS_SQL": "g3.sql g6.sql",
        "DOBLES_DIR": str(raiz / "estado"),
    }


def _ejecutar(raiz: Path, subcomando: str, romper: str | None) -> subprocess.CompletedProcess:
    assert _SCRIPT.is_file(), (
        f"{_SCRIPT} no está montado: esta guarda NO puede saltarse. Ejecuta la suite con "
        "el perfil de dev (docker-compose.yml + docker-compose.dev.yml)."
    )
    (raiz / "estado").mkdir(parents=True, exist_ok=True)
    entorno = dict(os.environ)
    entorno.update(_montar_nas(raiz))
    entorno["PATH"] = f"{_plantar_dobles(raiz)}:{entorno['PATH']}"
    entorno["CORE_NET"] = "red-de-mentira"
    if romper:
        entorno["ROMPER"] = romper
    return subprocess.run(
        ["bash", str(_SCRIPT), subcomando],
        env=entorno, capture_output=True, text=True, timeout=120,
    )


# --------------------------------------------------------------------------
# Servidor de /v1/health y /v1/ready para el Paso 7 (el programa del smoke se
# ejecuta DE VERDAD: no hay un segundo parser en el test).
# --------------------------------------------------------------------------
_RESPUESTAS: dict[str, dict] = {}


class _Sonda(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        cuerpo = json.dumps(_RESPUESTAS.get(self.path, {})).encode()
        self.send_response(200 if self.path in _RESPUESTAS else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *_):  # silencio
        pass


@pytest.fixture(scope="module")
def sonda():
    servidor = HTTPServer(("127.0.0.1", 8000), _Sonda)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    yield
    servidor.shutdown()


def _preparar_smoke(raiz: Path, romper: str | None) -> None:
    ready = {"status": "ready", "alembic": _HEAD, "release": _RELEASE, "authoritative": True}
    if romper == "smoke_status":
        ready["status"] = "ok"          # LO QUE EL RUNBOOK EXIGÍA (R3 P2-1)
    elif romper == "smoke_release":
        ready["release"] = "0tra1mg"
    elif romper == "smoke_authoritative":
        ready["authoritative"] = False
    elif romper == "smoke_alembic":
        ready["alembic"] = "core0030"
    _RESPUESTAS.clear()
    _RESPUESTAS["/v1/ready"] = ready
    _RESPUESTAS["/v1/health"] = {
        "status": "ok", "release": _RELEASE, "alembic_expected": _HEAD, "authoritative": True,
    }
    trabajo = raiz / "trabajo"
    trabajo.mkdir(parents=True, exist_ok=True)
    if romper != "smoke_sin_paso3":
        (trabajo / "estado.env").write_text(f"RELEASE_ESPERADA={_RELEASE}\n", encoding="utf-8")
    (raiz / "estado").mkdir(parents=True, exist_ok=True)
    (raiz / "estado" / "parados").touch()
    (raiz / "estado" / "recreado").touch()


# --------------------------------------------------------------------------
# Camino feliz: sin él, «todo rojo» no demostraría nada
# --------------------------------------------------------------------------
def test_el_camino_feliz_de_los_pasos_1_a_6_sale_cero(tmp_path):
    p = _ejecutar(tmp_path, "cutover", None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "las cuatro invariantes del Paso 6 cuadran" in p.stdout


def test_el_camino_feliz_del_smoke_sale_cero(tmp_path, sonda):
    _preparar_smoke(tmp_path, None)
    p = _ejecutar(tmp_path, "smoke", None)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "smoke OK" in p.stdout


def test_la_copia_de_seguridad_queda_verificada_y_con_nombre_definitivo(tmp_path):
    """El `.gz` solo gana su nombre final tras `gzip -t` y los dos esquemas."""
    assert _ejecutar(tmp_path, "cutover", None).returncode == 0
    copias = sorted((tmp_path / "backups").glob("pre_canonizacion_*.sql.gz"))
    assert len(copias) == 1, copias
    assert not list((tmp_path / "backups").glob("*.parcial"))
    subprocess.run(["gzip", "-t", str(copias[0])], check=True)


# --------------------------------------------------------------------------
# Una etapa rota, una salida no cero
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "etapa",
    [
        "paso1",                 # los cinco escritores siguen vivos tras el `stop`
        "paso2_pg_dump",         # pg_dump falla y el .gz saldría válido y vacío
        "paso2_sin_public",
        "paso2_sin_jobhunt",     # el rol no lee `jobhunt`: la copia no sirve
        "paso3_load",
        "paso3_release_unknown", # imagen sin RELEASE_SHA → authoritative: false
        "paso4a_primera",
        "paso4a_segunda",        # preflight de LAS DOS antes de confirmar la primera
        "paso4b_enclavamiento",  # cohortes SELLADAS afectadas
        "paso4b_senal",          # descartaría match_results con señal del usuario
        "paso4c_primera",
        "paso4c_segunda",        # EL caso irreparable: la primera ya confirmada
        "paso4c_informe",        # el informe en firme difiere del ensayo
        "paso5_json",            # la aplicación no cuadra con su --dry-run
        "paso5_idempotencia",
        "paso6_slots",
        "paso6_jobs",
        "paso6_juicios",
        "paso6_pares",
    ],
)
def test_cada_etapa_rota_detiene_la_secuencia(tmp_path, etapa):
    p = _ejecutar(tmp_path, "cutover", etapa)
    assert p.returncode != 0, f"la etapa {etapa} falló y la secuencia SIGUIÓ:\n{p.stdout}"
    assert "PARAR" in p.stdout + p.stderr, p.stdout + p.stderr


def test_la_segunda_copia_abortada_no_deja_pasar_al_paso_5(tmp_path):
    """El caso peor que nombra el runbook, aislado: la primera copia CONFIRMADA y la
    segunda abortada. La secuencia no puede llegar a `canonical_refs`."""
    p = _ejecutar(tmp_path, "cutover", "paso4c_segunda")
    assert p.returncode != 0
    assert "Paso 5" not in p.stdout, p.stdout
    assert "RESTAURA el volcado del Paso 2" in p.stdout + p.stderr


@pytest.mark.parametrize(
    "etapa",
    ["smoke_status", "smoke_release", "smoke_authoritative", "smoke_alembic",
     "smoke_escritor_caido", "smoke_sin_paso3"],
)
def test_el_smoke_falla_cerrado(tmp_path, sonda, etapa):
    _preparar_smoke(tmp_path, etapa)
    p = _ejecutar(tmp_path, "smoke", etapa)
    assert p.returncode != 0, f"el smoke aceptó {etapa}:\n{p.stdout}"


def test_el_smoke_rechaza_el_status_ok_que_exigia_el_runbook(tmp_path, sonda):
    """R3 P2-1, invertido: `ok` era lo que pedía la postcondición del Paso 7 y NUNCA lo
    devuelve la API. Hoy eso es rojo, y el rojo dice por qué."""
    _preparar_smoke(tmp_path, "smoke_status")
    p = _ejecutar(tmp_path, "smoke", "smoke_status")
    assert p.returncode != 0
    assert "'ok'" in p.stdout + p.stderr and "'ready'" in p.stdout + p.stderr


@pytest.mark.parametrize(
    ("entorno", "porque"),
    [
        ({"ENSAYO": "1"}, "sin CORE_DSN el Paso 5 escribiría en la base viva"),
        ({"ENSAYO": "1", "PG_DB": "swissjobhunter",
          "CORE_DSN": "postgresql://u:p@h:5432/swissjob_ensayo"}, "PG_DB es la de producción"),
        ({"ENSAYO": "1", "PG_DB": "swissjob_ensayo",
          "CORE_DSN": "postgresql://u:p@h:5432/swissjobhunter"}, "CORE_DSN es la de producción"),
    ],
)
def test_el_unico_escape_del_ensayo_no_puede_apuntar_a_produccion(tmp_path, entorno, porque):
    """`ENSAYO=1` es la única salida del fallo cerrado y se salta dos pasos, así que no
    puede convertirse en la maniobra real por descuido — ni tocar la base viva."""
    guardado = {k: os.environ.get(k) for k in entorno}
    os.environ.update(entorno)
    try:
        p = _ejecutar(tmp_path, "cutover", None)
    finally:
        for k, v in guardado.items():
            os.environ.pop(k, None) if v is None else os.environ.update({k: v})
    assert p.returncode != 0, f"el ensayo se aceptó aunque {porque}:\n{p.stdout}"
