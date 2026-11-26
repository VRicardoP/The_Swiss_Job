# Plan: correcciones pendientes del NAS tras el estudio del 25 de noviembre

> **Estado a 26-11-2026.** B (Redis), C (manifiestos) y D (verificador y copia versionada) hechas. De A, el script ya registra el error real y la causa está identificada (`invalid_grant`); **falta que el propietario reautorice Gmail**. D3 de este documento (definiciones muertas) también hecha. Sigue pendiente la rotación de la contraseña de Redis.

Para el agente que ejecuta las correcciones. Complementa a [PLAN_TRASLADO_SWISSJOB_NAS_A_SSD_2026-11-25.md](PLAN_TRASLADO_SWISSJOB_NAS_A_SSD_2026-11-25.md), que aquí se llama «el plan de traslado». Léelo antes: este documento usa sus reglas, sus variables de sesión (`D`, `C`, `N`, `S`, `A`, `U`, `F`) y sus fases como puntos de enganche.

Son cuatro correcciones independientes entre sí. Todo lo afirmado se midió en el NAS el 25 de noviembre de 2026 en solo lectura.

| | Corrección | Corte de servicio | Cuándo |
|---|---|---|---|
| A | El supervisor no consigue enviar correo | No | Cuando se quiera |
| B | `swissjob-redis` arrancaría con un `maxmemory` mayor que su límite de memoria | Sí, dentro de la ventana de la fase 6 del plan de traslado | Fase 6 |
| C | El directorio de manifiestos `unification-e15-20260914` sigue en `Public` | No | Fases 3 y 7 |
| D | El verificador de secretos no ve literales en listas YAML, y la copia versionada del compose de Container Station está desfasada | No | D1 cuando se quiera; D2 después de la fase 6 |

## Reglas

Valen las ocho del plan de traslado. Se añade una:

9. **Lectura de ficheros con secretos.** No vuelques un fichero de configuración del NAS a la conversación con un `sed` que «oculte» valores: el 25 de noviembre ese enmascarado falló dos veces. Lee solo nombres de clave (`sed -n 's/^\([A-Za-z_]*\) *[=:].*/\1/p'`), cuenta coincidencias o procesa el fichero en memoria con un programa que imprima únicamente el resultado.

## Secretos que quedaron expuestos durante el estudio

Aparecieron en la salida de la sesión de estudio, no en ningún fichero:

- **La contraseña de `swissjob-redis`.** Ya constaba como expuesta el 24 de noviembre y sin rotar. La rotación tiene propuesta propia en `ReactPortfolio/ops/nas-saturacion/propuesta-redis.md` y sigue siendo del propietario; la corrección B no la sustituye.
- **`AppID`, `enRefreshToken` y `enAccessToken` de `/etc/config/ssmtp/ssmtp.conf`**, que son la autorización de Gmail del QTS, cifrada por el propio QTS. Si la corrección A termina en una reautorización de Gmail, se renuevan solos; si no, el propietario decide si reautoriza igualmente.

## A. El supervisor no consigue enviar correo

### Lo que se sabe

- El registro tiene 887 líneas `NO ENVIADO (sin sendmail ni SMTP_HOST)` y ningún `ENVIADO`, desde su primera línea, del 15 de octubre.
- El mensaje es engañoso. `/usr/sbin/ssmtp` existe y es ejecutable, así que el script sí entra en la rama de `sendmail`. Lo que ocurre es que `sendmail -t` devuelve error, el script cae a la rama de SMTP, no encuentra `SMTP_HOST` y escribe ese texto. El error real de `sendmail` se pierde: el script no lo captura y el cron redirige todo a `/dev/null`.
- `ssmtp.conf` apunta a `smtp.gmail.com:465` con la autorización OAuth del QTS, sin usuario ni contraseña. El QTS lo reescribe en cada arranque.
- **La causa del fallo de `sendmail` no está identificada.** Averiguarla exige intentar un envío, que no se hizo en el estudio.

### A1. 🔒 Diagnóstico: un envío de prueba

Manda un correo al propietario. En el NAS, como `Ricardo`:

