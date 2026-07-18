# Despliegue — SwissJobHunter

> Guía completa de despliegue. Pensada para QNAP NAS con Container Station +
> Tailscale, pero documenta también los flujos alternativos (dev local y
> build-en-NAS). Acceso final en producción:
> `http://capsule.tailebc81d.ts.net:4000` desde cualquier dispositivo
> de la tailnet (Mac, Linux, iPhone, iPad).

---

## Índice

- [0. Particularidades de QNAP Container Station — LEE ESTO PRIMERO](#0-particularidades-de-qnap-container-station--lee-esto-primero)
- [1. Variantes del compose](#1-variantes-del-compose)
- [2. Arquitectura del stack](#2-arquitectura-del-stack)
- [3. Despliegue desde cero en el NAS — paso a paso](#3-despliegue-desde-cero-en-el-nas--paso-a-paso)
- [4. Operaciones cotidianas](#4-operaciones-cotidianas)
- [5. Actualización de versión](#5-actualización-de-versión)
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

> **Para Container Station usa siempre `docker-compose.qnap.yml`.** Es el único
> testeado con los gotchas de la sección 0.

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
  - `default`: fetchs cada 6h de los 16 providers vía API.
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

Después de cambios en el código:

### 5.1 Build local + tar

```bash
cd ~/Public/SwissJob

# Si solo cambió backend
docker build -t swissjob-backend:prod -f backend/Dockerfile.prod backend/
docker save swissjob-backend:prod -o /tmp/swissjob/swissjob-backend.tar

# Si solo cambió frontend (recompilar tras editar /frontend)
docker build -t swissjob-frontend:prod -f frontend/Dockerfile.prod frontend/
docker save swissjob-frontend:prod -o /tmp/swissjob/swissjob-frontend.tar
```

### 5.2 Transfer + load

```bash
# Local
scp /tmp/swissjob/swissjob-backend.tar  Ricardo@capsule:/share/Public/swissjob/
scp /tmp/swissjob/swissjob-frontend.tar Ricardo@capsule:/share/Public/swissjob/

# SSH al NAS
ssh Ricardo@capsule
cd /share/Public/swissjob

docker rmi swissjob-backend:prod 2>/dev/null
docker load -i swissjob-backend.tar

docker rmi swissjob-frontend:prod 2>/dev/null
docker load -i swissjob-frontend.tar
```

### 5.3 Recreate

Container Station UI → Applications → swissjob → **⋮ menú** → **Recreate**.

Mantiene los volúmenes (`pgdata`, `redisdata`, `hfcache`) → migraciones nuevas
se aplican automáticamente en el arranque del backend (Alembic) y el modelo
embedding NO se vuelve a descargar.

**Tiempo esperado**: <60s gracias a `hfcache`.

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

