# Despliegue en QNAP NAS — SwissJobHunter

> Guía operativa para desplegar el stack en QNAP Container Station con acceso
> via Tailscale. Acceso final: `http://capsule.tailebc81d.ts.net:8080`
> desde cualquier dispositivo de la tailnet (incluido el iPhone).

---

## 1. Pre-requisitos en el NAS

- **Container Station 3.x** instalado y operativo
- **Tailscale** ya activo (el usuario lo confirmó — `capsule.tailebc81d.ts.net`)
- **Espacio libre**: ~3 GB para imágenes + ~1-2 GB para BD/Redis a medio plazo
- **RAM disponible**: ~1.5-2 GB durante operación normal
- **Acceso SSH** al NAS habilitado (opcional, facilita logs/backups)

---

## 2. Subir el código al NAS

Hay dos formas — elige según gusto.

### Opción A — Shared folder + File Station
1. Crear carpeta compartida en el NAS: por ejemplo `/share/Container/swissjob`
2. Desde la máquina dev:
   ```bash
   cd /home/lothar/Public/SwissJob
   # Excluye .git, node_modules, frontend/dist y los .env locales
   rsync -av --exclude='.git' --exclude='node_modules' \
         --exclude='frontend/dist' --exclude='.env*' \
         --exclude='backend/.venv' --exclude='__pycache__' \
         . capsule:/share/Container/swissjob/
   ```
   *(Asume que tu hostname Tailscale del NAS es `capsule` y tienes SSH key
   configurada. Si no, usa la IP Tailscale o el upload via File Station.)*

### Opción B — git clone en el NAS
Si tienes el repo en GitHub privado, vía SSH al NAS:
```bash
ssh admin@capsule
cd /share/Container
git clone <repo-url> swissjob
cd swissjob
```

---

## 3. Generar `.env.prod` en el NAS

Por SSH al NAS o desde File Station crear `/share/Container/swissjob/.env.prod`:

```bash
ssh admin@capsule
cd /share/Container/swissjob

cp .env.prod.example .env.prod

# Generar SECRET_KEY (48 bytes urlsafe)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# → copia el output al SECRET_KEY del .env.prod

# Generar POSTGRES_PASSWORD (16 bytes urlsafe alfanumérico es suficiente)
python3 -c "import secrets; print(secrets.token_urlsafe(20))"
# → copia el output AL MISMO valor en POSTGRES_PASSWORD y en la parte
# `:password@` de DATABASE_URL

# Editar el resto (GROQ_API_KEY real, providers opcionales)
vi .env.prod
```

**Checklist `.env.prod`:**

- [ ] `SECRET_KEY` distinto del default `change-me-in-production`
- [ ] `POSTGRES_PASSWORD` cambiada y replicada dentro de `DATABASE_URL`
- [ ] `GROQ_API_KEY` rellenada (sin ella, matching pipeline funciona pero sin
      LLM re-ranking ni traducción ni generador de cartas)
- [ ] `BACKEND_CORS_ORIGINS` apunta al hostname Tailscale correcto

---

## 4. Crear el stack en Container Station

1. Abre Container Station → **Crear** → **Aplicación / Stack**
2. Modo: **Personalizado (docker-compose)**
3. Nombre del stack: `swissjob`
4. **Ruta del proyecto**: `/share/Container/swissjob`
5. **Fichero compose**: `docker-compose.prod.yml` (lo lee de esa ruta)
6. **Variables de entorno**: Container Station permite o bien pegar el
   contenido del `.env.prod`, o bien usar el fichero del filesystem.
   Recomendado: usar el fichero (`env_file: .env.prod` ya está en el
   compose), así no quedan en la UI.
7. Validar y **Crear**.

> Si Container Station se queja por el `:ro` del init-pgvector, verifica
> que la ruta `./docker/postgres/init-pgvector.sql` existe relativa al
> `docker-compose.prod.yml`. Subió bien con rsync? Si no, copia ese fichero
> a mano antes.

---

## 5. Primer arranque

```bash
# Vía SSH (también puedes hacerlo desde la UI de Container Station)
cd /share/Container/swissjob
docker compose -f docker-compose.prod.yml up -d --build
```

**Qué pasará:**

1. Postgres arranca y crea la BD vacía → la extensión `pgvector` se activa
   por el script `init-pgvector.sql`.
2. Redis arranca.
3. Backend image se construye (~5-10 min la primera vez: instala Playwright
   con dependencias del sistema).
4. Frontend image se construye (~2 min: `npm ci` + `vite build`).
5. Backend arranca → `entrypoint.sh` corre `alembic upgrade head` →
   crea todas las tablas + seeds → arranca gunicorn → carga el modelo de
   embeddings (~30s, ~80 MB RAM).
6. Worker arranca tras `backend` healthy.
7. Frontend (nginx) arranca tras `backend` healthy.