```bash
printf 'To: %s\nFrom: %s\nSubject: [NAS] prueba de diagnostico del supervisor\n\nPrueba manual.\n' "$DEST" "$DEST" \
  | /usr/sbin/sendmail -v -t 2>&1 | sed -E 's/[A-Za-z0-9+\/=_-]{24,}/<oculto>/g' | tail -15; echo "rc=${PIPESTATUS[1]}"
```

`DEST` es la dirección de `SUPERVISOR_EMAIL` del script (línea 42). Ejecútalo con `bash`. El `sed` oculta cualquier cadena larga, porque el modo detallado puede imprimir los tokens.

Repite la prueba con el entorno vacío, que es como corre bajo cron: antepón `env -i /bin/sh -c '…'` al mismo comando.

### A2. 🔒 Que el script diga la verdad

En `scripts/nas/supervisor_contenedores.sh`, función `enviar`, sustituye el bloque de `sendmail` (líneas 101 a 103) por:

```sh
  if [ -x /usr/sbin/ssmtp ]; then  # el sendmail del QTS es ssmtp; el de busybox (alpine) NO sirve
    error_sendmail=$(printf '%s\n' "$mensaje" | /usr/sbin/sendmail -t 2>&1) \
      && echo "$fecha ENVIADO (sendmail): $asunto" >> "$LOG" && return
    echo "$fecha NO ENVIADO (sendmail: $(printf '%s' "${error_sendmail:-falló sin mensaje}" | head -1 | cut -c1-200)): $asunto" >> "$LOG"
  fi
```

Va en el mismo PR que los cambios de la fase 3 del plan de traslado y se sube al NAS en su paso 4.1. Comprueba con `sh -n`.

### A3. Según el resultado de A1

| Resultado | Qué significa | Qué hacer |
|---|---|---|
| `rc=0` en las dos pruebas y el correo llega | El envío funciona hoy; el fallo es intermitente o ya no ocurre | Aplica A2 y pasa a A4: el registro dirá el motivo la próxima vez |
| Error de autenticación (`535`, `invalid_grant`, `Authorization failed`) | La autorización de Gmail del QTS está caducada o revocada | Del propietario: volver a autorizar la cuenta de correo en el Centro de notificaciones del QTS. Después repite A1 |
| `rc=0` a mano y error con `env -i` | Falta algo del entorno bajo cron | Informa con la salida literal; no cambies el crontab por tu cuenta |
| Cualquier otro error | Sin hipótesis | Regla 2 |

### A4. Comprobar de extremo a extremo

El supervisor está hoy en `mal` por `portfolio_redis`. Cuando la fase 4 del plan de traslado instale el script corregido, pasará a `ok` e intentará el aviso «recuperado» desde el cron real.

**Hecho cuando** `grep -c "ENVIADO (sendmail)" $S/supervisor/supervisor.log` da al menos 1 y el propietario confirma que recibió ese correo. Si la fase 4 ya pasó, provoca el aviso con un directorio de estado aparte, para no chocar con el guard de 240 segundos:

```bash
T=$(mktemp -d); SUPERVISOR_DIR=$T EXTRA_ESPERADOS=contenedor-inexistente /bin/sh $S/supervisor_contenedores.sh; cat $T/supervisor.log | cut -c1-200
```

## B. Realinear `swissjob-redis` con su compose

### Lo que se sabe

- El contenedor se creó el 22 de noviembre con `--maxmemory 512mb`. El compose dice `200mb` desde el 24, pero el contenedor no se recreó: el valor vivo es 200 MB porque se cambió en caliente.
- El límite de memoria del contenedor es de 256 MB. **En cualquier reinicio, Redis volverá a 512 MB**, por encima de su límite: al llenarse, el kernel lo mataría en vez de devolver el error de `noeviction`. Hoy usa 8,5 MB, así que no es inminente.
- Esa diferencia es la causa de que su hash de Compose no coincida con el desplegado.
- Clientes conectados: `swissjob-backend`, `swissjob-worker`, `swissjob-erasure-cdc`, `portfolio_backend` y `portfolio_worker`. Guarda en disco con `--save 60 1`, en un directorio del SSD. IP dinámica.

