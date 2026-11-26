# Plan: retirar `/share/Public/swissjob` del NAS y llevar su contenido al SSD

> **Ejecutado el 25 y 26-11-2026; no repetir.** Fases 0 a 7 y 9 hechas (PR #52); el crontab y el borrado definitivo los hizo el propietario el 26-11. La fase 6 duró unos 6 minutos. Difiere de lo escrito: el compose de Container Station quedó solo con `postgres` y `redis`. Estado vigente: `docs/unificacion/ESTADO_Y_HOJA_DE_RUTA.md` §49.

Para el agente que ejecuta el traslado. La parte 1 explica qué es ese directorio y qué sobra; la parte 2 dice qué hacer, en qué orden y cómo saber que está hecho. Todo lo afirmado aquí se midió en el NAS el 25 de noviembre de 2026, en solo lectura; no se cambió nada.

Ejecuta las fases en orden. Las fases 0 a 5 no interrumpen el servicio. La fase 6 necesita una ventana de unos 5 minutos con SwissJob parado. Las fases 7 y 8 borran lo antiguo y van separadas por 24 horas.

## Reglas

1. **Puerta de aprobación.** Los pasos marcados con 🔒 cambian producción, borran ficheros o tocan un repositorio. Antes de cada uno, muestra al propietario el comando o el diff exacto y espera su «sí» en ese mismo turno. Una aprobación vale para ese paso, una vez.
2. **Parada.** Si un comando falla, si una comprobación da un valor distinto del esperado o si la realidad difiere de la tabla «Hechos verificados», detente, aplica la reversión de ese paso si ya cambiaste algo e informa con la salida literal. Continúa solo con instrucciones nuevas.
3. **Secretos.** Los ficheros `.env*` y los composes del NAS contienen contraseñas. Trabaja con ellos con `grep -n` de una ruta concreta, `md5sum`, `diff` contado o los fragmentos de este plan. Su contenido completo se queda fuera de la conversación y de cualquier fichero que escribas.
4. **Copia antes de editar.** Todo fichero del NAS que modifiques se copia antes con el sufijo `.before-<motivo>-<AAAAMMDD>` en su mismo directorio.
5. **Borrado.** Se borra con `rm -f` sobre nombres concretos y `rmdir` sobre directorios. `rmdir` falla si queda algo dentro, y ese fallo es una señal de parada. No uses `rm -r`.
6. **Compose.** Nombra siempre el servicio y usa `--no-deps`. Nunca ejecutes un `up` sin servicio ni `--remove-orphans`: el compose de Container Station define también `backend`, `worker` y `frontend` con imágenes `:prod` antiguas, y un `up` global los sustituiría por ellas.
7. **Alcance.** Toca solo lo que nombra el paso. El directorio de manifiestos `unification-e15-20260914`, que también está en `Public`, queda fuera de este plan.
8. **Idioma y commits.** Respuestas y documentación en español. Los commits y los PR los pide el propietario; prepara los cambios y espera.

## Parte 1. Estudio

### Qué es el directorio

`/share/Public/swissjob` es el directorio de trabajo del despliegue. Nació con el primer despliegue por Container Station, que exige rutas absolutas, y se eligió `Public` porque es la carpeta compartida en la que el usuario de SSH puede escribir. `/share/Public` es un enlace a `/share/CACHEDEV1_DATA/Public`, en el disco interno. El directorio tiene permisos `700` del usuario `Ricardo`.

No contiene datos de aplicación. Los datos de PostgreSQL y Redis ya están en el SSD desde el 22 de noviembre.

Lo usan dos proyectos: SwissJob y Portfolio. El despliegue de Portfolio deja ahí sus imágenes (`pf-*.tar.gz`) y usa el mismo binario de Compose.

### Quién depende de él hoy

| Elemento | Quién lo usa | Cuándo se lee |
|---|---|---|
| `docker/postgres/init-pgvector.sql` | Montaje de `swissjob-postgres` (línea 14 del compose de Container Station) | En cada arranque del contenedor. Si falta, Docker crea un directorio en su lugar y PostgreSQL no arranca (incidente del 21-10-2026) |
| `.env.postgres.runtime` | `env_file` del servicio `postgres` (línea 7 del mismo compose) | Solo cuando Compose crea o valida el servicio |
| `.env.prod` | `env_file` de `backend` y `worker` en ese compose (líneas 71 y 97) y el paso de limpieza de credenciales de `scripts/deploy_nas.sh` | Compose lo exige para poder leer el fichero, aunque esos dos servicios se despliegan desde otro compose |
| `.env.core.prod`, `.env.core.admin.prod` | `scripts/deploy_nas.sh core`, al migrar el núcleo | En cada despliegue del núcleo |
| `bin-docker-compose` (v5.6.0) | Los dos scripts de despliegue y cualquier operación manual | `docker compose` no funciona por SSH en este NAS |
| `supervisor_contenedores.sh`, `supervisor/` | Crontab del QTS, línea 32, cada 5 minutos | Script, estado, registro y marca de última ejecución |
| `*.tar.gz` | Destino del `scp` de los dos scripts de despliegue, antes del `docker load` | Solo durante el despliegue |

El único contenedor que monta algo del directorio es `swissjob-postgres`. Los composes de `unification-e15-20260914` no lo referencian.

### Qué sobra

49 ficheros, unos 10,5 GB:

- **43 imágenes comprimidas** (`bff-`, `core-`, `frontend-`, `pf-`, `worker-` seguidos de un SHA de 7 caracteres y `.tar.gz`). Una vez cargadas con `docker load` no las lee nadie. Los scripts de despliegue no las borran, y por eso se acumulan desde el 15 de octubre. Las imágenes que corren hoy, y la mayoría de las anteriores, siguen cargadas en Docker. Nueve tarballs del 15 al 18 de octubre son la única copia de su imagen (`bff-5002445`, `bff-8906b89`, `bff-a2ba944`, `bff-ae3cb02`, `worker-5002445`, `worker-ae3cb02`, `core-239a737`, `core-5b3bf3d`, `core-acf48b2`); ninguna corre y todas se pueden reconstruir desde git.
- **4 scripts de medición sueltos** del 21 de octubre: `cola.sql`, `medir.py`, `cronometrar.py`, `tamano.py`.
- **2 copias de secretos anteriores a rotaciones**: `.env.core.admin.prod.before-admin-auth-20261122` y `.env.prod.before-20261110-rotacion`.

### Destino

En el SSD el proyecto ya tiene `pgdata-swissjob` en la raíz y, dentro de `apps-ssd/` (permisos `700` de `Ricardo`), `core-hf-cache`, `portfolio-postgres`, `redis-core` y `redis-legacy`. El directorio de despliegue va junto a ellos:

```
/share/external/DEV3301_1/apps-ssd/swissjob-deploy/        700
├── .env.prod  .env.postgres.runtime  .env.core.prod  .env.core.admin.prod   600
├── bin-docker-compose                                     700
└── docker/postgres/init-pgvector.sql                      644 (directorios 755)
```

Se conservan los nombres de los ficheros para que el cambio sea una sustitución de prefijo.

### Tres decisiones ya tomadas en este plan

1. **El supervisor no va al SSD; va a `/share/homes/Ricardo/swissjob-supervisor`, en el disco interno.** El supervisor existe para avisar cuando los contenedores caen. El SSD es un disco USB del que dependen PostgreSQL y los dos Redis: si se desconecta, caen todos los contenedores, y un supervisor alojado en ese mismo disco tampoco se ejecutaría. Sale de `Public`, que es el objetivo, sin crear ese punto ciego. Si el propietario lo prefiere en el SSD, usa `$N/supervisor-host` como valor de `S` en todos los pasos; el resto no cambia.
2. **Los scripts de despliegue borran el tarball después de cargarlo.** La imagen cargada en Docker y la copia `.before-*` del compose son la vía de vuelta atrás; el tarball no aporta nada más.
3. **El montaje de `init-pgvector.sql` pasa a la sintaxis larga con `create_host_path: false`**, igual que el de PGDATA. Con ella, si el fichero falta, la creación del contenedor falla en el momento en vez de fabricar un directorio y fallar en el siguiente arranque.

### Hallazgos al margen

- El supervisor está en estado `mal` desde el 25 de noviembre porque espera `portfolio_redis`, borrado ese día. La fase 3 lo quita de la lista.
- El registro del supervisor dice `NO ENVIADO (sin sendmail ni SMTP_HOST)` en sus últimos avisos: hoy no manda correo. Este plan no lo arregla; queda para el propietario.
- `deploy/nas/containerstation-swissjob.yml` del repo está desfasado respecto al NAS: no recoge los montajes del SSD ni `.env.postgres.runtime`.
- El hash de Compose de `swissjob-redis` no coincide con el que calcula su fichero. Es anterior a este plan y es otro motivo para no tocar ese servicio.

## Parte 2. Ejecución

### Hechos verificados (2026-11-25)

Compruébalos en la fase 0 antes de usarlos.

| Hecho | Valor |
|---|---|
| Acceso | `ssh nas`, usuario `Ricardo`. Sin `sudo` sin contraseña. Usa `-o ConnectTimeout=60` |
| Herramientas del NAS | Hay `bash`, `mktemp`, `diff`, `md5sum`, `rsync`, `cmp`. No hay `python3`: lo que necesite Python se edita en local y se sube |
| Docker | `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker` |
| SSD | `KINGSTON SA400S37480G`, ext4, montado en `/share/external/DEV3301_1`, 380 GB libres. La letra del dispositivo cambia entre reinicios (`sde1`, `sdf1`); la ruta de montaje se mantuvo |
| Compose de Container Station | `/share/CACHEDEV1_DATA/.qpkg/container-station/data/application/swissjob/docker-compose.yml` más `docker-compose.resource.yml`; proyecto `swissjob` |
| Referencias a la ruta antigua en ese compose | 4: línea 7 (`.env.postgres.runtime`), línea 14 (`init-pgvector.sql`), líneas 71 y 97 (`.env.prod`) |
| Hash de Compose | `postgres` coincide con lo desplegado; `redis` no |
| `swissjob-postgres` | Señal de parada `SIGINT`, IP dinámica, sin ranuras de replicación |
| Clientes de `swissjob-postgres` | `swissjob-backend`, `swissjob-worker`, `swissjob-erasure-cdc` y los cuatro `swissjob-core-*-r5`. Portfolio no se conecta a él |
| Crontab | `/etc/config/crontab`, de `admin`, línea 32; `crond` corre como `admin` |
| Copia desplegada del supervisor | Idéntica a `scripts/nas/supervisor_contenedores.sh` (md5 `c36ddfc3b796cdaea1b59f9488294ebb`) |
| `init-pgvector.sql` | md5 `59d394def094264f5d1445d8a0645b14`; su directorio `docker/` es de `admin`, creado por el daemon |

### Variables de sesión

Define esto al principio de cada sesión de SSH:

```bash
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
O=/share/Public/swissjob
N=/share/external/DEV3301_1/apps-ssd/swissjob-deploy
S=/share/homes/Ricardo/swissjob-supervisor
A=/share/CACHEDEV1_DATA/.qpkg/container-station/data/application/swissjob
U=/share/CACHEDEV1_DATA/Public/unification-e15-20260914
F=20261125                      # fecha de inicio; no la cambies entre fases
export DOCKER_HOST=unix:///var/run/docker.sock
export DOCKER_CONFIG=$(mktemp -d)   # evita 30 avisos de «permission denied» por comando
```

`C` es el binario de Compose: `$O/bin-docker-compose` hasta terminar la fase 2 y `$N/bin-docker-compose` después.

### Fase 0. Comprobación previa (solo lectura)

```bash
awk '$2=="/share/external/DEV3301_1" && $3=="ext4" {ok=1} END {exit !ok}' /proc/mounts && echo "SSD OK"
df -h /share/external/DEV3301_1 | tail -1
ls -ld /share/external/DEV3301_1/apps-ssd; test ! -e $N && echo "N libre"; test ! -e $S && echo "S libre"
ls -A $O
grep -n "Public/swissjob" $A/docker-compose.yml | cut -d: -f1 | tr '\n' ' '
grep -c "Public/swissjob" $U/*.configured.yml
grep -n "Public/swissjob" /etc/config/crontab
ls /etc/init.d/crond.sh
md5sum $O/supervisor_contenedores.sh $O/docker/postgres/init-pgvector.sql
$D ps -a --format '{{.Names}}|{{.State}}|{{.Status}}' | grep -E 'swissjob|portfolio' | sort > /tmp/estado-antes-$F.txt; cat /tmp/estado-antes-$F.txt
```

Esperado: `SSD OK`; `N libre` y `S libre`; líneas `7 14 71 97`; los cuatro contadores de `$U` a 0; `/etc/init.d/crond.sh` existe; la línea 32 del crontab; los dos md5 de la tabla; y 15 contenedores `running` de SwissJob y Portfolio.

**Hecho cuando** todo coincide y has guardado `/tmp/estado-antes-$F.txt`.

### Fase 1. 🔒 Borrar lo que sobra

Va antes de copiar para no llevar 10 GB al SSD.

1. Comprueba que no hay un despliegue en curso: `ps | grep -E '[g]unzip|[d]ocker.* load'` no imprime nada.
2. Lista lo que se va a borrar y enséñaselo al propietario:

```bash
cd $O
ls | grep -E '^(bff|core|frontend|pf|worker)-[0-9a-f]{7}\.tar\.gz$' | tee /tmp/tarballs-$F.txt | wc -l
ls -la cola.sql medir.py cronometrar.py tamano.py \
  .env.core.admin.prod.before-admin-auth-20261122 .env.prod.before-20261110-rotacion
```

El 25 de noviembre eran 43 tarballs. Si hay más es que hubo despliegues después: no es motivo de parada, la lista que apruebas es la que imprime el comando.

3. Tras el «sí»:

```bash
cd $O
while read f; do rm -f -- "$f"; done < /tmp/tarballs-$F.txt
rm -f cola.sql medir.py cronometrar.py tamano.py \
  .env.core.admin.prod.before-admin-auth-20261122 .env.prod.before-20261110-rotacion
ls -A $O
```

**Hecho cuando** `ls -A $O` imprime exactamente estas ocho entradas: `.env.core.admin.prod`, `.env.core.prod`, `.env.postgres.runtime`, `.env.prod`, `bin-docker-compose`, `docker`, `supervisor`, `supervisor_contenedores.sh`.

**Reversión:** no hay. Las imágenes en uso siguen cargadas en Docker.

### Fase 2. 🔒 Copiar al SSD

Crea ficheros nuevos y no cambia nada de lo que corre.

```bash
umask 077
mkdir $N && mkdir -p $N/docker/postgres && chmod 755 $N/docker $N/docker/postgres
cp -p $O/.env.prod $O/.env.postgres.runtime $O/.env.core.prod $O/.env.core.admin.prod $O/bin-docker-compose $N/
cp $O/docker/postgres/init-pgvector.sql $N/docker/postgres/ && chmod 644 $N/docker/postgres/init-pgvector.sql
for f in .env.prod .env.postgres.runtime .env.core.prod .env.core.admin.prod bin-docker-compose docker/postgres/init-pgvector.sql; do
  cmp -s $O/$f $N/$f && echo "$f IGUAL" || echo "$f DISTINTO"
done
ls -la $N $N/docker/postgres; $N/bin-docker-compose version
```

**Hecho cuando** las seis líneas dicen `IGUAL`, los cuatro `.env*` tienen modo `600`, el binario `700`, el `.sql` `644`, y el binario responde `v5.6.0`.

**Reversión:** borra los seis ficheros de `$N` por nombre y después `rmdir $N/docker/postgres $N/docker $N`.

### Fase 3. 🔒 Cambios en los repositorios (en local, sin commit)

Rama nueva en cada repositorio. Los scripts de despliegue solo publican el SHA exacto de `github/main`, así que estos cambios no surten efecto hasta que el propietario fusione los PR. Hasta entonces los despliegues siguen usando la ruta antigua, que por eso se conserva hasta la fase 7.

**3.1 `/home/lothar/Public/SwissJob/scripts/deploy_nas.sh`**

- Sustituye las 7 apariciones de `/share/Public/swissjob` por `/share/external/DEV3301_1/apps-ssd/swissjob-deploy` (líneas 81, 150, 151, 255, 258, 259 y 274).
- En la línea 151, borra el tarball tras cargarlo. Queda así:

```bash
  ssh nas "gunzip -c /share/external/DEV3301_1/apps-ssd/swissjob-deploy/$SERVICIO-$SHA.tar.gz | $D load && rm -f /share/external/DEV3301_1/apps-ssd/swissjob-deploy/$SERVICIO-$SHA.tar.gz"
```

**3.2 `/home/lothar/Public/ReactPortfolio/backend/scripts/deploy_nas.sh`** (repositorio git propio, raíz en `ReactPortfolio/backend`)

- Las mismas sustituciones en sus 3 apariciones (líneas 30, 44 y 45), con el mismo `&& rm -f` del tarball `pf-$SHA.tar.gz` en la línea 45.

**3.3 `/home/lothar/Public/SwissJob/scripts/nas/supervisor_contenedores.sh`**

- Línea 43: el valor por defecto de `DIR` pasa a `/share/homes/Ricardo/swissjob-supervisor/supervisor`.
- Línea 47: quita ` portfolio_redis` de `ESPERADOS`. Quedan 15 nombres.

**3.4 Comprobar**

```bash
cd /home/lothar/Public/SwissJob
bash -n scripts/deploy_nas.sh && sh -n scripts/nas/supervisor_contenedores.sh
grep -c "Public/swissjob" scripts/deploy_nas.sh scripts/nas/supervisor_contenedores.sh   # 0 y 0
scripts/deploy_nas.sh core --dry-run
bash -n ../ReactPortfolio/backend/scripts/deploy_nas.sh
grep -c "Public/swissjob" ../ReactPortfolio/backend/scripts/deploy_nas.sh                 # 0
```

`--dry-run` exige el subárbol `jobhunt_core` sin cambios y termina antes de tocar el NAS.

**Hecho cuando** los contadores dan 0, no hay errores de sintaxis y has enseñado al propietario el `git diff` de los tres ficheros.

### Fase 4. 🔒 Mover el supervisor

**4.1 Copiar** (en el NAS; el script nuevo se sube desde local):

```bash
umask 077
mkdir $S && cp -rp $O/supervisor $S/
```

```bash
# en local
scp -q -o ConnectTimeout=60 /home/lothar/Public/SwissJob/scripts/nas/supervisor_contenedores.sh nas:/share/homes/Ricardo/swissjob-supervisor/
ssh -o ConnectTimeout=60 nas 'chmod 700 /share/homes/Ricardo/swissjob-supervisor/supervisor_contenedores.sh; ls -la /share/homes/Ricardo/swissjob-supervisor /share/homes/Ricardo/swissjob-supervisor/supervisor'
```

**4.2 Cambiar el crontab.** Lo ejecuta el propietario, porque pide la contraseña de administrador. Entra con `ssh -t nas`, después `sudo -i`, y ejecuta:

```bash
cp -p /etc/config/crontab /etc/config/crontab.before-supervisor-20261125
sed -i 's#/share/Public/swissjob/supervisor_contenedores.sh#/share/homes/Ricardo/swissjob-supervisor/supervisor_contenedores.sh#' /etc/config/crontab
grep -n supervisor_contenedores /etc/config/crontab
crontab /etc/config/crontab && /etc/init.d/crond.sh restart
```

El `grep` debe mostrar una sola línea, con la ruta nueva. La recarga con `crontab` y `crond.sh restart` es el procedimiento habitual de QTS; no se ha probado en esta máquina.

**4.3 Comprobar**, pasados 6 minutos:

```bash
cat $S/supervisor/ultimo_run $O/supervisor/ultimo_run; date -u +%s
head -1 $S/supervisor/estado; tail -3 $S/supervisor/supervisor.log | cut -c1-200
```

**Hecho cuando** la marca de `$S` tiene menos de 300 segundos, la de `$O` ha dejado de avanzar y la primera línea de `estado` es `ok`. En el registro aparecerá un intento de aviso «recuperado»; si dice `NO ENVIADO`, es el hallazgo ya anotado y no es motivo de parada.

**Reversión:** el propietario restaura `/etc/config/crontab.before-supervisor-20261125` y repite la recarga.

### Fase 5. 🔒 Preparar el compose de Container Station (sin recrear nada)

**5.1 Candidato.** En local, con Python porque el NAS no lo tiene y porque `sed` no puede insertar las líneas nuevas allí:

```bash
T=$(mktemp -d); chmod 700 $T
A=/share/CACHEDEV1_DATA/.qpkg/container-station/data/application/swissjob
scp -q -o ConnectTimeout=60 nas:$A/docker-compose.yml $T/vivo.yml
python3 - $T/vivo.yml $T/candidato.yml <<'PY'
import sys
from pathlib import Path

OLD = "/share/Public/swissjob"
NEW = "/share/external/DEV3301_1/apps-ssd/swissjob-deploy"
texto = Path(sys.argv[1]).read_text()
init_viejo = f"    - {OLD}/docker/postgres/init-pgvector.sql:/docker-entrypoint-initdb.d/init-pgvector.sql:ro\n"
init_nuevo = (
    "    - type: bind\n"
    f"      source: {NEW}/docker/postgres/init-pgvector.sql\n"
    "      target: /docker-entrypoint-initdb.d/init-pgvector.sql\n"
    "      read_only: true\n"
    "      bind:\n"
    "        create_host_path: false\n"
)
assert texto.count(init_viejo) == 1, "montaje de init-pgvector no encontrado"
assert texto.count(f"    - {OLD}/.env.postgres.runtime\n") == 1, "env de postgres"
assert texto.count(f"    - {OLD}/.env.prod\n") == 2, "env de backend y worker"
assert texto.count(OLD) == 4, "hay referencias no previstas"
texto = texto.replace(init_viejo, init_nuevo).replace(OLD + "/", NEW + "/")
assert OLD not in texto and texto.count(NEW) == 4
Path(sys.argv[2]).write_text(texto)
print("candidato escrito")
PY
diff $T/vivo.yml $T/candidato.yml | grep -c '^[<>]'      # debe imprimir 13
diff $T/vivo.yml $T/candidato.yml                        # solo rutas: se puede mostrar
scp -q -o ConnectTimeout=60 $T/candidato.yml nas:$A/docker-compose.yml.next-deploy-dir-20261125
rm -f $T/vivo.yml $T/candidato.yml && rmdir $T
```

El `diff` cuenta 13 líneas: 4 que salen y 9 que entran. Si alguna línea del `diff` no es una ruta o una de las cinco claves del montaje, no la muestres y aplica la regla 2.

**5.2 Validar.** En el NAS, con `C=$N/bin-docker-compose`:

```bash
chmod 600 $A/docker-compose.yml.next-deploy-dir-$F
$C -p swissjob -f $A/docker-compose.yml.next-deploy-dir-$F -f $A/docker-compose.resource.yml config --quiet && echo "CANDIDATO VALIDO"
for s in postgres redis backend worker frontend; do
  a=$($C -p swissjob -f $A/docker-compose.yml -f $A/docker-compose.resource.yml config --hash $s | awk '{print $2}')
  b=$($C -p swissjob -f $A/docker-compose.yml.next-deploy-dir-$F -f $A/docker-compose.resource.yml config --hash $s | awk '{print $2}')
  [ "$a" = "$b" ] && echo "$s IGUAL" || echo "$s CAMBIA"
done
```

Esperado: `CANDIDATO VALIDO`; `postgres CAMBIA`; `redis IGUAL`; `frontend IGUAL`. `backend` y `worker` pueden dar cualquiera de los dos resultados, porque solo cambia la ruta de su `env_file` y esos dos servicios no se despliegan desde este fichero. Si `redis` o `frontend` dan `CAMBIA`, borra el candidato y aplica la regla 2.

**5.3 Promover.** Tras el «sí»:

```bash
cp -p $A/docker-compose.yml $A/docker-compose.yml.before-deploy-dir-$F
mv $A/docker-compose.yml.next-deploy-dir-$F $A/docker-compose.yml
grep -c "Public/swissjob" $A/docker-compose.yml       # 0
```

No se recrea nada. El contenedor de PostgreSQL sigue con su montaje antiguo, que continúa existiendo, de modo que un reinicio del NAS entre esta fase y la siguiente no rompe nada.

**Reversión:** `cp -p $A/docker-compose.yml.before-deploy-dir-$F $A/docker-compose.yml`.

### Fase 6. 🔒 Ventana: recrear `swissjob-postgres`

Es el único paso con corte de servicio: SwissJob y el feed que lee Portfolio quedan sin responder unos 5 minutos.

**Cuándo.** Fuera de las cosechas (01:30, 07:30, 13:30, 19:30, 09:30, 21:30 y 23:30 de Zúrich) y de las tareas diarias de 05:20 a 06:40. Deja al menos 45 minutos de margen después de cualquiera de esas horas. Acuerda la hora con el propietario.

**Copia de seguridad.** La recreación no toca PGDATA, que es un directorio del SSD montado con `create_host_path: false`; equivale a reiniciar PostgreSQL. Aun así, los únicos volcados que existen son del 22 de noviembre y no hay copia diaria programada: pregunta al propietario si quiere un volcado antes de la ventana.

**6.1 Comprobar justo antes:**

```bash
test -f $N/docker/postgres/init-pgvector.sql && test -f $N/.env.postgres.runtime && echo "ORIGENES OK"
awk '$2=="/share/external/DEV3301_1" {ok=1} END {exit !ok}' /proc/mounts && echo "SSD OK"
$D exec swissjob-postgres psql -h 127.0.0.1 -U swissjob -d postgres -Atc "select count(*) from pg_database" 
$D exec swissjob-postgres psql -h 127.0.0.1 -U swissjob -d postgres -Atc "select count(*) from pg_stat_activity where state='active' and query_start < now() - interval '2 minutes'"
```

Anota el número de bases. El último contador debe ser 0; si no, hay una consulta larga en curso: espera o consulta al propietario.

**6.2 Parar los clientes**, los trabajadores antes que las API:

```bash
$D stop -t 60 swissjob-core-harvest-worker-r5 swissjob-core-worker-r5 swissjob-core-ops-worker-r5 swissjob-worker swissjob-erasure-cdc
$D stop -t 30 swissjob-core-api-r5 swissjob-backend
```

**6.3 Recrear PostgreSQL:**

```bash
cd $A
$C -p swissjob -f docker-compose.yml -f docker-compose.resource.yml stop -t 120 postgres
$C -p swissjob -f docker-compose.yml -f docker-compose.resource.yml up -d --no-deps postgres
```

Solo debe recrearse `swissjob-postgres`. Compose avisará de contenedores huérfanos del proyecto `swissjob`: es lo esperado, porque el proyecto está repartido entre dos composes.

**6.4 Comprobar PostgreSQL**, hasta 2 minutos:

```bash
$D inspect -f '{{.State.Status}} {{.State.Health.Status}}' swissjob-postgres
$D inspect -f '{{range .Mounts}}{{.Source}}{{println}}{{end}}' swissjob-postgres
$D exec swissjob-postgres psql -h 127.0.0.1 -U swissjob -d postgres -Atc "select count(*) from pg_database"
```

Esperado: `running healthy`; dos orígenes, `/share/external/DEV3301_1/pgdata-swissjob` y `$N/docker/postgres/init-pgvector.sql`; y el mismo número de bases que en 6.1. Si el contenedor no arranca, lee `$D inspect -f '{{.State.Error}}' swissjob-postgres` antes que los registros y aplica la reversión.

**6.5 Arrancar los clientes** con `start`, que no recrea nada:

```bash
$D start swissjob-core-api-r5 swissjob-backend
# espera a que los dos estén healthy (hasta 5 min)
$D inspect -f '{{.Name}} {{.State.Health.Status}}' swissjob-core-api-r5 swissjob-backend
$D start swissjob-core-worker-r5 swissjob-core-harvest-worker-r5 swissjob-core-ops-worker-r5 swissjob-worker swissjob-erasure-cdc
```

**6.6 Comprobar el conjunto:**

```bash
$D ps -a --format '{{.Names}}|{{.State}}|{{.Status}}' | grep -E 'swissjob|portfolio' | sort | cut -d'|' -f1,2 > /tmp/estado-despues-$F.txt
cut -d'|' -f1,2 /tmp/estado-antes-$F.txt | diff - /tmp/estado-despues-$F.txt && echo "MISMOS CONTENEDORES"
$D exec swissjob-core-api-r5 python -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/v1/ready",timeout=5)); print(d.get("status"), d.get("authoritative"), d.get("release"))'
$D exec swissjob-worker celery -A celery_app inspect ping --timeout 5 | grep -c pong
$D logs --since 5m swissjob-core-api-r5 2>&1 | grep -ci traceback
```

**Hecho cuando** imprime `MISMOS CONTENEDORES`, `/v1/ready` devuelve `ready True` con la misma release que antes, el ping de Celery da al menos 1 y no hay ningún `Traceback` nuevo. El supervisor habrá anotado `MAL` durante la ventana y `ok` después; es lo esperado.

**Reversión:**

```bash
cp -p $A/docker-compose.yml.before-deploy-dir-$F $A/docker-compose.yml
cd $A && $O/bin-docker-compose -p swissjob -f docker-compose.yml -f docker-compose.resource.yml up -d --no-deps postgres
```

y después 6.4 a 6.6. Funciona porque la ruta antigua sigue intacta hasta la fase 7.

### Fase 7. 🔒 Retirar el directorio antiguo sin borrarlo

**Condiciones previas, todas:**

- Fase 6 terminada: `swissjob-postgres` monta el fichero de `$N`.
- Fase 4 terminada: el supervisor corre desde `$S`.
- Los PR de la fase 3 están fusionados en `github/main` en los dos repositorios. Si no lo están, un despliegue fallaría al no encontrar la ruta antigua: espera, o pide al propietario que confirme que no desplegará hasta fusionarlos.
- Ninguna referencia viva:

```bash
grep -c "Public/swissjob" $A/docker-compose.yml $A/docker-compose.resource.yml $U/*.configured.yml /etc/config/crontab
for c in $($D ps -aq); do $D inspect -f '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' $c; done | grep -c "Public/swissjob"
ls $O | grep -E '\.tar\.gz$'
```

Todos los contadores a 0 y ningún tarball. Un tarball nuevo significa que alguien desplegó con el script antiguo: bórralo por nombre con aprobación y revisa la tercera condición.

Tras el «sí»:

```bash
mv /share/Public/swissjob /share/Public/swissjob.retirar-$F
```

Renombrar es la prueba real de que nada lo usa, y se deshace con un `mv`.

**Comprobar** durante 24 horas, incluida al menos una ventana de cosecha: el supervisor en `ok`, los 15 contenedores `running` y, si el propietario despliega algo en ese plazo, que el despliegue termine bien.

**Reversión:** `mv /share/Public/swissjob.retirar-$F /share/Public/swissjob`.

### Fase 8. 🔒 Borrado definitivo

Pasadas las 24 horas sin incidencias. Enseña antes la lista con `find /share/Public/swissjob.retirar-$F`; deben ser 10 ficheros y 4 directorios, contando el propio directorio raíz.

```bash
R=/share/Public/swissjob.retirar-$F
rm -f $R/.env.prod $R/.env.postgres.runtime $R/.env.core.prod $R/.env.core.admin.prod $R/bin-docker-compose \
  $R/supervisor_contenedores.sh $R/supervisor/estado $R/supervisor/supervisor.log $R/supervisor/ultimo_run
rmdir $R/supervisor
# docker/ es de admin (lo creó el daemon): se vacía desde un contenedor, con una imagen que ya está en el NAS
$D run --rm -v $R:/host redis:7-alpine sh -c 'rm -f /host/docker/postgres/init-pgvector.sql && rmdir /host/docker/postgres /host/docker'
rmdir $R
```

Si algún `rmdir` falla es que hay algo no previsto dentro: regla 2. Después borra también los temporales `/tmp/estado-antes-$F.txt`, `/tmp/estado-despues-$F.txt` y `/tmp/tarballs-$F.txt`.

**Hecho cuando** `ls -d /share/Public/swissjob*` no encuentra nada.

**Reversión:** no hay. Lo único que desaparece son copias de ficheros que viven en `$N` y `$S`.

### Fase 9. 🔒 Documentación

Sustituye la ruta antigua por la nueva solo en los documentos que describen el estado vigente. Los informes históricos, `docker-compose.qnap.yml` y `docker-compose.rehearsal.qnap.yml` se dejan como están.

| Fichero | Qué cambiar |
|---|---|
| `SwissJob/CLAUDE.md` | Las dos rutas del supervisor (líneas 275 y 293), la cifra «16 esperados», que pasa a 15, y un aviso de que el directorio de despliegue vive en `$N` |
| `SwissJob/INVARIANTES.md` | I-G4: ruta de `estado` y, de paso, que el supervisor va por cron y no en el contenedor `swissjob-supervisor` |
| `SwissJob/docs/DEPLOY_NAS.md` | La ruta del binario (línea 17) y la tabla 0.7 |
| `SwissJob/deploy/nas/containerstation-swissjob.yml` | Las 4 rutas, con el `env_file` de `postgres` apuntando a `.env.postgres.runtime` como en el NAS. Informa al propietario de que el resto del fichero sigue desfasado; no lo resincronices por tu cuenta. Ejecuta `python3 scripts/check_no_secrets.py deploy/nas` y espera código 0 |
| `SwissJob/deploy/nas/erasure.configured.yml` | El comentario de la línea 30 |
| `SwissJob/.env.core.prod.example`, `.env.core.admin.prod.example` | El comentario de la línea 2 |
| `ReactPortfolio/ops/nas-saturacion/RUNBOOK.md` | La ruta de `$C` (líneas 25 y 74) |

### Informe final

Termina con un mensaje al propietario que contenga, en este orden:

1. Qué se hizo y qué quedó sin hacer.
2. Duración real de la ventana de la fase 6.
3. Ficheros creados, modificados y borrados, incluidas las copias `.before-*` que quedan en el NAS (`docker-compose.yml.before-deploy-dir-20261125` y `crontab.before-supervisor-20261125`).
4. Cualquier cosa inesperada, con su salida literal.
5. Lo que sigue pendiente y no era de este plan: el correo del supervisor, la resincronización de `deploy/nas/containerstation-swissjob.yml` y el directorio `unification-e15-20260914`, que sigue en `Public`.