**Tiempo total primera vez**: 10-15 minutos. Restarts posteriores: <30s.

---

## 6. Verificación post-deploy

```bash
# Estado de los contenedores
docker compose -f docker-compose.prod.yml ps

# Esperado: 5 servicios "Up", todos "healthy"

# Logs del backend
docker compose -f docker-compose.prod.yml logs backend --tail 50

# Healthcheck HTTP directo
curl -s http://localhost:8080/api/v1/.. # depende del endpoint
curl -s http://localhost:8080/nginx-health  # → "ok"
```

**Desde tu iPhone o laptop (en la tailnet):**

1. Abrir `http://capsule.tailebc81d.ts.net:8080`
2. Pulsar "Register" para crear tu usuario real
3. Login → vista de Matches vacía esperada (aún no hay jobs scrapeados)
4. Profile → subir tu CV en PDF + **activar el toggle "Vigilancia de colegios suizos"** si quieres recibir los matches de la watchlist sin la penalización H (docencia). En producción este flag arranca apagado para usuarios nuevos.
5. Esperar 6h o forzar el scheduler:
   ```bash
   # En el NAS
   docker compose -f docker-compose.prod.yml exec backend python -c "
   from celery_app import celery_app
   celery_app.send_task('tasks.scraping.fetch_scrapers')
   celery_app.send_task('tasks.fetch_providers')"
   ```
6. En unos minutos aparecen jobs. Volver a Matches → "Find new matches".

---

## 7. Operaciones cotidianas

### Ver logs en vivo

```bash
docker compose -f docker-compose.prod.yml logs -f backend
docker compose -f docker-compose.prod.yml logs -f worker
```

### Restart selectivo

```bash
docker compose -f docker-compose.prod.yml restart backend worker
```

### Actualizar a una versión nueva

```bash
cd /share/Container/swissjob
git pull   # (o rsync desde la máquina dev)
docker compose -f docker-compose.prod.yml up -d --build backend worker frontend
# Las migraciones nuevas se aplican automáticamente en el arranque
```

### Backup de la BD

Backup manual:
```bash
mkdir -p /share/Container/backups/swissjob
docker compose -f docker-compose.prod.yml exec -T postgres \
  pg_dump -U swissjob swissjobhunter \
  | gzip > /share/Container/backups/swissjob/db-$(date +%Y%m%d-%H%M).sql.gz
```

Backup automático con cron del NAS (QNAP → Control Panel → Tareas →
Programación). Crear tarea semanal con ese mismo comando.

### Restaurar desde backup

```bash
gunzip < /share/Container/backups/swissjob/db-YYYYMMDD-HHMM.sql.gz | \
  docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U swissjob swissjobhunter
```

### Borrar el usuario de pruebas creado durante el dev

El usuario `capture@swissjob.ch` se creó solo para los screenshots de
verificación iPhone. Eliminarlo desde la BD del NAS:

```bash
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U swissjob swissjobhunter -c \
  "DELETE FROM users WHERE email = 'capture@swissjob.ch';"
```

CASCADE de FKs limpia su perfil, matches, notifications, etc.

### Parar todo (manteniendo datos)

```bash
docker compose -f docker-compose.prod.yml down
```

Los volúmenes `pgdata` y `redisdata` persisten. Para volver a arrancar:
```bash
docker compose -f docker-compose.prod.yml up -d
```

### Reset total (BORRA datos)

```bash
docker compose -f docker-compose.prod.yml down -v
```

---

## 8. Si algo va mal

| Síntoma | Diagnóstico | Acción |
|---|---|---|
| Backend reinicia en loop | Probablemente `SECRET_KEY = "change-me-in-production"` | Revisar `.env.prod` y restart |
| Backend `unhealthy` >90s | Modelo de embeddings descargando | Esperar; ver `docker logs backend` |
| Frontend 502 | Backend aún no healthy | `docker logs backend` |
| Postgres exits | Permisos volumen | Borrar `pgdata` y rearrancar (perderás datos) |
| Worker no procesa jobs | `docker logs worker` | Probablemente Redis no conecta |
| 0 jobs en Matches tras 6h | El scheduler dispatcha pero el worker falla | Logs worker |
| Healthcheck Container Station rojo | Healthcheck command falla | `docker inspect` para ver el error real |

---

## 9. Recursos esperados en operación normal

- **CPU**: <10% idle, picos al hacer matching/scraping
- **RAM**:
  - postgres: ~150 MB
  - redis: ~50-200 MB
  - backend (2 workers gunicorn): ~400-600 MB (el modelo embedding pesa)
  - worker: ~300-400 MB
  - frontend (nginx): ~10 MB
  - **TOTAL**: ~1-1.5 GB
- **Disco**:
  - Imágenes Docker: ~2 GB
  - `pgdata` crece ~50-100 MB por semana con uso normal
  - `redisdata`: <100 MB