La corrección es recrear el contenedor desde el compose, sin editar ningún fichero. Se hace dentro de la ventana de la fase 6 del plan de traslado, porque tres de sus cinco clientes ya están parados en ese momento.

### B1. Antes de la ventana (solo lectura)

```bash
[ "$($D inspect -f '{{index .Config.Cmd 8}}' swissjob-redis)" = "--requirepass" ] && echo "INDICE OK"
P=$($D inspect -f '{{index .Config.Cmd 9}}' swissjob-redis)        # no lo imprimas
$D exec -e REDISCLI_AUTH="$P" swissjob-redis sh -c 'redis-cli config get maxmemory | tr "\n" " "; for d in 0 1 2 3; do echo "db$d $(redis-cli -n $d dbsize)"; done'
```

Esperado: `INDICE OK` y `maxmemory 209715200`. Anota los cuatro tamaños.

### B2. 🔒 En la ventana, entre los pasos 6.4 y 6.5 del plan de traslado

```bash
$D stop -t 60 portfolio_worker portfolio_backend
$D exec -e REDISCLI_AUTH="$P" swissjob-redis redis-cli save
cd $A
$C -p swissjob -f docker-compose.yml -f docker-compose.resource.yml stop -t 60 redis
$C -p swissjob -f docker-compose.yml -f docker-compose.resource.yml up -d --no-deps redis
```

Solo debe recrearse `swissjob-redis`.

### B3. Comprobar

```bash
$D inspect -f '{{.State.Status}} {{.State.Health.Status}} {{index .Config.Cmd 5}}' swissjob-redis
$D inspect -f '{{range .Mounts}}{{.Source}}{{end}}' swissjob-redis
P=$($D inspect -f '{{index .Config.Cmd 9}}' swissjob-redis)
$D exec -e REDISCLI_AUTH="$P" swissjob-redis sh -c 'for d in 0 1 2 3; do echo "db$d $(redis-cli -n $d dbsize)"; done'
d=$($D inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' swissjob-redis)
c=$($C -p swissjob -f $A/docker-compose.yml -f $A/docker-compose.resource.yml config --hash redis | awk '{print $2}')
[ "$d" = "$c" ] && echo "redis OK"
```

Esperado: `running healthy 200mb`; el origen `/share/external/DEV3301_1/apps-ssd/redis-legacy`; `redis OK`; y tamaños iguales a los de B1 salvo las claves con caducidad. Una base que pase de tener claves a 0 es motivo de parada.

Después sigue con el paso 6.5 del plan de traslado y arranca además Portfolio:

```bash
$D start portfolio_backend portfolio_worker
for c in swissjob-backend swissjob-worker swissjob-erasure-cdc portfolio_backend portfolio_worker; do
  echo "$c $($D logs --since 3m $c 2>&1 | grep -ciE 'NOAUTH|WRONGPASS|redis.*(refused|timeout)')"
done
```

**Hecho cuando** los cinco contadores son 0 y el paso 6.6 del plan de traslado pasa.

**Reversión:** no se edita ningún fichero, así que no hay nada que restaurar. Si el contenedor nuevo no arranca, lee `$D inspect -f '{{.State.Error}}' swissjob-redis` y aplica la regla 2; los datos siguen en el directorio del SSD.

**Rotación.** Esta ventana para a los cinco clientes de Redis, que es justo lo que necesita la variante «parada coordinada» de la propuesta de rotación. Pregunta al propietario, antes de fijar la hora, si quiere preparar la rotación para hacerla en la misma ventana.

## C. Sacar de `Public` el directorio de manifiestos

### Lo que se sabe

