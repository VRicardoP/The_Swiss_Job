"""El cutover del NAS FALLA CERRADO: cada etapa rota tiene que salir distinto de cero.

REGRESIÓN auditoría externa R3 P1-1. El runbook decía «si no cuadra, se PARA» y sus
postcondiciones eran prosa: `docker ps` no comprobaba nada, `pg_dump | gzip` devolvía el
estado de `gzip` y `psql | tee` el de `tee`. El auditor lo ejecutó literalmente con los
cinco escritores vivos y obtuvo `POSTCOND_EXIT=0`; con la SEGUNDA copia SQL abortada tras
confirmar la primera, la secuencia seguía hasta el Paso 5 — el estado que el propio
documento declara IRREPARABLE (`core0025` sella las cohortes).

REGRESIÓN auditoría externa R4 (los cinco P1 viven en este script). Los dobles de R3
respetaban precisamente los agregados y las cadenas que había que poner en duda, así que
aquí se rompen a propósito:

  P1-1  un `CORE_DSN` de PRODUCCIÓN con `?query`, `#fragmento` o percent-encoding
        esquivaba la guarda del ensayo (comparaba por SUFIJO) y llegaba al Paso 5 en
        firme. Además el DSN podía ser sintácticamente inocente y apuntar a otra base:
        ahora hay una SONDA que compara la identidad de la base por psql y por el core.
  P1-2  el enclavamiento 4b era un oráculo APROXIMADO propio del script: paraba por pares
        que G3/G6 no remapean (67 contra 0 en local). El caso `enclavamiento_falso_rojo`
        exige que eso ya NO pare; `paso4b_enclavamiento` exige que lo que los ensayos SÍ
        declaran siga parando.
  P1-3  las cuatro invariantes eran cantidades: `paso6_identidad_*` mueve la identidad sin
        mover ni una cifra, que es exactamente lo que el auditor reprodujo.

La guarda de orden (`test_deploy_order.py`) protege la SECUENCIA; no puede demostrar que
una etapa rota la detenga. Esto sí: ejecuta `backend/scripts/nas_cutover.sh` con dobles de
`docker` y `psql` en el PATH (el doble de `docker` delega en el de `psql`, igual que el
runbook, que invoca `psql` a través de `docker exec`), inyecta un fallo por etapa y exige
salida no cero. Los caminos felices salen 0: sin eso, «todo rojo» no demostraría nada.

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
# La identidad que ve psql y la que declara el core tienen que coincidir salvo
# cuando se rompe la sonda a propósito.
IDENTIDAD="${PG_DB:-swissjobhunter}|16384|2026-08-28 00:00:00.000000"

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
      *identidad_destino*)
        # La sonda de destino (R4 P1-1): la identidad que ve el DSN del core.
        if [ "$r" = sonda_destino ]; then echo "otra_base|99999|2000-01-01 00:00:00.000000"
        else echo "$IDENTIDAD"; fi
        exit 0 ;;
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

if [ -n "$sql" ]; then                      # consultas escalares y manifiestos
  fase=antes; [ -f "$d/firme" ] && fase=despues
  case "$sql" in
    *pg_postmaster_start_time*)             # sonda de destino (R4 P1-1)
      echo "${PG_DB:-swissjobhunter}|16384|2026-08-28 00:00:00.000000" ;;
    *"p.id::text"*)                         # MANIFIESTO de pares (identidades)
      if [ "$fase" = antes ]; then printf 'p-1\np-2\n'
      elif [ "$r" = paso6_identidad_pares ]; then printf 'p-1\np-3\n'
      else printf 'p-1\np-2\n'; fi ;;
    *"j.set_id::text"*)                     # MANIFIESTO de juicios (identidades)
      if [ "$fase" = antes ]; then printf 'j-1\nj-2\n'
      elif [ "$r" = paso6_identidad_juicios ]; then printf 'j-1\nj-3\n'
      else printf 'j-1\nj-2\n'; fi ;;
    *labeled_dedup_cohorts*)
      # El oráculo APROXIMADO que el script tenía en Bash (R4 P1-2). Ya no se
      # consulta; sigue aquí para demostrar que un 67 no vuelve a parar nada.
      if [ "$r" = paso4b_enclavamiento_legacy ] || [ "$r" = enclavamiento_falso_rojo ]; then
        echo 67
      else echo 0; fi ;;
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

cuerpo=$(cat)                               # las dos copias y la verificación por hash, por `-f -`

# Verificación por IDENTIDAD de las vacantes (R4 P1-3): el script la GENERA con los
# hashes que los dry-runs declararon.
case "$cuerpo" in
  *ident_desaparece*)
    viejas=0; canonicas=0
    [ "$r" = paso6_identidad_jobs ] && viejas=2
    [ "$r" = paso6_identidad_canonicas ] && canonicas=1
    printf 'identidad: viejas que NO desaparecieron|%s\n' "$viejas"
    printf 'identidad: canonicas que NO aparecieron|%s\n' "$canonicas"
    exit 0 ;;
esac

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
# El enclavamiento lo DECLARA el propio ensayo desde su tabla de supervivientes (R4 P1-2).
enclave=0; [ "$r" = paso4b_enclavamiento ] && enclave=1
cat <<EOF
BEGIN
reescritas|12
clones fusionados|$clones
match_results descartados por la fusion|$senal
  ... de ellos CON senal del usuario|$senal
sombra: slots reapuntados al hash canonico|9
sombra: slots de clones (los cierra el op=D del PASO 6)|4
EOF
[ "$r" != enclavamiento_sin_concepto ] &&
  printf 'enclavamiento: refs de cohortes SELLADAS que ESTE script remapea|%s\n' "$enclave"

# IDENTIDADES declaradas: dos hashes que desaparecen y uno canónico por copia.
if [ "$copia" = primera ]; then p=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; else p=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; fi
sufijo=01
[ "$r" = paso4c_ident ] && [ "$modo" = firme ] && sufijo=0f
printf 'IDENT|desaparece|%s%s\n' "$p" "$sufijo"
printf 'IDENT|desaparece|%s02\n' "$p"
printf 'IDENT|canonico|%s0a\n' "$p"
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
    else:
        entorno.pop("ROMPER", None)
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
    assert "identidad de vacantes" in p.stdout


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
        "sonda_destino",         # el core apunta a OTRA base que psql (R4 P1-1)
        "paso4a_primera",
        "paso4a_segunda",        # preflight de LAS DOS antes de confirmar la primera
        "paso4b_enclavamiento",  # el ensayo DECLARA refs de cohortes selladas
        "enclavamiento_sin_concepto",  # y si no lo declara, tampoco se sigue
        "paso4b_senal",          # descartaría match_results con señal del usuario
        "paso4c_primera",
        "paso4c_segunda",        # EL caso irreparable: la primera ya confirmada
        "paso4c_informe",        # el informe en firme difiere del ensayo
        "paso4c_ident",          # …y las identidades declaradas, también
        "paso5_json",            # la aplicación no cuadra con su --dry-run
        "paso5_idempotencia",
        "paso6_slots",
        "paso6_jobs",
        "paso6_juicios",
        "paso6_pares",
        "paso6_identidad_pares",     # R4 P1-3: cifras iguales, par distinto
        "paso6_identidad_juicios",   # R4 P1-3: cifras iguales, juicio distinto
        "paso6_identidad_jobs",      # un hash declarado fusionado sigue vivo
        "paso6_identidad_canonicas", # un hash canónico declarado no existe
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


# --------------------------------------------------------------------------
# R4 P1-2 — el enclavamiento no puede parar por pares que G3/G6 no remapean
# --------------------------------------------------------------------------
def test_el_enclavamiento_no_para_por_pares_que_los_scripts_no_remapean(tmp_path):
    """El oráculo aproximado contaba 67 pares en local mientras los mapas de G3/G6
    remapeaban 0: el procedimiento paraba sin motivo y el remedio que sugería (cargar una
    cohorte nueva) podía dejarlo bloqueado para siempre. Hoy la cifra la declara cada
    ensayo desde su PROPIA tabla de supervivientes, y un 67 en la consulta vieja no para
    nada."""
    p = _ejecutar(tmp_path, "cutover", "enclavamiento_falso_rojo")
    assert p.returncode == 0, (
        "el enclavamiento sigue parando por pares que los scripts no remapean:\n"
        + p.stdout + p.stderr
    )
    assert "declara 0 refs de cohortes SELLADAS" in p.stdout, p.stdout


def test_el_enclavamiento_para_por_lo_que_el_ensayo_si_declara(tmp_path):
    """El lado opuesto: si un ensayo declara que SÍ remapearía un ref congelado, no hay
    Paso 4c. Es el enclavamiento sin marcha atrás que `core0025` hace irreparable."""
    p = _ejecutar(tmp_path, "cutover", "paso4b_enclavamiento")
    assert p.returncode != 0
    assert "cohorte NUEVA" in p.stdout + p.stderr
    assert "Paso 4c" not in p.stdout, p.stdout


# --------------------------------------------------------------------------
# R4 P1-3 — identidades, no cardinalidades
# --------------------------------------------------------------------------
def test_las_cifras_pueden_cuadrar_y_aun_asi_perderse_un_par_conocido(tmp_path):
    """La reproducción del auditor: mover las source listings de un par positivo a otro
    par deja las cuatro fórmulas intactas (PARES antes = PARES después) y pierde
    exactamente el par que importaba. Con manifiestos por identidad eso es rojo, y el rojo
    NOMBRA el par perdido."""
    p = _ejecutar(tmp_path, "cutover", "paso6_identidad_pares")
    assert p.returncode != 0, p.stdout
    salida = p.stdout + p.stderr
    assert "IDENTIDAD" in salida and "p-2" in salida, salida


# --------------------------------------------------------------------------
# R4 P1-5 — el smoke
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# R4 P1-1 — el aislamiento del ensayo
# --------------------------------------------------------------------------
_ENSAYO_OK = {
    "ENSAYO": "1",
    "PG_DB": "swissjob_ensayo",
    "CORE_DSN": "postgresql+asyncpg://u:p@postgres:5432/swissjob_ensayo",
}


def _con_entorno(tmp_path, entorno, subcomando="cutover", romper=None):
    guardado = {k: os.environ.get(k) for k in entorno}
    os.environ.update(entorno)
    try:
        return _ejecutar(tmp_path, subcomando, romper)
    finally:
        for k, v in guardado.items():
            os.environ.pop(k, None) if v is None else os.environ.update({k: v})


@pytest.mark.parametrize(
    ("dsn", "porque"),
    [
        (None, "sin CORE_DSN el Paso 5 escribiría en la base viva"),
        ("postgresql://u:p@postgres:5432/swissjobhunter", "es la base de producción"),
        ("postgresql://u:p@postgres:5432/swissjobhunter?ssl=require",
         "query string: la guarda comparaba por SUFIJO (R4 P1-1)"),
        ("postgresql://u:p@postgres:5432/swissjobhunter?ssl=require&application_name=x",
         "varios parámetros tras la base de producción"),
        ("postgresql://u:p@postgres:5432/swissjobhunter#/swissjob_ensayo",
         "fragmento: el sufijo visible no es la base"),
        ("postgresql://u:p@postgres:5432/swissjobhunte%72",
         "percent-encoding: decodifica a la base de producción"),
        ("postgresql://u:p@postgres:5432/%73wissjobhunter",
         "percent-encoding en la primera letra"),
        ("postgresql://u:p@postgres:5432/swissjob_ensayo?dbname=swissjobhunter",
         "un parámetro que puede REDEFINIR la base de destino"),
        ("postgresql://u:p@postgres:5432/swissjob_ensayo?host=otro",
         "un parámetro que puede redefinir el host"),
        ("postgresql://u:p@otro-servidor:5432/swissjob_ensayo",
         "la base se llama bien pero el servidor no es el esperado"),
        ("postgresql://u:p@postgres:5432/otra_base",
         "el core mediría una base distinta de la de psql"),
        ("esto no es una url", "no es parseable"),
        ("postgresql://u:p@postgres:5432/", "no nombra ninguna base"),
        ("mysql://u:p@postgres:3306/swissjob_ensayo", "no es un DSN de PostgreSQL"),
    ],
)
def test_el_unico_escape_del_ensayo_no_puede_apuntar_a_produccion(tmp_path, dsn, porque):
    """`ENSAYO=1` es la única salida del fallo cerrado y se salta dos pasos, así que no
    puede convertirse en la maniobra real por descuido — ni tocar la base viva. La guarda
    vieja comparaba por sufijo y `…/swissjobhunter?ssl=require` llegaba al Paso 5 EN
    FIRME."""
    entorno = {"ENSAYO": "1", "PG_DB": "swissjob_ensayo", "CORE_DSN": dsn or ""}
    p = _con_entorno(tmp_path, entorno)
    assert p.returncode != 0, f"el ensayo se aceptó aunque {porque}:\n{p.stdout}"
    assert "Paso 4c" not in p.stdout, f"llegó a escribir con un DSN que {porque}:\n{p.stdout}"
    assert "Paso 5" not in p.stdout, f"llegó al Paso 5 con un DSN que {porque}:\n{p.stdout}"


def test_el_ensayo_con_pg_db_de_produccion_no_arranca(tmp_path):
    p = _con_entorno(
        tmp_path,
        {"ENSAYO": "1", "PG_DB": "swissjobhunter",
         "CORE_DSN": "postgresql://u:p@postgres:5432/swissjob_ensayo"},
    )
    assert p.returncode != 0
    assert "producción" in p.stdout + p.stderr


def test_un_ensayo_bien_aislado_si_recorre_la_maniobra(tmp_path):
    """Sin esto, «todo rojo» solo demostraría que la guarda rechaza cualquier cosa."""
    p = _con_entorno(tmp_path, dict(_ENSAYO_OK))
    assert p.returncode == 0, p.stdout + p.stderr
    assert "ENSAYO validado" in p.stdout
    assert "Pasos 1–6 OK" in p.stdout


def test_la_sonda_de_destino_para_si_el_core_ve_otra_base(tmp_path):
    """La guarda mira una CADENA; la sonda mira la BASE. Un DSN sintácticamente perfecto
    puede resolver a otro servidor (env-file, DNS, pooler): entonces el Paso 4 escribiría
    en una base y el Paso 5 en otra."""
    p = _con_entorno(tmp_path, dict(_ENSAYO_OK), romper="sonda_destino")
    assert p.returncode != 0
    assert "Paso 4c" not in p.stdout, p.stdout
    assert "NO apunta a la base de psql" in p.stdout + p.stderr
