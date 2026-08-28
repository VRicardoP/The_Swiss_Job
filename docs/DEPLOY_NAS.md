# Despliegue — SwissJobHunter

> Guía completa de despliegue. Pensada para QNAP NAS con Container Station +
> Tailscale, pero documenta también los flujos alternativos (dev local y
> build-en-NAS). Acceso final en producción:
> `http://capsule.tailebc81d.ts.net:4000` desde cualquier dispositivo
> de la tailnet (Mac, Linux, iPhone, iPad).
>
> **ALCANCE: solo el stack LEGACY** (postgres, redis, backend, worker, frontend).
> El core y la SOMBRA (`core-api`, `core-worker`, `core-capture`, `redis-core`, y el
> Postgres con wal2json que exige `wal_level=logical`) tienen su propio paquete:
> [`jobhunt_core/shadow/DEPLOY_NAS.md`](../jobhunt_core/shadow/DEPLOY_NAS.md), con
> ventana de mantenimiento propia (reiniciar Postgres) y su
> `deploy_nas_composes.patch` — que NO está aplicado al repo a propósito.

---

## Índice

- [0. Particularidades de QNAP Container Station — LEE ESTO PRIMERO](#0-particularidades-de-qnap-container-station--lee-esto-primero)
- [1. Variantes del compose](#1-variantes-del-compose)
  - [1.1 Perfil operativo vs. perfil de desarrollo del core](#11-perfil-operativo-vs-perfil-de-desarrollo-del-core-2026-08-27-p1-3)
- [2. Arquitectura del stack](#2-arquitectura-del-stack)
- [3. Despliegue desde cero en el NAS — paso a paso](#3-despliegue-desde-cero-en-el-nas--paso-a-paso)
- [4. Operaciones cotidianas](#4-operaciones-cotidianas)
- [5. Actualización de versión](#5-actualización-de-versión)
  - [🚦 GATE: la canonización de identidad sigue SIN aplicar en el NAS](#-gate-de-subida--está-aplicada-ya-en-el-nas-la-canonización-de-identidad)
  - [5.3 Secuencia única de mantenimiento (canonización + subida)](#53-secuencia-única-de-mantenimiento-canonización--subida)
- [6. Troubleshooting](#6-troubleshooting)
- [7. Backups](#7-backups)
- [8. Recursos esperados](#8-recursos-esperados)

---

## 0. Particularidades de QNAP Container Station — LEE ESTO PRIMERO

QNAP Container Station NO es un Docker estándar. Los detalles siguientes han
costado horas de debugging — documentados aquí para no repetirlos.

### 0.1 Container Station NO expande `${VARS}` del compose

Container Station carga el YAML directamente sin pasar por el preprocessor de
Docker Compose CLI. Por tanto:

- `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}` → queda vacío al inicializar
  Postgres → backend conecta con otro password → `InvalidPasswordError`.
- `pg_isready -U ${POSTGRES_USER}` en healthcheck → resuelve a `pg_isready -U`
  → healthcheck falla.

**Solución**: usar `env_file:` (con path absoluto, ver 0.2) para inyectar las
variables como entorno del contenedor. En healthchecks usar `$$VAR` (doble
dólar) para que la resolución la haga el shell del contenedor en runtime:

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
```

### 0.2 Container Station NO resuelve paths relativos

Tanto `env_file:` como bind-mounts (`volumes:`) DEBEN usar paths absolutos.

```yaml
# ❌ NO funciona en Container Station
env_file: [.env.prod]
volumes: ["./docker/postgres/init.sql:/..."]

# ✅ Sí funciona
env_file: [/share/Public/swissjob/.env.prod]
volumes: ["/share/Public/swissjob/docker/postgres/init.sql:/..."]
```

### 0.3 `docker compose` NO funciona en SSH para usuarios normales

Por SSH como usuario no-admin:

```
$ docker compose -f docker-compose.qnap.yml down
unknown shorthand flag: 'f' in -f
```

El plugin `compose*` (asterisco = external plugin) vive en
`/share/CACHEDEV1_DATA/.qpkg/container-station/homes/<user>/.docker/` con
permisos restringidos.

**Comandos `docker` SSH que SÍ funcionan**: `run`, `exec`, `load`, `save`,
`rmi`, `logs`, `image ls`, `ps`, `inspect`, `network ls`, `volume ls`,
`stats`, `restart`.

**Para `up`/`down`/`recreate` del stack**: usa la UI de
**Container Station → Applications → swissjob → Stop / Recreate**.
La opción "Recreate" relee el YAML del filesystem.

### 0.4 Primer arranque: el modelo embedding se carga en BACKGROUND

`paraphrase-multilingual-MiniLM-L12-v2` (~120 MB) sigue tardando 4-5 min en
descargarse/cargarse la primera vez, PERO desde 2026-07-18 el `lifespan` ya
**no bloquea** en esa carga: se lanza como tarea background
(`asyncio.to_thread`, gateada por `EMBEDDING_PRELOAD_ON_STARTUP=True`), así que
`Application startup complete.` aparece de inmediato y el backend pasa a
healthy en segundos. La primera petición de matching que necesite el modelo
esperará a que termine la carga; el resto de la API responde ya.

- **En los logs verás `Embedding model warmed up`** cuando la carga background
  termina (ya NO el antiguo `Preloading embedding model...`, que era síncrono).
- Con `EMBEDDING_PRELOAD_ON_STARTUP=False` la carga es puramente perezosa (en la
  primera petición que la use).

**Mitigaciones que siguen aplicando** en `docker-compose.qnap.yml` (y `prod.yml`):

- Healthcheck con `start_period: 360s` y `retries: 5` (holgura de sobra ahora que
  el arranque no bloquea; se mantiene por seguridad).
- Volumen `hfcache` para que el modelo persista entre recreates → siguientes
  arranques no re-descargan.

### 0.5 `alembic.ini` tenía el password hard-coded

El entrypoint del contenedor ejecuta `alembic upgrade head` antes de gunicorn,
y Alembic leía `sqlalchemy.url` del `.ini` ignorando `DATABASE_URL`. Cualquier
despliegue con password ≠ default fallaba.

**Arreglado en código**: [backend/alembic/env.py](../backend/alembic/env.py)
sobrescribe el `sqlalchemy.url` con `settings.DATABASE_URL` (pydantic-settings
→ env vars > `.env`). Una sola fuente de verdad. Verificación post-rebuild:

```bash
docker run --rm --entrypoint sh swissjob-backend:prod \
  -c 'grep sqlalchemy.url alembic.ini; grep set_main_option alembic/env.py'
# Esperado:
#   sqlalchemy.url = driver://user:pass@localhost/dbname
#   config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```

### 0.6 Sincronización del `.env.prod`

`POSTGRES_PASSWORD` y la parte `:password@` de `DATABASE_URL` deben coincidir
exactamente. Si modificas uno, modifica el otro. Verifica:

```bash
grep -E "^POSTGRES_|^DATABASE_URL" /share/Public/swissjob/.env.prod
```

### 0.7 Rutas estándar en el NAS

| Concepto | Ruta |
|---|---|
| Stack files | `/share/Public/swissjob/` |
| `.env.prod` | `/share/Public/swissjob/.env.prod` |
| YAML compose | `/share/Public/swissjob/docker-compose.qnap.yml` |
| Tars imágenes | `/share/Public/swissjob/swissjob-{backend,frontend}.tar` |
| Script init pgvector | `/share/Public/swissjob/docker/postgres/init-pgvector.sql` |

---

## 1. Variantes del compose

| Fichero | Uso | Imágenes | Paths |
|---|---|---|---|
| [`docker-compose.yml`](../docker-compose.yml) | Dev local (Linux/Mac) | Build local | Relativos |
| [`docker-compose.prod.yml`](../docker-compose.prod.yml) | NAS build-on-site | Build en el NAS | Relativos |
| [`docker-compose.prebuilt.yml`](../docker-compose.prebuilt.yml) | NAS con tars cargados | `docker load` previo | Relativos |
| [`docker-compose.qnap.yml`](../docker-compose.qnap.yml) | **Container Station** | `docker load` previo | **Absolutos** |

| [`docker-compose.dev.yml`](../docker-compose.dev.yml) | **Override de DESARROLLO del core** (nunca en el NAS) | — | Relativos |

> **Para Container Station usa siempre `docker-compose.qnap.yml`.** Es el único
> testeado con los gotchas de la sección 0.

### 1.1 Perfil operativo vs. perfil de desarrollo del core (2026-08-27, P1-3)

Desde `ae7fbf2`, `docker-compose.yml` es un perfil **OPERATIVO**: `core-api`,
`core-worker`, `core-capture` y `core-migrate` **ya no montan** `./jobhunt_core`.
Corren el código de la imagen, y `RELEASE_SHA` se hornea como *build arg*
(ARG → ENV en `jobhunt_core/Dockerfile`) — deliberadamente **no** va en ningún
`environment:`, para que no pueda desligarse del código que identifica.

Motivo: un proceso podía servir la release A con los ficheros y el esquema ya en la
B. Pasó dos veces en este despliegue (el capturador cinco días con código viejo en
memoria; la API dos días en 503 con la base sana). Si el código no se monta, cambiarlo
exige reconstruir la imagen, y eso recrea el contenedor.

**Todo comando del core que deba ver el árbol de trabajo necesita el override
explícito**, empezando por la suite:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm core-migrate python -m pytest jobhunt_core/tests
```

Sin los dos `-f` se probaría el código de la **imagen**. No es un
`docker-compose.override.yml` a propósito: uno implícito se aplicaría también al
desplegar y devolvería el defecto en silencio; olvidarse del `-f` deja el perfil
**seguro**, no el mutable. El override pone `CORE_CODE_MUTABLE=1`, con lo que
`/v1/ready` responde `authoritative: false` (verde **informativo**, no autorización
para operar).

**Verificación de una release, comprobable en vez de confiada:**

```bash
curl -s localhost:8003/v1/health   # {"release":"<sha>","alembic_expected":"…","authoritative":true}
curl -s localhost:8003/v1/ready    # {"release":"<sha>","authoritative":true,...}
```

Todos los procesos del core deben publicar **el mismo** `release` y el mismo head, y
`authoritative: true`. Las dos sondas llevan la marca (G9 P2-A: health la publicaba sin
ella, y es la primera que se ejecuta aquí). `authoritative` es **false** si el código va
montado —y eso ya **se comprueba**, no se supone: el proceso lee `/proc/self/mountinfo` y
busca montajes sobre su propio árbol (G11 P2-2; antes bastaba montar el código sin poner
`CORE_CODE_MUTABLE` para que la marca siguiera en verde sobre código sustituido)—, si la
imagen no sabe nombrar su release
(`RELEASE_SHA=unknown`) —sin esa condición el «mismo SHA» se cumpliría entre `unknown`s
sin decir nada (G9 P2-B)— **o si el `RELEASE_SHA` del entorno no es el que la imagen
lleva horneado en `/opt/jobhunt-release/RELEASE`** (G10 P2-2). Esto último cierra el
agujero de la marca: `RELEASE_SHA` es un ENV de la imagen y el ENV de una imagen lo pisa
cualquier `environment:`/`env_file:` del contenedor, así que un `-e RELEASE_SHA=deadbee`
bastaba para publicar `deadbee` con `authoritative: true` sobre el código de otra
construcción. Si una sonda da `authoritative: false` con el código NO montado y un SHA
con pinta normal, comprueba justamente eso:

```bash
docker compose exec -T core-api printenv RELEASE_SHA                  # lo que inyecta el entorno
docker compose exec -T core-api cat /opt/jobhunt-release/RELEASE      # lo que hornea la imagen
```

> **Por qué el marcador NO vive en `/app/RELEASE`** (auditoría externa R2 P1-1). Vivía
> ahí, con un comentario que llamaba a `/app` «un sitio que el entorno no puede pisar».
> Era falso: `/app` es el WORKDIR y **contiene** el paquete, así que un bind mount en
> `/app` sustituye de un golpe el código y el marcador. Y la comprobación de montajes
> solo miraba el punto IGUAL a la raíz del paquete o por DEBAJO, nunca sus ancestros: con
> un montaje en `/app` que trajera un `RELEASE` igual al `RELEASE_SHA` del entorno, la
> sonda daba `authoritative: true` sobre código ajeno. Hoy cuenta como mutable el punto
> igual, el descendiente **y el ancestro no-`/`** —de la raíz del paquete y del marcador—,
> y el marcador vive fuera de `/app`.
>
> ⚠ **Hasta dónde llega esta marca, y hasta dónde no.** Todo lo que compara lo observa el
> propio proceso **desde dentro** del contenedor: cierra la falsificación local
> demostrada (montar el código, montar el ancla, inyectar `RELEASE_SHA`) y **nada más**.
> Una garantía industrial de procedencia exige además contrastar el **digest de la imagen
> tal y como lo ve el orquestador** —`docker inspect --format '{{.Image}}'
> swissjob-core-api` y el digest del repositorio—, que es la única señal que no vive
> dentro de lo que se está auditando. Mientras eso no se haga, `authoritative: true`
> significa «este proceso no puede detectar sustitución», no «esta imagen es la
> aprobada».

> ✅ **Deuda cerrada (2026-08-28).** `core-api` ya tiene healthcheck de compose contra
> `/v1/ready` en los TRES composes (`docker-compose.yml`, `.prod.yml` y `.qnap.yml`), y
> `core-migrate` de `.prod.yml` construye con el build arg `RELEASE_SHA`. Sin esa sonda
> un `core-api` en 503 podía pasar días sin que nadie se enterara — es exactamente lo
> que ocurrió. Al desplegar, esto hace que Container Station marque `swissjob-core-api`
> como *unhealthy* cuando la BD no responde o su head de Alembic no es el que espera la
> imagen, en vez de mostrarlo verde.

### Puertos por variante

**Dev local** (`docker-compose.yml`): puertos host mapeados para evitar
conflictos con otros servicios:

| Servicio | Container | Host (local) |
|---|---|---|
| PostgreSQL | 5432 | 5435 |
| Redis | 6379 | 6380 |
| Backend | 8000 | 8002 |
| Frontend | 5173 | 5174 |

**Producción** (`prod.yml`/`prebuilt.yml`/`qnap.yml`): solo el frontend (nginx)
expone puerto al host (`4000:80`). Backend, postgres y redis son privados de
la red `swissjob-net`.

---

## 2. Arquitectura del stack

```
                         ┌─────────────────┐
   Cliente (tailnet) ───▶│ frontend :4000  │
                         │  nginx → React  │
                         └────────┬────────┘
                                  │ /api/*
                                  ▼
                         ┌─────────────────┐
                         │ backend :8000   │◀─── alembic upgrade head
                         │ FastAPI gunicorn│     (en entrypoint)
                         │ 2 workers       │
                         └───┬─────┬───────┘
                             │     │
                  ┌──────────┘     └───────────┐
                  ▼                            ▼
          ┌──────────────┐            ┌──────────────┐
          │ postgres     │            │ redis        │
          │ pgvector pg16│            │ 7-alpine     │
          └──────────────┘            └──┬───────────┘
                                         │ broker / result
                                         ▼
                                ┌──────────────┐
                                │ worker       │
                                │ celery       │
                                │ -Q default,  │
                                │   scraping,  │
                                │   ai         │
                                └──────────────┘
```

**Servicios**:

- **postgres** (`pgvector/pgvector:pg16`) — BD principal con extensión
  pgvector para embeddings. Healthcheck: `pg_isready`. Volumen: `pgdata`.
- **redis** (`redis:7-alpine`) — cache (DB 0), broker Celery (DB 1), backend
  de resultados Celery (DB 2). 512 MB max, política `allkeys-lru`. Volumen:
  `redisdata`.
- **backend** (`swissjob-backend:prod`) — FastAPI bajo gunicorn (2 workers
  uvicorn). Entrypoint corre `alembic upgrade head` antes de gunicorn.
  Carga `paraphrase-multilingual-MiniLM-L12-v2` en lifespan. Healthcheck
  HTTP `/health`. Volumen: `hfcache`.
- **worker** (`swissjob-backend:prod`) — Celery con tres queues:
  - `default`: fetchs cada 6h de los providers vía API (25 registrados a 2026-08-15: 20 activos + 5 restringidos gated).
  - `scraping`: scrapers HTTP/Playwright cada 6h.
  - `ai`: matching, traducción, generación de documentos vía Groq.
  Comparte `hfcache` con backend.
- **frontend** (`swissjob-frontend:prod`) — Nginx servidor del build de
  React + proxy `/api/*` → backend. Único puerto expuesto al host.

**Volúmenes persistentes**:

| Volumen | Contenido | Tamaño típico |
|---|---|---|
| `pgdata` | BD Postgres | crece 50-100 MB/semana |
| `redisdata` | Snapshots Redis | <100 MB |
| `hfcache` | Modelo embedding | ~120 MB (constante) |

---

## 3. Despliegue desde cero en el NAS — paso a paso

> Asume Container Station 3.x instalado y Tailscale activo en el NAS.

### 3.1 Build de imágenes en la máquina de desarrollo

```bash
cd ~/Public/SwissJob

# Backend (incluye Playwright + Chromium, ~10 min primera vez)
docker build -t swissjob-backend:prod -f backend/Dockerfile.prod backend/

# Frontend (Vite build + nginx, ~2 min)
docker build -t swissjob-frontend:prod -f frontend/Dockerfile.prod frontend/
```

### 3.2 Generar tars

```bash
mkdir -p /tmp/swissjob

docker save swissjob-backend:prod  -o /tmp/swissjob/swissjob-backend.tar
docker save swissjob-frontend:prod -o /tmp/swissjob/swissjob-frontend.tar

# Verificar tamaños esperados
ls -lh /tmp/swissjob/*.tar
# Backend  ~3.5 GB · Frontend ~50 MB
```

### 3.3 Preparar `.env.prod`

Copia `backend/.env.prod.example` (si existe) o crea uno desde cero:

```bash
# Generar secretos:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"  # → SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"  # → POSTGRES_PASSWORD

# Plantilla
cat > /tmp/swissjob/.env.prod <<'EOF'
# === Database ===
POSTGRES_USER=swissjob
POSTGRES_PASSWORD=<el-mismo-en-las-dos-líneas>
POSTGRES_DB=swissjobhunter
DATABASE_URL=postgresql+asyncpg://swissjob:<el-mismo-en-las-dos-líneas>@postgres:5432/swissjobhunter

# === Redis ===
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# === App secrets ===
SECRET_KEY=<output del token_urlsafe(48)>

# === Groq LLM ===
GROQ_API_KEY=gsk_...

# === Google Gemini (LLM PRIMARIO de generación de CV/carta) ===
# Sin ella, la generación de documentos cae al fallback Groq gpt-oss-120b
# (que en el free tier de Groq topa a 8k tokens/min).
GEMINI_API_KEY=

# === Email (SMTP) para avisos — alerta de docencia primaria ===
# Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, App Password (16 car., 2FA).
# Vacío = la alerta no envía (queda desactivada limpiamente).
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
TEACHER_ALERT_EMAIL=amoore3199@gmail.com

# === Providers opcionales (vacíos = desactivados) ===
JSEARCH_RAPIDAPI_KEY=
ADZUNA_APP_ID=
ADZUNA_APP_KEY=
JOOBLE_API_KEY=
CAREERJET_AFFID=

# === CORS Tailscale ===
BACKEND_CORS_ORIGINS=["http://capsule.tailebc81d.ts.net:4000"]

# === Scheduler ===
SCHEDULER_ENABLED=True
# Cosecha diaria autónoma (True) vs fetch por intervalos (False):
SCHEDULER_DAILY_HARVEST_ENABLED=True
EOF
```

**Checklist obligatorio**:

- [ ] `SECRET_KEY` ≠ `change-me-in-production` (el backend aborta si lo es)
- [ ] `POSTGRES_PASSWORD` igual en sus dos apariciones (raw y dentro de `DATABASE_URL`)
- [ ] `GROQ_API_KEY` rellenada (sin ella matching funciona pero sin LLM rerank,
      traducción ni generador de cartas)
- [ ] `GEMINI_API_KEY` rellenada (LLM primario de CV/carta; sin ella cae al fallback
      Groq gpt-oss-120b, limitado en el free tier)
- [ ] `SMTP_*` rellenadas si quieres la alerta de docencia primaria por email
      (`TEACHER_ALERT_EMAIL`); vacías = alerta desactivada sin error
- [ ] `BACKEND_CORS_ORIGINS` apunta al hostname Tailscale correcto

### 3.4 Transferir al NAS

Desde tu local, vía SCP (o SMB / File Station):

```bash
cd /tmp/swissjob

scp swissjob-backend.tar    Ricardo@capsule:/share/Public/swissjob/
scp swissjob-frontend.tar   Ricardo@capsule:/share/Public/swissjob/
scp .env.prod               Ricardo@capsule:/share/Public/swissjob/
scp docker-compose.qnap.yml Ricardo@capsule:/share/Public/swissjob/

# El init-pgvector.sql también, si no está ya:
scp -r ~/Public/SwissJob/docker Ricardo@capsule:/share/Public/swissjob/
```

### 3.5 Cargar imágenes en el NAS (SSH)

```bash
ssh Ricardo@capsule
cd /share/Public/swissjob

# Borrar imágenes viejas (si hay) para evitar overlay raro
docker rmi swissjob-backend:prod swissjob-frontend:prod 2>/dev/null

# Cargar las nuevas (puede tardar 1-2 min cada una)
docker load -i swissjob-backend.tar
docker load -i swissjob-frontend.tar

# Verificar que la fix de alembic está dentro de la imagen backend
docker run --rm --entrypoint sh swissjob-backend:prod \
  -c 'grep sqlalchemy.url alembic.ini; grep set_main_option alembic/env.py'
# Esperado:
#   sqlalchemy.url = driver://user:pass@localhost/dbname
#   config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Verificar credenciales del .env.prod (sin mostrar el password en pantalla
# si hay alguien mirando)
grep -E "^POSTGRES_|^DATABASE_URL" .env.prod
```

### 3.6 Crear la aplicación en Container Station

1. Container Station → **Applications** → **Create**
2. Modo: **Create with YAML**
3. Sube o pega `/share/Public/swissjob/docker-compose.qnap.yml`
4. **Application name**: `swissjob`
5. Apply.

Container Station leerá el YAML, creará la network y volúmenes, y arrancará
los contenedores en orden de `depends_on`.

**Tiempo de primer arranque**: ~5 min. La parte lenta es el backend cargando
el modelo embedding por primera vez (luego queda en `hfcache`).

### 3.7 Verificación post-deploy

```bash
docker ps --filter "name=swissjob"
# Esperado: 5 contenedores Up. Backend, postgres, redis y frontend con
# (healthy). El worker no tiene healthcheck explícito.

docker logs swissjob-backend 2>&1 | tail -20
# Esperado al final:
#   [entrypoint] Aplicando migraciones Alembic...
#   INFO alembic.runtime.migration ... (varias líneas)
#   [entrypoint] Arrancando: gunicorn ...
#   Preloading embedding model...
#   Embedding model loaded
#   Application startup complete.
#   "GET /health HTTP/1.1" 200
```

Desde un dispositivo de la tailnet (Mac, iPhone, …):

1. Abrir http://capsule.tailebc81d.ts.net:4000
2. **Register** para crear el primer usuario
3. **Profile** → subir tu CV (PDF/DOCX)
4. **Matches** → "Find new matches" (puede dar 0 hasta que se ejecute el primer
   scheduler; ver "Forzar fetch inmediato" en sección 4)

---

## 4. Operaciones cotidianas

> Recordatorio: por SSH al QNAP `docker compose` NO funciona para usuarios
> normales (ver sección 0.3). Usa **Container Station UI** para `up`/`down`/
> `recreate`. Los siguientes `docker` directos sí funcionan por SSH.

### Logs en vivo

```bash
docker logs -f swissjob-backend
docker logs -f swissjob-worker
docker logs -f swissjob-frontend
```

### Estado del stack

```bash
docker ps --filter "name=swissjob"
docker stats --no-stream  # CPU/RAM por contenedor
```

### Restart selectivo

```bash
docker restart swissjob-backend swissjob-worker
# o desde Container Station UI: Applications → swissjob → contenedor → Restart
```

### Forzar fetch inmediato (no esperar al scheduler)

```bash
docker exec swissjob-backend python -c "
from celery_app import celery_app
celery_app.send_task('tasks.scraping.fetch_scrapers')
celery_app.send_task('tasks.fetch_providers')
"
# Mira el progreso en:
docker logs -f swissjob-worker
```

### Conectar a la BD

```bash
# Shell interactivo psql
docker exec -it swissjob-postgres psql -U swissjob swissjobhunter

# Query one-shot
docker exec swissjob-postgres psql -U swissjob swissjobhunter -c \
  "SELECT COUNT(*) FROM jobs;"
```

### Parar todo (manteniendo datos)

Container Station UI → Applications → swissjob → **Stop**. Los volúmenes
`pgdata`, `redisdata` y `hfcache` persisten.

### Reset total (BORRA datos)

Container Station UI → Applications → swissjob → **Remove** marcando
"Remove volumes". Por SSH si tienes acceso al daemon:

```bash
docker volume rm swissjob_pgdata swissjob_redisdata swissjob_hfcache
```

---

## 5. Actualización de versión

> ### 🚦 GATE de subida — ¿está aplicada YA en el NAS la canonización de identidad?
>
> **Estado al 2026-08-28: NO.** El NAS corre imágenes anteriores al 2026-08-27 y su
> base tiene la identidad legacy **sin canonizar**. En local se hizo el 2026-08-27
> (commit `2462717`); el acta con cifras está en
> [`jobhunt_core/shadow/RUNBOOK.md` §7](../jobhunt_core/shadow/RUNBOOK.md).
>
> **Mientras este gate diga NO, la ÚNICA secuencia autorizada para subir imágenes es
> la §5.3, entera y en su orden.** La actualización rutinaria de §5.4 queda
> **prohibida**: empieza por recrear la aplicación, y eso arranca los escritores del
> código nuevo sobre identidades viejas. El código nuevo ya emite la identidad
> canónica, así que esa primera cosecha produce **a la vez pérdida silenciosa de
> ofertas y duplicación del corpus** — dos ramas distintas cuyo peor caso es su suma
> (razonamiento medido en el encabezado de
> `backend/scripts/g3_canonizacion_identidad_arbeitnow_jobgether.sql`).
>
> **Este documento tenía el orden al revés** (auditoría externa R2 P1-3): mandaba
> recrear en §5.3 y solo después, en §5.4, avisaba de que la canonización exige los
> workers parados. `Recreate` de Container Station levanta solos `swissjob-worker` y
> `swissjob-core-worker` (`restart: unless-stopped` en `docker-compose.qnap.yml`), así
> que seguir el único orden ejecutable documentado abría exactamente la ventana que el
> propio documento declaraba obligatorio evitar. El orden lo fija ahora una guarda
> ejecutable de la suite: `jobhunt_core/tests/test_deploy_order.py`. Y como sus
> postcondiciones seguían siendo prosa que no paraba nada (R3 P1-1), la secuencia la
> ejecuta `backend/scripts/nas_cutover.sh`, cuyo fallo cerrado demuestra etapa por etapa
> `jobhunt_core/tests/test_deploy_cutover_failclosed.py`.

### 5.1 Build local + tar

```bash
cd ~/Public/SwissJob

# Si solo cambió backend
docker build -t swissjob-backend:prod -f backend/Dockerfile.prod backend/
docker save swissjob-backend:prod -o /tmp/swissjob/swissjob-backend.tar

# Si solo cambió frontend (recompilar tras editar /frontend)
docker build -t swissjob-frontend:prod -f frontend/Dockerfile.prod frontend/
docker save swissjob-frontend:prod -o /tmp/swissjob/swissjob-frontend.tar

# El core va SIEMPRE con su SHA horneado (§1.1): sin el build arg la imagen
# hornea `unknown` y las sondas responden `authoritative: false`.
RELEASE_SHA=$(git rev-parse --short HEAD) \
  docker compose -f docker-compose.prod.yml build core-migrate
docker save swissjob-core:prod -o /tmp/swissjob/swissjob-core.tar
```

⚠ `docker-compose.prod.yml` construye el backend con `INSTALL_BROWSERS=false`. El
worker del NAS ejecuta los scrapers Playwright: si rebuildeas el backend desde ahí,
constrúyelo con `INSTALL_BROWSERS=true` (ver el comentario del servicio `worker` en
`docker-compose.qnap.yml`).

### 5.2 Transferir al NAS — solo COPIAR (aquí no se carga ni se arranca nada)

```bash
scp /tmp/swissjob/swissjob-backend.tar  Ricardo@capsule:/share/Public/swissjob/
scp /tmp/swissjob/swissjob-frontend.tar Ricardo@capsule:/share/Public/swissjob/
scp /tmp/swissjob/swissjob-core.tar     Ricardo@capsule:/share/Public/swissjob/

# El cuerpo ejecutable de §5.3 y las dos copias SQL: sin ellos no hay maniobra.
ssh Ricardo@capsule 'mkdir -p /share/Public/swissjob/scripts'
scp backend/scripts/nas_cutover.sh \
    backend/scripts/g3_canonizacion_identidad_arbeitnow_jobgether.sql \
    backend/scripts/g6_canonizacion_identidad_irishjobs.sql \
    Ricardo@capsule:/share/Public/swissjob/scripts/
ssh Ricardo@capsule 'chmod +x /share/Public/swissjob/scripts/nas_cutover.sh'
```

### 5.3 Secuencia única de mantenimiento (canonización + subida)

Una sola maniobra, siete pasos, en este orden. **Cada paso tiene una postcondición que
es una condición de EJECUCIÓN, no una nota: si no cuadra, el procedimiento SALE distinto
de cero y no hay paso siguiente** — todos los pasos anteriores al 4 son reversibles y el
4 no lo es.

**El cuerpo ejecutable de esta sección es `backend/scripts/nas_cutover.sh`**, versionado
con el repo y copiado al NAS en §5.2. Cada paso de abajo documenta lo que el script hace
y **qué asertaría para parar**; los bloques de comandos son los que el script ejecuta,
no una segunda copia que haya que teclear. Antes existían como prosa y una auditoría
externa (R3 P1-1) demostró ejecutándolos que ninguno paraba nada: con los cinco
escritores vivos la postcondición del Paso 1 salía `0`, con el volcado roto quedaba un
`.gz` válido y vacío, y con la segunda copia SQL abortada tras confirmar la primera la
secuencia seguía adelante — el estado que este mismo documento declara irreparable.

```bash
ssh Ricardo@capsule
cd /share/Public/swissjob/scripts

./nas_cutover.sh cutover     # Pasos 1–6. Para en la primera desviación.
# → Container Station UI → Applications → swissjob → ⋮ → Recreate (Paso 7)
./nas_cutover.sh smoke       # Paso 7. Solo entonces se abre a los usuarios.
```

**Ensáyala antes sobre una restauración desechable del backup del NAS**, no sobre la
base viva: restaura el `.sql.gz` de §7 en una base nueva (`swissjob_ensayo`) y recorre
los pasos 4, 5 y 6 contra ella:

```bash
ENSAYO=1 PG_DB=swissjob_ensayo \
  CORE_DSN=postgresql+asyncpg://jobhunt_core:…@postgres:5432/swissjob_ensayo \
  ./nas_cutover.sh cutover
```

`ENSAYO=1` se salta los pasos 1 y 3 (no para producción ni recarga imágenes) y **exige**
`CORE_DSN`: el módulo del Paso 5 saca su DSN de los `--env-file`, que apuntan a la base
viva, así que un «ensayo» sin esa variable escribiría en producción.

**Cómo se comprueba ese aislamiento** (auditoría externa R4 P1-1 — la guarda anterior
comparaba el DSN por SUFIJO, así que `…/swissjobhunter?ssl=require` la esquivaba y
llegaba al Paso 5 **en firme** sobre la base viva; con `#fragmento` y con
percent-encoding, igual):

1. el DSN se **parsea**, y solo se admite UNA forma:
   `esquema://[usuario[:clave]@]host[:puerto]/base[?parámetros]`. Se rechazan los
   fragmentos, las rutas de más de un segmento, los esquemas que no son de PostgreSQL y
   los parámetros que pueden **redefinir el destino** (`dbname`, `host`, `service`…: solo
   se permiten `ssl*`, `connect_timeout`, `application_name`, `target_session_attrs`);
2. el nombre de base se **decodifica** y tiene que ser exactamente `PG_DB`, distinto de
   `PG_DB_PROD`; el host, exactamente `CORE_DSN_HOST` (por defecto `postgres`);
3. y **antes de la primera escritura** —con `ENSAYO=1` y sin él— el script ejecuta una
   **sonda de destino**: pide a `psql` y al módulo `jobhunt_core.shadow.identidad_destino`
   —que resuelve el DSN igual que el one-shot del Paso 5— la identidad
   `base|oid|arranque del postmaster en UTC`, y para si no coinciden. Una cadena bien
   formada no demuestra a qué base se conecta el proceso; esto sí.

**Las cifras del NAS serán distintas a las locales** (otro corpus): el script las MIDE
allí y aserta contra lo que él mismo midió — no lleva ninguna constante de corpus. Las
del acta local sirven para reconocer la FORMA del resultado, nunca como valor esperado.

Ventana estimada: 20–40 min con la aplicación caída. Por SSH `docker compose` no
funciona (§0.3): todo va con `docker` directo o por la UI de Container Station.

#### Paso 1 — Detener todo escritor y proyector

`docker stop` es suficiente y no lo revierte `restart: unless-stopped`: esa política
NO rearranca lo que se paró a mano (a diferencia de `always`). `swissjob-postgres` y
`swissjob-redis*` se quedan EN MARCHA — la maniobra los necesita.

```bash
docker stop swissjob-backend swissjob-worker \
            swissjob-core-api swissjob-core-worker swissjob-core-capture
```

- `swissjob-backend` lleva dentro el scheduler APScheduler; el local se dejó vivo
  porque nadie usaba la web, pero aquí se para: su API también escribe.
- `swissjob-core-worker` lleva el **beat embebido** — es el proyector de la sombra.
- `swissjob-core-capture` escribe en `jobhunt.shadow_change_log`. Parado, el slot
  `jobhunt_shadow` **retiene WAL**: otra razón para que la ventana sea corta. Al
  rearrancar lo reproduce (RPO=0).

**Aserción** — ninguno de los cinco puede seguir en `docker ps`. Se comprueba la
presencia, no la palabra `Up`: un contenedor `Restarting` tampoco está parado y también
escribiría. Si alguno sigue vivo, el script sale 1 y no hay Paso 2.

#### Paso 2 — Copia de seguridad, con `public` **y** `jobhunt`

El dump por defecto de §7 se hace con el rol `swissjob`; si ese rol no puede leer el
esquema `jobhunt`, el backup no sirve para esta maniobra y hay que repetirlo con la
credencial de `/share/Public/swissjob/.env.core.admin.prod`.

```bash
docker exec swissjob-postgres \
  pg_dump -U swissjob -n public -n jobhunt swissjobhunter > "$crudo"
gzip -c "$crudo" > "$parcial" && gzip -t "$parcial"
```

**Aserción** — el volcado va a un fichero TEMPORAL y solo gana su nombre definitivo
(`pre_canonizacion_<sello>.sql.gz`) si `pg_dump` devolvió 0, el `.gz` pasa `gzip -t` y
contiene tablas de los DOS esquemas. Sin `-t` en `docker exec` (un TTY reescribe los
saltos de línea del volcado) y **sin tubería**: `pg_dump | gzip` devuelve el estado de
`gzip` y aceptaba un `.gz` válido y vacío.

#### Paso 3 — Cargar la imagen nueva SIN arrancar servicios

`docker load` sustituye el tag; **no toca contenedores**, y los que escriben están
parados desde el Paso 1. No se recrea nada todavía.

```bash
docker rmi swissjob-core:prod; docker load -i swissjob-core.tar        # ídem backend y frontend
docker run --rm --entrypoint sh swissjob-core:prod -c 'cat /opt/jobhunt-release/RELEASE'
```

**Aserción** — los tres tar existen, las tres imágenes quedan cargadas y la del core
sabe nombrar su release. Si dice `unknown` se construyó sin el build arg (volver a
§5.1) y el script para: esa imagen respondería `authoritative: false`. La release
medida aquí se guarda y es la que el smoke del Paso 7 exigirá.

#### Paso 4 — Las dos copias SQL: primero en seco, luego en firme

**4a. Ensayo en seco de LAS DOS.** Los ficheros del repo terminan en `ROLLBACK;`: tal
cual son un ensayo que imprime su informe sin tocar nada.

```bash
docker exec -i swissjob-postgres psql -U swissjob -d swissjobhunter \
  -v ON_ERROR_STOP=1 -X -q -A -t -F '|' -f - < "$f" > "$salida" 2> "$salida.err"
```

**Aserción** — el estado de salida es el de `psql`, no el de nada más. El runbook
canalizaba a `tee` y `tee` devuelve 0 aunque `psql` aborte (`false | tee /dev/null` → 0),
así que el bucle continuaba. Además, si el informe dice que la fusión descartaría
`match_results` **con señal del usuario**, el script PARA: es una decisión humana y hay
que repetir a mano con `PERMITIR_SENAL_USUARIO=1` tras revisarla.

**4b. 🚫 Enclavamiento sin marcha atrás — ¿hay cohortes dedup SELLADAS afectadas?**
Las dos mitades NO comparten transacción, y la segunda aborta *fail-closed* si una
cohorte sellada tiene pares que re-mapear. Commitear la primera con la segunda
condenada a abortar deja esos `job_ref` apuntando a **otras ofertas**, y `core0025` los
hace inmutables: **no se pueden reparar**. Por eso el preflight de **las dos** copias y
esta comprobación van ANTES de confirmar la primera.

**La cifra la declara CADA ensayo en seco, no el script** (auditoría externa R4 P1-2).
Antes el script traía su propia consulta: contaba cualquier par de cohorte sellada con un
lado presente en alguna de las tres fuentes, **sin preguntar si G3/G6 iba a cambiar ese
ref**. Era un segundo oráculo, aproximado y distinto del mapa exacto que los propios SQL
calculan — y medido en local el 2026-08-28 (SOLO SELECT) contaba **67** pares mientras los
mapas de G3+G6 remapean **0** refs: el procedimiento habría parado sin motivo, y el
remedio que sugería (cargar una cohorte nueva) podía dejarlo bloqueado para siempre.

Ahora cada script emite en su informe, desde su PROPIA `g3_map`, el concepto

```text
enclavamiento: refs de cohortes SELLADAS que ESTE script remapea|<n>
```

con el mismo filtro que el guard `_require_no_frozen_affected` del módulo del Paso 5
aplica sobre su `canon_map`: cohortes **AFECTADAS**, no cohortes selladas.

**Aserción: la suma de los dos informes es cero.** Cualquier fila ⇒ el script PARA. Y si
un informe **no trae el concepto**, también para: no se confirma nada sin ese dato. La
única salida es cargar una cohorte NUEVA con los refs canónicos y retirar la vieja del
gate; el sello existe justo para que el acta no se reescriba. (En local esta consulta devuelve hoy `67`, verificado
el 2026-08-28 SOLO SELECT: sirve para ver la FORMA de la respuesta, no como valor
esperado del NAS — allí el script la mide y para si no es 0.)

**4c. En firme.** Solo si 4a y 4b salieron limpios. El `COMMIT` va en una **copia**,
nunca en el fichero versionado:

```bash
sed 's/^ROLLBACK;$/COMMIT;/' "$f" > "$f.commit.sql"   # exactamente un COMMIT;
```

**Aserción** — un solo `COMMIT;` en la copia, estado de salida de `psql` comprobado en
cada una, y las cifras del informe en firme **idénticas** a las del ensayo de 4a
(comparación automática de los informes, no un vistazo). Si la SEGUNDA falla, el
mensaje ordena RESTAURAR el volcado del Paso 2: la primera ya está confirmada. Y también se comparan las IDENTIDADES que cada script
declara (`IDENT|desaparece|…`, `IDENT|canonico|…`): si el conjunto de hashes cambia entre
el ensayo y la aplicación, el Paso 6 estaría verificando otra cosa.

#### Paso 5 — La otra mitad: `canonical_refs` con la imagen nueva, one-shot

Con los escritores **todavía parados**. Es un contenedor efímero de la imagen recién
cargada — no arranca ningún servicio. El script descubre el nombre real de la red
(Container Station la prefija con el de la aplicación) inspeccionando
`swissjob-core-migrate`, y para si no lo consigue; se puede forzar con `CORE_NET=…`.

```bash
$CORE_RUN python -m jobhunt_core.shadow.canonical_refs --dry-run   # mide, no escribe
$CORE_RUN python -m jobhunt_core.shadow.canonical_refs             # aplica
```

**Aserción** — el JSON de la aplicación cuadra con el del `--dry-run` salvo la bandera
`dry_run` (comparación automática), y una tercera pasada en seco devuelve
`filas_canonizadas_en_legacy: 0`: el módulo es idempotente y si no lo quedó, hay que
restaurar.

#### Paso 6 — Verificar antes de dejar entrar a nadie

Todo `SELECT`, con los escritores aún parados, y **con aserciones**: el script mide el
estado ANTES (justo después del Paso 3) y lo compara con el de después usando las
cifras y las **identidades** de los informes en firme del Paso 4. Nada aquí se
inspecciona a ojo.

| # | Qué mide | Aserción del script |
|---|---|---|
| a | slots huérfanos de las tres fuentes canonizadas (`source_listings` sin `jobs.hash`) | `después = antes + Σ «sombra: slots de clones»`. Sube por los clones borrados, y **solo** por ellos: si subiera en miles sería el fallo que el PASO 7c de los scripts evita |
| b | `labeled_judgments` y cuántos resuelven a un `source_listing` legacy | `resuelven = juicios`: ningún juicio puede dejar de resolver |
| c | pares con sus DOS refs resueltos | no puede BAJAR respecto a lo medido antes |
| d | `count(*)` de `public.jobs` | `después = antes − Σ «clones fusionados»` |
| **b′** | **manifiesto ordenado de `set_id\|job_ref` que resuelven** | ningún juicio que resolvía ANTES puede dejar de resolver — **por identidad** |
| **c′** | **manifiesto ordenado de `pair_id` con sus dos refs resueltos** | ningún par que resolvía ANTES puede dejar de resolver — **por identidad** |
| **e** | los `IDENT\|desaparece\|<hash>` que declararon los dry-runs | ninguno puede seguir en `public.jobs` |
| **f** | los `IDENT\|canonico\|<hash>` que declararon los dry-runs | todos tienen que existir en `public.jobs` |

**Por qué b′/c′/e/f y no solo a–d** (auditoría externa R4 P1-3). Las cuatro primeras
comparan **cantidades**, y una pérdida se compensa con una ganancia distinta. Reproducido
el 2026-08-28 en una base desechable: mutando las `source_listings` de un par positivo
conocido (`pair_duplicate_A_B`) a otro par (`pair_distinct_C_D`), las cuatro fórmulas
salen idénticas —`SLOTS 0→0`, `JOBS 4→4`, `PARES 1→1`, `JUICIOS 0→0`— mientras el par que
importaba **deja de resolver**. Con el manifiesto por identidad, `comm -23` sobre los dos
ficheros ordenados nombra exactamente `pair_duplicate_A_B`.

Los manifiestos se guardan ordenados con la colación de C (`LC_ALL=C sort`) para que la
comparación sea determinista, y una copia viaja junto al backup: son también lo que
`restaurar` usa para comprobar la vuelta atrás.

Las consultas están en el script y verificadas ejecutándolas (SOLO `SELECT`) contra la
base local el 2026-08-28. **Sus resultados en el NAS serán otros**: los mide allí.
Cualquier desviación ⇒ el script PARA y el remedio es **restaurar el dump del
Paso 2** antes de arrancar nada.

#### Paso 7 — Recrear y smoke (y solo ahora)

Container Station UI → Applications → swissjob → **⋮ menú** → **Recreate**. Mantiene
los volúmenes (`pgdata`, `redisdata`, `hfcache`, `core_hf_cache`): Alembic aplica lo
que falte al arrancar y el modelo de embeddings no se vuelve a descargar (<60 s).

```bash
./nas_cutover.sh smoke
```

**Aserción** — los cinco escritores vuelven a estar `Up` y `/v1/ready` cumple las
cuatro condiciones a la vez, o el script sale 1:

- `status: ready` — **no `ok`**. La postcondición decía `ok` y la API contractual
  devuelve `ready` desde siempre (R3 P2-1): un cutover correcto habría terminado en
  falso rojo justo después de la parte irreversible. La constante vive en
  `jobhunt_core/api/main._READY_STATUS`, el script la lleva en `READY_STATUS_ESPERADO`
  y `jobhunt_core/tests/test_deploy_order.py` impide que diverjan.
- `authoritative: true` — si es `false`, §1.1 dice qué mirar; **no** es autorización
  para operar mientras siga en false.
- `alembic` igual al `alembic_expected` que publica `/v1/health` del mismo proceso
  (medido allí, no copiado aquí).
- `release` igual al `RELEASE` horneado que se leyó en el Paso 3.

Después imprime `docker logs --tail 50` de `swissjob-core-capture` (reproduce el WAL
retenido y alcanza el slot) y de `swissjob-worker`. Con el smoke en verde el gate de la
cabecera de §5 pasa a **SÍ** y las siguientes subidas usan §5.4.

### 5.4 Actualización rutinaria — SOLO con el gate de §5 en SÍ

§5.1 → §5.2 → cargar las imágenes (`docker load`) → Container Station UI →
Applications → swissjob → **⋮ menú** → **Recreate** → el smoke del Paso 7.

Mantiene los volúmenes; migraciones nuevas se aplican solas en el arranque (Alembic
en `backend`, `core-migrate` en el core). **Tiempo esperado**: <60 s gracias a
`hfcache`.

---

## 6. Troubleshooting

| Síntoma | Causa probable | Acción |
|---|---|---|
| `Failed to create application "swissjob". ... container swissjob-backend is unhealthy` y al ver logs el último mensaje es `[entrypoint] Aplicando migraciones Alembic...` con traceback `asyncpg.exceptions.InvalidPasswordError` | `alembic.ini` con `sqlalchemy.url` hard-coded, o desincronización entre `POSTGRES_PASSWORD` y `DATABASE_URL` en `.env.prod`, o YAML del compose con literal distinto del `.env.prod` | 1. Verifica que la imagen tiene el fix: `docker run --rm --entrypoint sh swissjob-backend:prod -c 'grep sqlalchemy.url alembic.ini'` debe dar `driver://user:pass@localhost/dbname`. Si no, rebuild. 2. `grep -E "^POSTGRES_\|^DATABASE_URL" .env.prod` — passwords coinciden. 3. `grep environment docker-compose.qnap.yml` — no debe haber password literal en postgres. |
| Backend `unhealthy` >90s tras arranque limpio | Ya NO debería pasar por el modelo (carga en background desde 2026-07-18). Si ocurre, es otra cosa (BD, Redis) | `docker logs -f swissjob-backend`: `Application startup complete.` debe salir en segundos; `Embedding model warmed up` llega después sin bloquear. Si el startup no completa, mira BD/Redis. |
| Frontend devuelve 502 | Backend aún no healthy | `docker logs swissjob-backend` y esperar. Tras pasar healthy, refrescar. |
| Postgres exited al arrancar | Permisos volumen o init-pgvector inaccesible | Verifica que `/share/Public/swissjob/docker/postgres/init-pgvector.sql` existe y es legible. |
| Worker no procesa jobs (queue crece) | Redis no conecta o tarea falla | `docker logs swissjob-worker` — buscar tracebacks. Verificar `redis-cli ping` desde dentro: `docker exec swissjob-worker redis-cli -h redis ping`. |
| 0 jobs en Matches tras 6h | Scheduler dispatcha pero scrapers o providers fallan | `docker logs swissjob-worker` filtrar por errores de provider/scraper. Probablemente `CircuitBreaker` abierto en alguno. |
| `unknown shorthand flag: 'f' in -f` en SSH | `docker compose` no disponible para tu usuario | Usa Container Station UI para `up`/`down`/`recreate`. Para todo lo demás, comandos `docker` directos (ver sección 4). |
| `WARNING: Error loading config file: ... permission denied` | Cosmético — Docker CLI no lee config del usuario | Ignorable. Los comandos funcionan. |
| Healthcheck Container Station rojo pero `/health` responde 200 | Caché de Container Station tras un `up` fallido | Stop + Recreate desde UI. |
| `Error response from daemon: ... bind: address already in use` en swissjob-frontend | Puerto del host ya en uso por otro servicio QNAP | `ss -tlnp \| grep :<puerto>` para identificar. Actualmente el frontend está mapeado a `4000:80` porque 8080 y 8090 están ocupados por QTS. Si 4000 también se ocupa, cambia a otro y actualiza `BACKEND_CORS_ORIGINS` en `.env.prod` (incidencia #7). |

---

## 7. Backups

### Backup manual

```bash
mkdir -p /share/Public/backups/swissjob

docker exec -t swissjob-postgres \
  pg_dump -U swissjob swissjobhunter \
  | gzip > /share/Public/backups/swissjob/db-$(date +%Y%m%d-%H%M).sql.gz
```

### Backup automático (semanal)

QNAP Control Panel → Tareas Programadas → Crear "User defined script" semanal
con el comando de arriba. Conserva las últimas N copias con:

```bash
find /share/Public/backups/swissjob -name 'db-*.sql.gz' -mtime +90 -delete
```

### Restore

```bash
gunzip < /share/Public/backups/swissjob/db-YYYYMMDD-HHMM.sql.gz \
  | docker exec -i swissjob-postgres psql -U swissjob swissjobhunter
```

⚠ Antes de restaurar es recomendable hacer un backup del estado actual y
parar el backend (`docker stop swissjob-backend swissjob-worker`).

---

## 8. Recursos esperados

- **CPU**: <10% idle. Picos cuando corre matching (LLM rerank vía Groq), o
  scraping con Playwright (Chromium headless).
- **RAM** (steady state):
  - postgres: ~150 MB
  - redis: ~50-200 MB
  - backend: ~1.8-2.0 GB (el modelo embedding pesa ~500 MB en memoria, más
    2 workers gunicorn)
  - worker: ~600 MB
  - frontend (nginx): ~10 MB
  - **TOTAL**: ~2.5-3 GB
- **Disco**:
  - Imágenes Docker: ~4 GB (backend con Playwright es pesado)
  - `pgdata`: crece ~50-100 MB/semana
  - `redisdata`: <100 MB
  - `hfcache`: ~120 MB constante
- **Red**: Tailscale ~50-100 KB/s en uso normal, picos durante fetch/scraping.

---

## 9. Apéndice — Bitácora de incidencias resueltas

> Registro histórico de los problemas reales encontrados en el despliegue
> de primera puesta en producción (mayo-junio 2026). Documenta el síntoma,
> el log real, el diagnóstico y la fix aplicada. Sirve como referencia
> si vuelven a aparecer.

### Incidencia #1 — `InvalidPasswordError` por desincronización de credenciales

**Fecha**: 2026-05-31. **Estado**: ✅ resuelto.

**Síntoma en Container Station**:
```
Failed to create application "swissjob". Error message: operateApp action
[--project-name swissjob up -d --remove-orphans] failed: exit status 1:
... Container swissjob-postgres Healthy
Container swissjob-backend Starting
Container swissjob-backend Waiting
Container swissjob-backend Error
Container swissjob-backend Error dependency failed to start:
container swissjob-backend is unhealthy
```

**Log real del backend** (último `docker logs swissjob-backend`):
```
File "/usr/local/lib/python3.12/site-packages/asyncpg/connect_utils.py", line 1102, in __connect_addr
    await connected
asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "swissjob"
[entrypoint] Aplicando migraciones Alembic...
```

**Causa raíz**: dos passwords distintos para Postgres conviviendo en el stack:

1. `docker-compose.qnap.yml` original tenía `POSTGRES_PASSWORD: "DkJMA6gl..."`
   literal en `environment:`.
2. `.env.prod` tenía `POSTGRES_PASSWORD=-dcOY0u_-...` distinto.

Postgres se inicializaba con (1), backend conectaba con (2) → mismatch.

**Fix**: el servicio `postgres` ahora usa `env_file:` (igual que backend y
worker) para garantizar fuente única. Eliminado el `environment:` literal.

```yaml
postgres:
  env_file:
    - /share/Public/swissjob/.env.prod    # antes: environment con valores literales
```

---

### Incidencia #2 — Container Station no expande `${VARS}` del compose

**Fecha**: 2026-05-31. **Estado**: ✅ resuelto.

**Síntoma**: el `docker-compose.prebuilt.yml` original usaba sustituciones
del compose:

```yaml
postgres:
  environment:
    POSTGRES_USER: ${POSTGRES_USER}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    POSTGRES_DB: ${POSTGRES_DB}
```

Container Station QNAP NO ejecuta el preprocessor de Docker Compose CLI; lee
el YAML literal. Esas sustituciones quedan vacías al inicializar Postgres,
que falla al arranque o se inicializa con credenciales por defecto.

**Verificación**:
```bash
docker exec swissjob-postgres sh -c 'echo USER=$POSTGRES_USER PWD=$POSTGRES_PASSWORD'
# Sin la fix: USER= PWD=
```

**Fix**: sustituir `environment: ${VARS}` por `env_file:` con path absoluto.
En healthchecks usar `$$VAR` (doble dólar) para que el shell del contenedor
resuelva la variable en runtime:

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
```

---

### Incidencia #3 — `alembic.ini` con `sqlalchemy.url` hard-coded

**Fecha**: 2026-06-01. **Estado**: ✅ resuelto (requirió rebuild de imagen).

**Síntoma**: tras resolver #1 y #2, Postgres ya arrancaba con el password
correcto (`docker exec swissjob-postgres env | grep PASSWORD` mostraba el
valor del `.env.prod`), pero el backend seguía dando exactamente el mismo
error en el log que la incidencia #1.

**Verificación que descartó cualquier otra causa**:
```bash
# Postgres OK con el password esperado
$ docker exec swissjob-postgres sh -c 'echo PWD=$POSTGRES_PASSWORD'
PWD=DkJMA6glrp1DL2tN8WnKp8iLyf7FFpPv

# .env.prod consistente (POSTGRES_PASSWORD y DATABASE_URL idénticos)
$ grep -E "^POSTGRES_|^DATABASE_URL" .env.prod
POSTGRES_USER=swissjob
POSTGRES_PASSWORD=DkJMA6glrp1DL2tN8WnKp8iLyf7FFpPv
POSTGRES_DB=swissjobhunter
DATABASE_URL=postgresql+asyncpg://swissjob:DkJMA6glrp1DL2tN8WnKp8iLyf7FFpPv@postgres:5432/swissjobhunter

# .env.prod limpio, sin CRLF ni BOM
$ cat -A .env.prod | grep PASSWORD
POSTGRES_PASSWORD=DkJMA6glrp1DL2tN8WnKp8iLyf7FFpPv$       ← termina en $ (LF puro)
```

**Causa raíz**: el `entrypoint.sh` del backend ejecuta `alembic upgrade head`
ANTES de gunicorn. Alembic leía `sqlalchemy.url` del fichero `alembic.ini`:

```ini
# backend/alembic.ini (versión original con el bug)
sqlalchemy.url = postgresql+asyncpg://swissjob:swissjob_dev_2024@postgres:5432/swissjobhunter
```

→ Alembic ignoraba la env var `DATABASE_URL` y usaba el password hard-coded
de desarrollo. En local funcionaba porque el postgres de dev usa también
`swissjob_dev_2024`, pero en producción siempre falla.

**Fix** (requiere rebuild del backend):

[backend/alembic/env.py](../backend/alembic/env.py):
```python
from config import settings

# Override del sqlalchemy.url del .ini con la URL que viene de pydantic-settings
# (env vars > .env > defaults). Una sola fuente de verdad.
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```

[backend/alembic.ini](../backend/alembic.ini):
```ini
# antes: sqlalchemy.url = postgresql+asyncpg://swissjob:swissjob_dev_2024@...
sqlalchemy.url = driver://user:pass@localhost/dbname   # placeholder inocuo
```

**Verificación post-rebuild**:
```bash
docker run --rm --entrypoint sh swissjob-backend:prod \
  -c 'grep sqlalchemy.url alembic.ini; grep set_main_option alembic/env.py'

# Debe salir:
#   sqlalchemy.url = driver://user:pass@localhost/dbname
#   config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
```

---

### Incidencia #4 — `docker compose` no disponible por SSH

**Fecha**: 2026-06-01. **Estado**: ✅ workaround documentado.

**Síntoma**:
```
[Ricardo@Capsule swissjob]$ docker compose -f docker-compose.qnap.yml down
WARNING: Error loading config file: open /share/CACHEDEV1_DATA/.qpkg/container-station/homes/Ricardo/.docker/config.json: permission denied
unknown shorthand flag: 'f' in -f
See 'docker --help'.
```

**Causa**: el plugin `compose` (marcado `compose*` en `docker --help` con
asterisco = external plugin) vive en
`/share/CACHEDEV1_DATA/.qpkg/container-station/homes/<user>/.docker/config.json`
con permisos restringidos. Sin ese config, Docker no descubre el plugin.

**Workaround**: usar Container Station UI para `up`/`down`/`recreate`. Para
todo lo demás (logs, exec, load, restart de un contenedor concreto, etc.) los
comandos `docker` directos sí funcionan. Ver sección 4.

---

### Incidencia #5 — Healthcheck del backend insuficiente para el primer arranque

**Fecha**: 2026-06-01. **Estado**: ✅ resuelto.

**Síntoma tras resolver #1, #2 y #3**: las migraciones Alembic se aplicaban
correctamente y el backend arrancaba, pero Container Station seguía marcando
el up como fallido. Sin embargo, `docker logs` confirmaba que el backend
estaba vivo y `/health` respondía 200.

**Cronología real** (timestamps del log):
```
13:57:49 — [entrypoint] Aplicando migraciones Alembic...
13:57:49 — INFO alembic.runtime.migration Running upgrade ... (19 migraciones, OK)
13:57:49 — [entrypoint] Arrancando: gunicorn main:app -w 2 -k uvicorn.workers.UvicornWorker
13:57:49 — Listening at: http://0.0.0.0:8000
13:57:49 — Booting worker with pid: 80
13:57:49 — Booting worker with pid: 81
...   ~4 min 40 s de carga del modelo embedding (silencio en logs)
14:02:27 — Application startup complete.
14:02:27 — "GET /health HTTP/1.1" 200
14:02:28 — Worker (pid:171) exited with code 23    ← reaper de gunicorn
```

**Análisis**:
- El healthcheck del YAML era `interval: 30s, retries: 3, start_period: 90s`
  → 90 + 3×30 = **180s** antes de abortar el up.
- El backend tardó **278s** en `Application startup complete.` (descarga del
  modelo embedding `paraphrase-multilingual-MiniLM-L12-v2`, ~120 MB).
- Container Station abortaba el up a los 180s, pero el backend seguía vivo.
- El `Worker pid 171 exited with code 23` fue colateral: el master gunicorn
  forkó un reemplazo creyendo que los workers originales estaban colgados,
  pero estos completaron a tiempo y el reemplazo se descartó. **No es un
  crash recurrente** — solo 2 líneas idénticas duplicadas por el reaper.

**Verificación**:
```bash
$ docker stats swissjob-backend --no-stream
CPU %     MEM USAGE / LIMIT     NET I/O         BLOCK I/O
0.39%     1.907GiB / 7.663GiB   456MB / 896kB   12.6GB / 808MB
# 456 MB de descarga = el modelo embedding bajándose en directo
```

**Fix**: subir `start_period` y `retries` para acomodar el primer arranque.

```yaml
backend:
  healthcheck:
    interval: 30s
    timeout: 5s
    start_period: 360s    # antes: 90s
    retries: 5            # antes: 3
```

→ 360 + 5×30 = **510s** de margen. Holgado para descarga + carga + boot.

---

### Incidencia #6 — Re-descarga del modelo embedding en cada Recreate

**Fecha**: 2026-06-01. **Estado**: ✅ resuelto (relacionada con #5).

**Problema**: el modelo embedding (~120 MB) vivía dentro del filesystem del
contenedor (`/home/app/.cache/huggingface`). Cada `Recreate` desde Container
Station destruía y recreaba el contenedor → re-descarga + re-carga (4-5 min)
en cada despliegue.

**Fix**: volumen Docker persistente compartido entre backend y worker:

```yaml
backend:
  volumes:
    - hfcache:/home/app/.cache/huggingface

worker:
  volumes:
    - hfcache:/home/app/.cache/huggingface   # compartido

volumes:
  hfcache:
    driver: local
```

**Resultado**:
- **Primer arranque** (volumen vacío): 4-5 min (descarga + carga).
- **Recreates posteriores**: <60 s (carga directa del volumen).

---

### Incidencia #7 — Puerto del frontend ocupado por servicios internos de QTS

**Fecha**: 2026-06-01. **Estado**: ✅ resuelto.

**Síntoma**: tras resolver #5 y #6 (backend healthy, worker started), Container Station falla al levantar el frontend:

```
Container swissjob-frontend Starting
Error response from daemon: driver failed programming external connectivity
on endpoint swissjob-frontend (...): failed to bind port 0.0.0.0:8080/tcp:
listen tcp4 0.0.0.0:8080: bind: address already in use
```

**Causa raíz**: el host del NAS ya tiene otros servicios QTS escuchando en los puertos comunes. Verificamos en orden:

- **8080**: ocupado (panel interno de QTS).
- **8090**: ocupado (otro panel interno).
- **4000**: libre.

QNAP usa abundantemente el rango 8000-9000 según los paquetes instalados (Helpdesk, QMailAgent, etc.). Elegir un puerto "no obvio" reduce probabilidades de colisión.

**Diagnóstico**:
```bash
docker ps --filter "publish=<puerto>" --format 'table {{.Names}}\t{{.Image}}\t{{.Ports}}'
ss -tlnp 2>/dev/null | grep :<puerto>   # o netstat -tlnp si ss no está
```

**Fix aplicada**: puerto del frontend a **4000** en los tres compose files y `BACKEND_CORS_ORIGINS` del `.env.prod`.

```yaml
frontend:
  ports:
    - "4000:80"   # antes: "8080:80"
```

```env
BACKEND_CORS_ORIGINS=["http://capsule.tailebc81d.ts.net:4000"]
```

**Acceso final**: `http://capsule.tailebc81d.ts.net:4000`.

---

### Resumen — cambios netos en el código

| Fichero | Cambio | Razón |
|---|---|---|
| [backend/alembic/env.py](../backend/alembic/env.py) | `config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)` | #3 |
| [backend/alembic.ini](../backend/alembic.ini) | Placeholder inocuo | #3 |
| [docker-compose.qnap.yml](../docker-compose.qnap.yml) | `env_file:` postgres + `$$VAR` en healthcheck + `start_period: 360s` + `hfcache` | #1, #2, #5, #6 |
| [docker-compose.prod.yml](../docker-compose.prod.yml) | Mismo conjunto de fixes | #1, #2, #5, #6 |
| [docker-compose.prebuilt.yml](../docker-compose.prebuilt.yml) | Mismo conjunto de fixes | #1, #2, #5, #6 |

Todas las incidencias están además resumidas en la memoria persistente de
Claude Code (fuera del repo, en
`~/.claude/projects/-home-lothar-Public-SwissJob/memory/qnap_container_station.md`)
para que el asistente las recuerde en futuras sesiones sin tener que releer
este doc completo.