- `/share/CACHEDEV1_DATA/Public/unification-e15-20260914` contiene los cuatro composes vivos (`core`, `swissjob`, `portfolio`, `erasure`), cuatro `.env.nas.*` y las copias `.before-*`: 23 ficheros, 164 KB. Varios llevan secretos en claro. El directorio tiene permisos `777`; los ficheros con secretos, `600`.
- Lo nombran: `E=` en los dos scripts de despliegue, `ReactPortfolio/backend/scripts/sync_nas_compose.py` (línea 42) y las etiquetas de diez contenedores. Nada más en el NAS: ni el crontab ni el compose de Container Station.
- Los diez servicios reproducen hoy su hash desplegado. Para nueve se comprobó que el hash no depende del directorio del proyecto. El décimo, `erasure-cdc`, usa un `env_file` relativo (`.env.nas.erasure`) y no se pudo comprobar sin mover el fichero; la reconciliación del 22 de noviembre mostró que la ruta del `env_file` no entra en el hash, así que se espera que tampoco cambie.
- Ningún contenedor monta nada de ese directorio. Mover los ficheros no obliga a recrear ningún contenedor. Las etiquetas `config_files` y `working_dir` conservarán la ruta antigua hasta el siguiente despliegue de cada servicio; son informativas.

Destino: `M=$N/compose`, es decir `/share/external/DEV3301_1/apps-ssd/swissjob-deploy/compose`, con permisos `700`.

### C1. 🔒 Cambios en los repositorios

Van en los mismos PR que la fase 3 del plan de traslado. Sustituye `/share/CACHEDEV1_DATA/Public/unification-e15-20260914` por `/share/external/DEV3301_1/apps-ssd/swissjob-deploy/compose` en:

- `SwissJob/scripts/deploy_nas.sh`, línea 82.
- `ReactPortfolio/backend/scripts/deploy_nas.sh`, líneas 11 y 31.
- `ReactPortfolio/backend/scripts/sync_nas_compose.py`, línea 42.

### C2. 🔒 Copiar y validar

Hazlo cuando los PR estén fusionados y justo antes de C3, porque cada despliegue con el script antiguo modifica el directorio viejo y dejaría la copia desfasada. Comprueba primero que no hay un despliegue en curso.

```bash
M=$N/compose
umask 077; mkdir $M
find $U -maxdepth 1 -type f | wc -l
cp -p $U/.env.nas.* $U/*.yml $U/*.yml.* $M/ 2>&1
for f in $(ls -A $U); do cmp -s $U/$f $M/$f && echo IGUAL || echo "DISTINTO $f"; done | sort | uniq -c
cd $M
for p in "swissjob-r5 core.configured.yml core-api swissjob-core-api-r5" "swissjob-r5 core.configured.yml core-worker swissjob-core-worker-r5" \
  "swissjob-r5 core.configured.yml core-ops-worker swissjob-core-ops-worker-r5" "swissjob-r5 core.configured.yml core-harvest-worker swissjob-core-harvest-worker-r5" \
  "swissjob swissjob.configured.yml backend swissjob-backend" "swissjob swissjob.configured.yml worker swissjob-worker" \
  "swissjob swissjob.configured.yml frontend swissjob-frontend" "portfolio portfolio.configured.yml backend portfolio_backend" \
  "portfolio portfolio.configured.yml worker portfolio_worker" "swissjob-erasure erasure.configured.yml erasure-cdc swissjob-erasure-cdc"; do
  set -- $p
  d=$($D inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' $4)
  c=$($C -p $1 -f $M/$2 config --hash $3 | awk '{print $2}')
  [ "$d" = "$c" ] && echo "$4 OK" || echo "$4 DISTINTO"
done
```

Esperado: una sola línea `IGUAL` con el mismo número que imprimió `find`, y diez líneas `OK`. Un `DISTINTO` significa que desde la ubicación nueva se recrearía ese servicio en su siguiente despliegue: regla 2.

### C3. 🔒 Retirar el directorio antiguo

En el mismo momento que la fase 7 del plan de traslado:

```bash
mv /share/CACHEDEV1_DATA/Public/unification-e15-20260914 /share/CACHEDEV1_DATA/Public/unification-e15-20260914.retirar-$F
```

Durante las 24 horas siguientes vale la misma vigilancia que en la fase 7. A partir de aquí, las comprobaciones del plan de traslado que usan `$U` deben usar `$M`.

**Reversión:** el `mv` inverso, y revertir los PR si ya hubo un despliegue desde `$M`. En ese caso copia antes de `$M` al directorio antiguo el compose que ese despliegue haya modificado.

### C4. 🔒 Borrado definitivo

