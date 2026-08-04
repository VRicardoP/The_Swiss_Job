# DEPLOY_NAS — Sombra (Fase B) en producción QNAP

> Runbook de DESPLIEGUE de la sombra en el NAS (Container Station). Autorización
> del propietario registrada **2026-07-28**: la sombra pasa a producción porque el
> equipo local no está 24h. El contrato **§0 sigue vigente**: CERO efectos
> visibles a usuarios — el outbox del core solo entrega al inbox sombra
> (`jobhunt.shadow_inbox`), nunca a usuarios reales.
>
> Complementa (no sustituye): la OPERACIÓN diaria de la sombra es
> [RUNBOOK.md](RUNBOOK.md); los gotchas del NAS y el despliegue del legacy son
> [docs/DEPLOY_NAS.md](../../docs/DEPLOY_NAS.md) (secciones 0 y 3 — releer la 0
> antes de tocar nada).

---

## Índice

- [0. Decisiones que el propietario debe confirmar ANTES de ejecutar](#0-decisiones-que-el-propietario-debe-confirmar-antes-de-ejecutar)
- [1. Prerrequisitos](#1-prerrequisitos)
- [2. Build local de las 2 imágenes nuevas](#2-build-local-de-las-2-imágenes-nuevas)
- [3. Secretos: ficheros .env.core.*.prod](#3-secretos-ficheros-envcoreprod)
- [4. Transferencia al NAS (save → scp → load)](#4-transferencia-al-nas-save--scp--load)
- [5. VENTANA DE MANTENIMIENTO — reinicio del Postgres de producción](#5-ventana-de-mantenimiento--reinicio-del-postgres-de-producción)
- [6. Orden de arranque y qué verificar en cada paso](#6-orden-de-arranque-y-qué-verificar-en-cada-paso)
- [7. Bootstrap del modelo y la política (receta v2)](#7-bootstrap-del-modelo-y-la-política-receta-v2)
- [8. ORÁCULO sobre los datos reales del NAS](#8-oráculo-sobre-los-datos-reales-del-nas)
- [9. Verificación post-despliegue — checklist tipo revisor](#9-verificación-post-despliegue--checklist-tipo-revisor)
- [10. Rollback completo](#10-rollback-completo)
- [Apéndice A. Ejecutar comandos del core en el NAS (sin docker compose)](#apéndice-a-ejecutar-comandos-del-core-en-el-nas-sin-docker-compose)

---

## 0. Decisiones que el propietario debe confirmar ANTES de ejecutar

| # | Decisión tomada al preparar el paquete | Confirmar |
|---|---|---|
| 1 | **Arquitectura del NAS: linux/amd64.** No consta explícitamente en docs, pero las imágenes actuales (backend/frontend) se construyen en la máquina de dev (x86_64) y corren en el NAS vía `docker load` → evidencia operativa de amd64. Verificación definitiva: `ssh Ricardo@capsule uname -m` → esperado `x86_64`. Si diera ARM, TODO el flujo de tars cambia (`docker buildx --platform linux/arm64`). | ☐ |
| 2 | **Compose del NAS: `docker-compose.qnap.yml`** (el canónico según docs/DEPLOY_NAS.md §1). `docker-compose.prod.yml` se ha alineado también (es la fuente de build de las imágenes `:prod`). `docker-compose.prebuilt.yml` NO se ha tocado: no tiene servicios de la sombra — decidir si se retira o se alinea después. | ☐ |
| 3 | **`profiles: ["core"]` ELIMINADOS** de qnap.yml y prod.yml: Container Station lanza `up -d` sin `--profile`, con profile la sombra jamás arrancaría. Ahora el core arranca SIEMPRE con la app. | ☐ |
| 4 | **Claves .env nuevas**: `CORE_CAPTURE_PASSWORD` (→ `.env.core.admin.prod`) y `CAPTURE_DSN` (→ fichero NUEVO `.env.core.capture.prod`). Además hay que crear en el NAS los tres ficheros core ya previstos si aún no existen (`.env.core.prod`, `.env.core.admin.prod`, `.env.core.redis.prod`). | ☐ |
| 5 | **Tag de imagen del core: `swissjob-core:prod`** (lo que referencian qnap.yml/prod.yml), no `:dev`. Mismo Dockerfile que dev; solo cambia el tag. | ☐ |
| 6 | **`core-api` arranca también** (sin puerto de host, solo red interna). Sobra en sombra estricta; se deja por paridad con dev y para el `/v1/ready`. Si molesta (RAM), quitarlo del YAML. | ☐ |
| 7 | **Volumen nuevo `core_hf_cache`** (desviación deliberada de dev): la imagen core fija `HF_HOME=/tmp/hf-cache`; sin volumen cada Recreate re-descargaría el modelo (~120 MB) — misma lección que la incidencia #6 del legacy. | ☐ |
| 8 | **`start_period: 600s`** en el healthcheck de core-capture (dev: 300s): el backfill del corpus real corre en la CPU del NAS. | ☐ |

## 1. Prerrequisitos

- Container Station 3.x + Tailscale activos; stack legacy `swissjob` funcionando.
- Espacio en disco del NAS: +~1 GB de imágenes nuevas + margen para WAL
  (el slot lógico RETIENE WAL si el consumidor cae — umbral de alerta 2 GiB, §6).
- **Backup previo obligatorio** (docs/DEPLOY_NAS.md §7):

```bash
ssh Ricardo@capsule
docker exec -t swissjob-postgres pg_dump -U swissjob swissjobhunter \
  | gzip > /share/Public/backups/swissjob/db-pre-sombra-$(date +%Y%m%d-%H%M).sql.gz
```

- En dev: repo al día, suite core verde (`docker compose run --rm core-migrate
  python -m pytest jobhunt_core/tests`).

## 2. Build local de las 2 imágenes nuevas

En la máquina de dev (x86_64 — coincide con el NAS, ver §0.1):

```bash
cd ~/Public/SwissJob

# 1) Core (API + worker + capture + migrate — imagen esbelta, sin Chromium)
#    Contexto = RAÍZ del repo (el .dockerignore lo limita a jobhunt_core).
docker build -t swissjob-core:prod -f jobhunt_core/Dockerfile .

# 2) Postgres con wal2json (pgvector pg16 + postgresql-16-wal2json)
docker build -t swissjob-postgres-core:pg16 docker/postgres-core/
```

Verificación de los builds:

```bash
# El plugin de logical decoding está dentro:
docker run --rm --entrypoint ls swissjob-postgres-core:pg16 \
  /usr/lib/postgresql/16/lib/wal2json.so

# El core arranca y la config carga (con defaults de dev):
docker run --rm swissjob-core:prod python -c "import jobhunt_core.config, jobhunt_core.shadow.capture; print('OK')"
```

## 3. Secretos: ficheros .env.core.*.prod

Cuatro ficheros, plantillas `.example` en la raíz del repo. Generar contraseñas
con `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`
(el bootstrap solo acepta `[A-Za-z0-9_-]`, 8-128 — token_urlsafe cumple).

| Fichero (en `/share/Public/swissjob/`) | Quién lo lee | Contenido |
|---|---|---|
| `.env.core.prod` | core-api, core-worker, core-migrate, core-capture | `CORE_ENV=prod`, `CORE_DATABASE_URL`, `CORE_BROKER_URL`, `CORE_RESULT_BACKEND` |
| `.env.core.admin.prod` | SOLO core-migrate | `CORE_ADMIN_DATABASE_URL` (usuario `swissjob` admin), `CORE_DB_PASSWORD`, **`CORE_CAPTURE_PASSWORD`** (nuevo) |
| `.env.core.redis.prod` | SOLO redis-core | `CORE_REDIS_PASSWORD` |
| `.env.core.capture.prod` **(NUEVO)** | SOLO core-capture | `CAPTURE_DSN` (rol `jobhunt_capture`) |

Invariantes de sincronía (el que falle da `InvalidPasswordError` o crash-loop):

1. `CORE_DB_PASSWORD` (admin) == contraseña dentro de `CORE_DATABASE_URL` — lo
   cruza el propio core-migrate y ABORTA si difieren.
2. `CORE_REDIS_PASSWORD` (redis) == contraseña dentro de `CORE_BROKER_URL` y
   `CORE_RESULT_BACKEND` (misma en ambas — lo exige el validador de config).
3. `CORE_CAPTURE_PASSWORD` (admin) == contraseña dentro de `CAPTURE_DSN`
   (capture). ⚠ Este par NO lo valida nadie en el arranque: revisarlo A MANO.
4. `CORE_ADMIN_DATABASE_URL` lleva el `POSTGRES_PASSWORD` real del `.env.prod`
   legacy del NAS (usuario `swissjob`).
5. Ningún valor `CAMBIA_*` sin rellenar (el validador prod rechaza los CORE_*;
   `CAPTURE_DSN` NO se valida — ver ⚠ de su plantilla).

Permisos: `chmod 600 /share/Public/swissjob/.env.core.*`.

## 4. Transferencia al NAS (save → scp → load)

```bash
# Local
mkdir -p /tmp/swissjob
docker save swissjob-core:prod           -o /tmp/swissjob/swissjob-core.tar
docker save swissjob-postgres-core:pg16  -o /tmp/swissjob/swissjob-postgres-core.tar
ls -lh /tmp/swissjob/*.tar   # core ~0.5-1 GB · postgres ~0.5 GB

scp /tmp/swissjob/swissjob-core.tar          Ricardo@capsule:/share/Public/swissjob/
scp /tmp/swissjob/swissjob-postgres-core.tar Ricardo@capsule:/share/Public/swissjob/
scp ~/Public/SwissJob/docker-compose.qnap.yml Ricardo@capsule:/share/Public/swissjob/
# + los .env.core.*.prod de §3 (o crearlos por SSH/File Station)

# SSH al NAS — cargar ANTES de la ventana (no cuenta como downtime)
ssh Ricardo@capsule
cd /share/Public/swissjob
docker load -i swissjob-core.tar
docker load -i swissjob-postgres-core.tar
docker image ls | grep -E "swissjob-core|swissjob-postgres-core"
```

> Alternativa registry local: `docker tag` + `docker push` a un registry en la
> LAN y `image:` con prefijo del registry en el YAML. No se usa aquí: el flujo
> probado del proyecto es tar+load (docs/DEPLOY_NAS.md §3.5) y Container
> Station no añade nada con registry.
>
> ⚠ NO hacer `docker rmi pgvector/pgvector:pg16` todavía: es la imagen de
> rollback (§10).

## 5. VENTANA DE MANTENIMIENTO — reinicio del Postgres de producción

**Por qué**: `wal_level` NO es recargable (exige reinicio del proceso Postgres)
y además cambia la imagen del contenedor (`pgvector/pgvector:pg16` →
`swissjob-postgres-core:pg16`). El Postgres es COMPARTIDO con el legacy →
parada breve de TODO el stack. Confirmado por el propietario (2026-07-24 el
cambio de compose; 2026-07-28 su aplicación en el NAS).

**Qué esperar**:

- Downtime total del stack: **~3-6 min** (recreate + healthchecks). El dato
  NO se toca: mismo volumen `pgdata`, mismo major PG16 → sin initdb ni
  migración de datos.
- Backend healthy en <60 s (modelo ya en `hfcache`); frontend justo después.
- Primer arranque de la sombra DESPUÉS de la ventana: backfill completo del
  corpus real (~minutos, ver §6.2) — no es downtime, el legacy ya sirve.

**Pasos**:

1. Pre-ventana: §§2-4 completos (imágenes cargadas, YAML y .env en su sitio,
   backup hecho). Verificar sincronía de secretos (§3).
2. Container Station → Applications → swissjob → **Stop**.
3. Applications → swissjob → **⋮ → Recreate** (relee el YAML nuevo del
   filesystem). Si Container Station no ofrece Recreate sobre una app parada:
   editar la aplicación pegando el YAML nuevo y Apply.
4. Verificar el reinicio del Postgres:

```bash
docker ps --filter "name=swissjob-postgres"    # Up (healthy), imagen nueva
docker exec swissjob-postgres psql -U swissjob -d swissjobhunter -c "SHOW wal_level;"
#  wal_level = logical   ← LA verificación de la ventana
docker exec swissjob-postgres psql -U swissjob -d swissjobhunter \
  -c "SHOW max_replication_slots; SHOW max_wal_senders;"
#  4 y 4
docker logs swissjob-backend 2>&1 | tail -5    # Application startup complete.
```

5. Smoke del legacy: `http://capsule.tailebc81d.ts.net:4000` responde, login OK.

**Si el Postgres no arranca**: `docker logs swissjob-postgres`. Causa típica en
rollbacks a medias: slot lógico presente con `wal_level` < logical (§10, orden
del rollback). Con la imagen/YAML nuevos no aplica.

## 6. Orden de arranque y qué verificar en cada paso

Container Station arranca todo con el `depends_on` del YAML; el operador
verifica EN ESTE ORDEN. (`core_run` = helper del Apéndice A.)

### 6.1 core-migrate (one-shot — `Exited (0)` es lo normal)

```bash
docker logs swissjob-core-migrate 2>&1 | grep -E \
  "Rol jobhunt_core|Rol jobhunt_capture|GRANTs RO|Aislamiento verificado|pgvector verificado|Migraciones del core al día"
docker inspect -f '{{.State.ExitCode}}' swissjob-core-migrate   # 0
```

Crea/converge: rol `jobhunt_core` (mínimo privilegio) + esquema `jobhunt` +
rol `jobhunt_capture` (LOGIN REPLICATION) + GRANTs RO enumerados + cadena
Alembic `core0001..core0010`. Si falla, NADA más del core arranca (correcto).

### 6.2 core-capture — bootstrap: slot + snapshot + BACKFILL del corpus REAL

```bash
docker logs -f swissjob-core-capture
# Secuencia esperada:
#   readiness del esquema legacy OK
#   CREATE_REPLICATION_SLOT jobhunt_shadow (snapshot exportado)
#   backfill: N filas (jobs + user_profiles + users)
#   frontera snapshot↔LSN registrada
#   Streaming wal2json v2 arrancado          ← a partir de aquí, streaming
```

Verificar backfill consistente (DoD B-01 — conteos legacy == staging):

```bash
docker exec swissjob-postgres psql -U swissjob -d swissjobhunter -c "
SELECT (SELECT count(*) FROM public.jobs)
     + (SELECT count(*) FROM public.user_profiles)
     + (SELECT count(*) FROM public.users)      AS legacy_total,
       (SELECT count(*) FROM jobhunt.shadow_change_log
         WHERE lsn = (SELECT snapshot_lsn FROM jobhunt.shadow_capture_state))
                                                AS backfill_staging;"
# legacy_total == backfill_staging

docker exec swissjob-postgres psql -U swissjob -d swissjobhunter -c "
SELECT slot_name, active, active_pid,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS wal_retenido
FROM pg_replication_slots WHERE slot_name = 'jobhunt_shadow';"
# active = t, active_pid IS NOT NULL, wal_retenido pequeño

docker ps --filter "name=swissjob-core-capture"   # (healthy) tras el start_period
```

### 6.3 core-worker — beat embebido vivo

```bash
docker ps --filter "name=swissjob-core-worker"                    # Up
docker logs swissjob-core-worker 2>&1 | grep -m1 "beat: Starting" # beat vivo
# En <= 5 min despacha las cadencias:
docker logs swissjob-core-worker 2>&1 | grep -E "Sending due task (shadow-|delivery-)"
```

⚠ Primer ciclo de embeddings: descarga del modelo (~120 MB) a `core_hf_cache`
— solo la primera vez.

### 6.4 core-api (opcional) y redis-core

```bash
docker exec swissjob-core-api python -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/v1/ready').status)"   # 200
docker ps --filter "name=swissjob-redis-core"   # Up (healthy); SIN puerto de host
```

## 7. Bootstrap del modelo y la política (receta v2)

Mismas costuras que en dev (`register_model` / `ensure_policy`) y MISMOS
identificadores — la identidad del espacio vectorial es
`(name, version, recipe_version)` y la versión es el commit SHA INMUTABLE de HF
(los valores de abajo son EXACTAMENTE los registrados en dev; verificados
2026-07-28 contra la BD de dev). Ejecutar tras 6.1:

```bash
core_run python -c "
import asyncio
from jobhunt_core.database import task_session_factory
from jobhunt_core import embeddings, matching
from jobhunt_core.embedding_recipes import LEGACY_V1, ROLE_COMPOSITE_V2

NAME = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
SHA  = 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'

async def main():
    async with task_session_factory() as factory:
        async with factory() as s:
            mid = await embeddings.register_model(
                s, NAME, SHA, recipe_version=ROLE_COMPOSITE_V2, active=True)
            # legacy_v1 conservada INACTIVA (paridad con dev: rollback de receta)
            await embeddings.register_model(
                s, NAME, SHA, recipe_version=LEGACY_V1, active=False)
            pid = await matching.ensure_policy(
                s, name='cosine-baseline', prompt_version='v1', active=True)
            await s.commit()
            print('model_id:', mid, '| policy_id:', pid)
asyncio.run(main())
"
```

Verificar:

```bash
docker exec swissjob-postgres psql -U swissjob -d swissjobhunter -c "
SELECT name, version, recipe_version, dim, active FROM jobhunt.embedding_models;
SELECT name, prompt_version, active FROM jobhunt.scoring_policies;"
# role_composite_v2 activo (t), legacy_v1 inactivo (f), cosine-baseline/v1 activo
```

Idempotente: re-ejecutarlo no crea filas nuevas (ON CONFLICT + relectura bajo
lock). El orden importa poco — si el proyector corre antes del bootstrap, las
ofertas quedan sin vector hasta que el **proyector sombra** (beat `shadow-project`,
cada `CORE_SHADOW_PROJECT_EVERY_S` ≈ 5 min) drene los embeddings pendientes vía
`run_pending` al existir el modelo activo; convergen solas. (NO hay un beat
`run_pending` propio: `run_pending` se invoca DENTRO del proyector — `_drain_embeddings`.)

## 8. ORÁCULO sobre los datos reales del NAS

**Los sets de dev NO viajan** (etiquetan jobs de dev — `job_ref` = hash de
`public.jobs` LOCAL, sin sentido en el corpus del NAS). El DoD de B-03 y
`labels_ready` se cumplen ALLÍ, sobre el corpus real. En producción los sets
exigen **usuarios reales** (decisión delegada 2026-07-28: las personas de
evaluación solo valen en dev).

Requisitos (DoD B-03 + §6): **≥ 2 perfiles reales** con set **CONGELADO**,
**≥ 30 juicios**/set, **≥ 50 pares dedup**; con < 50 juicios rel≥2 el gate de
`falsos_negativos` opera en modo 0-permitidos (esperado con pocos usuarios).

### 8.1 Crear + sembrar (tras la primera proyección — perfiles ya en el core)

```bash
# Perfiles proyectados (external_ref = user_id legacy):
docker exec swissjob-postgres psql -U swissjob -d swissjobhunter -c \
  "SELECT id, external_ref FROM jobhunt.profiles;"

core_run python -c "
import asyncio, uuid
from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow import labels

PROFILE_ID = uuid.UUID('<id de jobhunt.profiles>')

async def main():
    async with task_session_factory() as factory:
        async with factory() as s:
            sid = await labels.create_set(s, PROFILE_ID, 'nas-ronda-1')
            n = await labels.seed_labels(s, sid)          # feedback legacy RO
            d = await labels.seed_dedup_pairs(s, sid)     # jobs.duplicate_of
            await s.commit()
            print('set:', sid, '| juicios seed:', n, '| pares dedup:', d)
asyncio.run(main())
"
```

Semillas trazables: `thumbs_up→2`, `applied→3`, `thumbs_down/dismissed→0`
(de `match_results.feedback`, read-only); pares dedup desde `duplicate_of`.

### 8.2 CURACIÓN MANUAL (obligatoria antes de congelar)

Revisar los seeds y añadir juicios hasta ≥ 30 por set (0 irrelevante · 1
marginal · 2 relevante · 3 ideal). La curación PISA seeds; un seed jamás pisa
curación:

```bash
core_run python -c "
import asyncio, uuid
from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow import labels

SET_ID = uuid.UUID('<set_id>')
JUICIOS = {  # job_ref (public.jobs.hash) -> relevance 0..3
    '<hash>': 3,
    # ...
}

async def main():
    async with task_session_factory() as factory:
        async with factory() as s:
            for ref, rel in JUICIOS.items():
                await labels.add_judgment(s, SET_ID, ref, rel)
            await s.commit()
asyncio.run(main())
"
```

### 8.3 FREEZE (el oráculo no se mueve durante la medición)

```bash
core_run python -c "
import asyncio, uuid
from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow import labels

async def main():
    async with task_session_factory() as factory:
        async with factory() as s:
            print(await labels.freeze_set(s, uuid.UUID('<set_id>')))
            await s.commit()
asyncio.run(main())
"
```

Repetir 8.1-8.3 para el segundo perfil real. Congelado = INMUTABLE: un error
de etiquetado ⇒ set NUEVO (`nas-ronda-2`), nunca editar el congelado. El
contador del GATE-SOMBRA (7 ciclos) solo corre con `labels_ready` (≥ 2 sets
congelados de perfiles ACTIVOS) — verificar con el informe del gate
(Apéndice A).

## 9. Verificación post-despliegue — checklist tipo revisor

Infra:

- [ ] `SHOW wal_level` = `logical`; `max_replication_slots`/`max_wal_senders` = 4.
- [ ] `wal2json.so` presente en la imagen del postgres en ejecución.
- [ ] `docker ps`: 14 contenedores del stack; core-migrate `Exited (0)`;
      capture/worker/redis-core/api `Up` (capture y redis-core `healthy`).
- [ ] NINGÚN servicio core con puerto de host (`docker ps` sin binds nuevos;
      el único puerto publicado del stack sigue siendo `4000` del frontend).
- [ ] `docker logs swissjob-core-migrate`: "Aislamiento verificado" y
      "Rol de captura verificado".

CDC:

- [ ] Slot `jobhunt_shadow`: `active = t`, `wal_retenido` estable y pequeño
      (< 100 MB en reposo; alerta del contrato a 2 GiB).
- [ ] Backfill == corpus legacy (consulta de §6.2).
- [ ] Heartbeat fresco: `SELECT now() - heartbeat_at FROM
      jobhunt.shadow_capture_state;` < 1 min.
- [ ] GDPR: los payloads de `users` en staging solo llevan id/is_active —
      `SELECT bool_and(payload - '_omitted' - '_backfilled' <@
      '{}'::jsonb || jsonb_build_object('id', payload->'id', 'is_active',
      payload->'is_active')) FROM jobhunt.shadow_change_log WHERE src_table =
      'public.users';` → `t` (jamás email/hashed_password/gdpr_*).

Pipeline:

- [ ] **Primer lote proyectado < 600 s**: `SELECT finished_at - min_received_at
      AS latencia, changes, recovered FROM jobhunt.shadow_projection_batches
      ORDER BY started_at DESC LIMIT 5;` — latencia < 10 min, `recovered = f`.
- [ ] Vacantes sombra creciendo: `SELECT s.name, count(*) FROM
      jobhunt.source_listings sl JOIN jobhunt.sources s ON s.id = sl.source_id
      WHERE s.name LIKE 'legacy:%' GROUP BY 1;` ≈ corpus por fuente.
- [ ] Embeddings de la receta v2: `SELECT count(*) FROM
      jobhunt.offer_embeddings oe JOIN jobhunt.embedding_models m ON
      m.id = oe.model_id WHERE m.recipe_version = 'role_composite_v2';` > 0 y
      creciendo hasta cubrir el corpus.
- [ ] **Entrega al shadow_inbox**: `SELECT consumer_id, count(*) FROM
      jobhunt.shadow_inbox GROUP BY 1;` crece con cada despacho; re-entregas
      no duplican (PK consumer_id+event_id).
- [ ] Outbox sano: 0 dead-letter (`SELECT count(*) FROM
      jobhunt.integration_outbox WHERE state = 'dead';` = 0).
- [ ] Beat despachando: log del worker con `Sending due task shadow-*` y
      `delivery-dispatch-outbox` cada 5 min (liveness §5 del RUNBOOK: > 15 min
      sin samples ⇒ beat caído aunque el worker viva).

Legacy intacto (contrato §0):

- [ ] Frontend `:4000` responde; login y matches del usuario real funcionan.
- [ ] `docker logs swissjob-worker` sin errores nuevos; cosecha diaria corre.
- [ ] CERO correos/notificaciones originados por el core (el outbox solo
      escribe en `jobhunt.shadow_inbox` — verificado arriba).
- [ ] Disco del NAS con margen; vigilar crecimiento de WAL los primeros días
      (amplificación por `wal_level=logical`, §8 del contrato).

Tras 24-48 h:

- [ ] Primer ciclo calendario cerrado: filas en `jobhunt.shadow_cycle_metrics`
      del `cycle_id` de ayer; informe del gate legible (Apéndice A).
- [ ] `labels_ready` (si §8 ya está hecho) y contador de ciclos avanzando.

## 10. Rollback completo

### 10.1 Parada de emergencia de la sombra (sin ventana — el legacy no se toca)

Cuándo: la sombra da problemas (crash-loop, WAL retenido creciendo, CPU) y hay
que quitarla YA. El legacy sigue sirviendo en todo momento.

```bash
# 1) Parar el consumidor SIEMPRE primero
docker stop swissjob-core-capture

# 2) DROP del slot (libera el WAL retenido AL INSTANTE) — bucle
#    terminate→espera→drop con guardas:
core_run python -c "
import os
from jobhunt_core.shadow.gate import emergency_drop_slot
print(emergency_drop_slot(capture_dsn=os.environ['CAPTURE_DSN'], confirm=True))
"   # ← core_run con --env-file TAMBIÉN de .env.core.capture.prod (Apéndice A)

# 3) Parar el resto del core
docker stop swissjob-core-worker swissjob-core-api swissjob-redis-core

# 4) Verificar: slot ausente y legacy vivo
docker exec swissjob-postgres psql -U swissjob -d swissjobhunter -c \
  "SELECT slot_name FROM pg_replication_slots;"   # sin jobhunt_shadow
```

La sombra queda SIN continuidad WAL: para reconstruirla después, rollback/replay
completo (RUNBOOK.md §3) — NO re-arrancar core-capture antes (fallaría con
"slot AUSENTE" a propósito). `wal_level=logical` puede quedarse activo sin la
sombra (coste: algo más de WAL) hasta la siguiente ventana.

### 10.2 Revert total del compose (segunda ventana de mantenimiento)

⚠ **ORDEN CRÍTICO: dropear el slot ANTES de revertir `wal_level`.** Un Postgres
con slots lógicos presentes y `wal_level` < logical **NO ARRANCA** — el
rollback mal ordenado convierte un revert en un outage.

1. Ejecutar 10.1 completo (slot dropeado, core parado).
2. Restaurar el YAML anterior en `/share/Public/swissjob/docker-compose.qnap.yml`
   (en el repo: revert del commit de este paquete): postgres vuelve a
   `pgvector/pgvector:pg16` SIN `command:`, y desaparecen los servicios core.
3. Container Station → swissjob → Stop → Recreate (ventana ~3-6 min).
4. Verificar: `SHOW wal_level;` → `replica`; backend healthy; frontend OK.
5. Limpieza opcional: `docker rmi swissjob-core:prod
   swissjob-postgres-core:pg16`; los datos de la sombra viven SOLO en el
   esquema `jobhunt` — conservarlos no afecta al legacy; para borrado total:
   `DROP SCHEMA jobhunt CASCADE; DROP ROLE jobhunt_core; DROP ROLE
   jobhunt_capture;` (con el usuario admin, y solo si de verdad se abandona
   la Fase B) + borrar `/share/Public/swissjob/.env.core.*`.

## Apéndice A. Ejecutar comandos del core en el NAS (sin docker compose)

`docker compose run/exec` NO funciona por SSH en el QNAP (docs/DEPLOY_NAS.md
§0.3). Equivalente con `docker run` sobre la red de la aplicación (Container
Station la nombra `<app>_<red>`; verificar con `docker network ls | grep
swissjob`):

```bash
# Helper — pegar en la sesión SSH (o en ~/.profile del NAS):
core_run() {
  docker run --rm --network swissjob_swissjob-net \
    --env-file /share/Public/swissjob/.env.core.prod \
    --env-file /share/Public/swissjob/.env.core.capture.prod \
    swissjob-core:prod "$@"
}
```

(El `--env-file` de capture solo hace falta para las guardas del slot —
`emergency_drop_slot` / `rollback_replay`; para el resto sobra y puede
omitirse.)

Traducción de los comandos del [RUNBOOK.md](RUNBOOK.md) a NAS:

| RUNBOOK (dev) | NAS |
|---|---|
| `docker compose exec -T postgres psql -U swissjob ...` | `docker exec swissjob-postgres psql -U swissjob ...` |
| `docker compose run --rm core-migrate python -c "..."` | `core_run python -c "..."` |
| `docker compose restart core-capture` | `docker restart swissjob-core-capture` |
| `docker compose logs -f core-capture` | `docker logs -f swissjob-core-capture` |
| `docker compose stop core-capture` | `docker stop swissjob-core-capture` |

Informe del gate en el NAS:

```bash
core_run python -c "
import asyncio
from jobhunt_core.database import task_session_factory
from jobhunt_core.shadow.gate import render_gate_report
async def main():
    async with task_session_factory() as factory:
        async with factory() as s:
            print(await render_gate_report(s))
asyncio.run(main())
"
```

> ⚠️ PAQUETE INERTE (reprioridad del propietario 2026-07-28): los cambios de los composes
> qnap/prod NO están aplicados en el repo — viven en `deploy_nas_composes.patch` y se aplican
> con `git apply jobhunt_core/shadow/deploy_nas_composes.patch` como PRIMER paso del despliegue
> (§2). Motivo: un compose ya modificado + `up -d` rutinario en el NAS sin las imágenes
> cargadas tumbaría el postgres de producción.