Con la fase 8 del plan de traslado. Enseña la lista con `find` y borra por nombre:

```bash
R=/share/CACHEDEV1_DATA/Public/unification-e15-20260914.retirar-$F
find $R -maxdepth 1 -type f | sort
for f in $(ls -A $R); do cmp -s $R/$f $M/$f || echo "SOLO EN EL ANTIGUO O DISTINTO: $f"; done
```

Si el segundo comando imprime algo que no sea un compose modificado por un despliegue posterior, regla 2. Después `rm -f` de cada fichero listado y `rmdir $R`.

### C5. 🔒 Un directorio vacío de más

`/share/CACHEDEV1_DATA/Public/portfolio-release-e6.XXCDe1Ax` contiene solo un subdirectorio `huggingface` vacío, de `admin`, del 15 de octubre. Ningún contenedor lo monta. Tiene el aspecto de un directorio fabricado por el daemon para un montaje que ya no existe.

```bash
for c in $($D ps -aq); do $D inspect -f '{{range .Mounts}}{{.Source}} {{end}}' $c; done | grep -c "portfolio-release-e6"   # 0
find /share/CACHEDEV1_DATA/Public/portfolio-release-e6.XXCDe1Ax -type f | wc -l                                             # 0
$D run --rm -v /share/CACHEDEV1_DATA/Public:/host redis:7-alpine rmdir /host/portfolio-release-e6.XXCDe1Ax/huggingface /host/portfolio-release-e6.XXCDe1Ax
```

### C6. Documentación

Añade a la tabla de la fase 9 del plan de traslado: la ruta de manifiestos en `SwissJob/CLAUDE.md`, `SwissJob/deploy/nas/README.md`, las cabeceras de `SwissJob/deploy/nas/*.configured.yml`, `ReactPortfolio/backend/deploy/nas/portfolio.configured.yml` (se regenera con su script) y la fila «Directorio de manifiestos» de `ReactPortfolio/ops/nas-saturacion/RUNBOOK.md`.

## D. El verificador de secretos y la copia versionada

### Lo que se sabe

- **`scripts/check_no_secrets.py` no ve un literal que sea un elemento de lista YAML.** Probado con un fichero sintético: un literal de 32 caracteres bajo `command:` y otro bajo `healthcheck.test:` dan «0 hallazgos» y código 0. Sus reglas de YAML exigen la forma `clave: valor`.
- Esa es exactamente la forma en que el compose de Container Station lleva la contraseña de Redis, dos veces. Un `cp` de ese fichero a `deploy/nas/` pasaría el CI con la contraseña dentro.
- La función `redactar` de `ReactPortfolio/backend/scripts/sync_nas_compose.py` tiene la misma ceguera: sustituye tres claves de ese fichero y deja los dos elementos de lista.
- `deploy/nas/containerstation-swissjob.yml` difiere del NAS en siete bloques: el `env_file` de `postgres`, los dos montajes del SSD con `create_host_path: false`, y cuatro diferencias en las definiciones de `backend` y `worker` (volumen `hfcache`, cola `scraping`). Estas últimas son definiciones muertas: esos dos servicios se despliegan desde `swissjob.configured.yml`.

### D1. 🔒 Cerrar la ceguera del verificador

En `SwissJob/scripts/check_no_secrets.py`, función `revisar`, añade una tercera regla para ficheros YAML: un elemento de lista (`- valor`) cuyo valor, sin comillas, no sea una variable `${…}` y cumpla `parece_credencial(valor)`, que es la función que el script ya usa para la sintaxis `.env`. Lee esa función antes de reutilizarla; si su criterio no encaja con un valor suelto, aplica la regla 2 en vez de escribir otro criterio.

Casos en `scripts/test_check_no_secrets.py`:

| Entrada | Esperado |
|---|---|
| `    - Zx9Kq2Lm8Pw4Rt6Yv1Bn3Cd5Fg7Hj0As` bajo `command:` | hallazgo |
| `    - ${REDIS_PASSWORD}` | limpio |
| `    - --maxmemory-policy`, `    - swissjob-net`, `    - default,ai` | limpio |
| `    - /share/external/DEV3301_1/apps-ssd/swissjob-deploy/docker/postgres/init-pgvector.sql:/docker-entrypoint-initdb.d/init-pgvector.sql:ro` | limpio |

Comprueba después que nada de lo ya versionado se rompe:

```bash
cd /home/lothar/Public/SwissJob
.venv/bin/python -m pytest scripts/test_check_no_secrets.py -q
python3 scripts/check_no_secrets.py deploy/nas; echo "rc=$?"                                              # 0
python3 scripts/check_no_secrets.py ../ReactPortfolio/backend/deploy/nas/portfolio.configured.yml; echo "rc=$?"   # 0
```

Un hallazgo nuevo en esos ficheros puede ser un secreto real ya versionado: no lo «arregles» relajando la regla; informa con la ruta y el número de línea, sin el valor.

**Hecho cuando** el caso positivo falla sin la regla y pasa con ella, y los dos códigos son 0.

### D2. 🔒 Regenerar la copia versionada

Después de la fase 6 del plan de traslado, para que recoja las rutas definitivas, y después de D1, para que el verificador pueda fallar. En local, en memoria:

```bash
cd /home/lothar/Public/SwissJob
ssh -o ConnectTimeout=60 nas 'cat /share/CACHEDEV1_DATA/.qpkg/container-station/data/application/swissjob/docker-compose.yml' | python3 -c '
import importlib.util, pathlib, re, subprocess, sys, tempfile
spec = importlib.util.spec_from_file_location("s", "/home/lothar/Public/ReactPortfolio/backend/scripts/sync_nas_compose.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
texto, claves = m.redactar(sys.stdin.read())
texto, n = re.subn(r"(?m)^(\s*)- [A-Za-z0-9]{24,}\s*$", r"\1- ${REDIS_PASSWORD}", texto)
assert n == 2, f"se esperaban 2 literales de lista, hay {n}"
destino = pathlib.Path("deploy/nas/containerstation-swissjob.yml")
cabecera = "".join(l for l in destino.read_text().splitlines(keepends=True) if l.startswith("#"))
with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as t:
    t.write(cabecera + texto)
r = subprocess.run([sys.executable, "scripts/check_no_secrets.py", t.name], capture_output=True, text=True)
print(r.stdout.strip()); assert r.returncode == 0, "el verificador rechaza el candidato: no se escribe nada"
destino.write_text(pathlib.Path(t.name).read_text()); pathlib.Path(t.name).unlink()
print("claves sustituidas:", sorted(set(claves)), "+ 2 de lista")
'
git diff --stat deploy/nas/containerstation-swissjob.yml
diff <(ssh nas 'cat /share/CACHEDEV1_DATA/.qpkg/container-station/data/application/swissjob/docker-compose.resource.yml') deploy/nas/containerstation-swissjob.resource.yml | grep -c '^[<>]'
```

Actualiza a mano la fecha de la cabecera del fichero. Revisa el `git diff` completo antes de enseñarlo: debe contener los siete bloques descritos arriba más las rutas nuevas del plan de traslado, y ninguna cadena larga que no sea una ruta o una variable. El último comando compara el fichero de recursos, que no lleva secretos; si no da 0, cópialo tal cual.

**Hecho cuando** `python3 scripts/check_no_secrets.py deploy/nas` da código 0 y el propietario ha visto el diff.

### D3. Para decidir el propietario, sin plan

El compose de Container Station conserva las definiciones de `backend`, `worker` y `frontend` con imágenes `:prod` de hace dos meses. No las usa nadie y son el motivo de la regla 6. Quitarlas eliminaría ese riesgo, pero cambia un fichero que Container Station también gestiona desde su interfaz. No lo hagas sin una instrucción expresa.

## Informe final

Como el del plan de traslado, y además:

1. La causa del fallo de correo si A1 la identificó, o la salida literal si no.
2. Los tamaños de las bases de Redis antes y después de B.
3. Los hallazgos nuevos del verificador tras D1, si los hubo, con ruta y línea.
4. Lo que sigue pendiente y es del propietario: la rotación de la contraseña de Redis, la reautorización de Gmail si procede y la decisión de D3.
