# CLAUDE.md — SwissJobHunter

> Proyecto: Agregador de búsqueda de empleo con IA para Suiza.
> Multi-portal, multi-idioma (DE/FR/EN/IT). Webapp standalone multi-usuario.
> Ver `PORTALES_EMPLEO_SUIZA.md` para especificaciones completas (2200+ líneas).
>
> **Revisado contra el código el 2026-10-20 (DT-148 F1).** Se corrigió lo que
> describía un árbol que ya no existe: los providers y scrapers del BFF, la CDC
> sombra, `job_matcher` y diez módulos más, las cadencias de cosecha, las
> cabezas de migración, los contadores de tests y el comando de los tests del
> núcleo (que ya no trae `pytest`). Lo verificado se cita con su fecha; si una
> cifra no la lleva, desconfía y mídela. Las secciones de puertos, perfiles de
> compose, sesiones (T9), operación del NAS y rendimiento del feed NO se
> tocaron: siguen siendo ciertas.

---

## Contexto rápido

- **Backend**: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- **Frontend**: React + TailwindCSS v4 + Vite
- **Cosecha: NATIVA y ÚNICA del core.** En el BFF no queda NINGÚN productor:
  `backend/providers/` está **vacío** y de `backend/scrapers/` sólo sobrevive
  `swiss_schools_config.py`, que ya no es un scraper sino la tabla de los
  colegios vigilados. Las listas `LEGACY_DISABLED_*` NO existen: no hay nada
  que deshabilitar. Lo cosecha todo `jobhunt.harvest.dispatch_native` en tres
  cadencias (DT-147): **6 h** a las 01:30/07:30/13:30/19:30, **12 h** a las
  09:30/21:30 y **24 h** a las 23:30 (Europe/Zurich), una tarea acotada por
  scope. Medido en PRODUCCIÓN el 2026-10-20: 72 fuentes en el registro, **40
  con scope habilitado y 50 scopes**, 94.807 vacantes activas, ninguna fuente
  apagada por bloqueo. Actas: `docs/audits/POINT4_CUTOVER_2026-09-22.md` y
  DT-144/DT-145/DT-147 del ledger
- **Lo que el BFF SÍ ejecuta**: `swissjob-worker` corre `tasks.maintenance_tasks`
  (hoy sólo la purga de refresh caducados), `tasks.watchlist_tasks`,
  `tasks.alert_tasks`, `tasks.profile_tasks` y `tasks.language_tasks` (más
  `tasks.example_task`, un `ping` sin llamador que DT-148 B3 propone retirar).
  APScheduler despacha CINCO citas: purga de refresh 03:45, idioma de títulos
  cada 5 min, salud de watchlist cada 6 h, digest de watchlist 18:00 y alerta
  de profesor cada 6 h (que lee el CATÁLOGO DEL CORE, no `jobs`)
- **`jobs` es un ARCHIVO de sólo lectura.** Última alta en producción:
  11-10-2026; no tiene ningún escritor desde el corte de la cosecha. El overlay
  local la sigue LEYENDO (`match_result_service`, `pattern_analysis`,
  `watchlist_state`), y por eso no se borra. DT-148 retiró lo que la escribía o
  la barría: chequeo nocturno de URLs, limpieza de caducadas a 60 días, digest
  diario de matches y el vertical local de candidaturas (`job_applications`,
  con cero filas en toda su vida)
- **Core**: paquete `jobhunt_core/` — API v1 FastAPI (`core-api`, puerto 8003 en
  local) y Celery con **beat embebido** (`-B`) y **14 cadencias** (leídas del
  fichero el 2026-10-20): cada **5 min** el despacho del outbox, la salud de la
  entrega, la recuperación de matching y el barrido de búsquedas guardadas; cada
  **hora** la purga de idempotencia (`CORE_IDEMPOTENCY_PURGE_EVERY_S=3600` — *no*
  cada 5 min), la salud de la cosecha (`jobhunt.harvest.check_health`: alerta si
  un scope acumula fallos o lleva días sin cosecha completa, y publica SIEMPRE el
  censo del parque porque `alertas: []` sobre un parque medio invisible se leía
  como cosecha sana) y la de las credenciales; las **tres ventanas de cosecha**
  de arriba; y **cuatro citas diarias** en este orden: dedup-scan 05:20 →
  archive-sweep 05:35 → matching-materialize-ce 06:15 → purga de retención 06:40.
  Migraciones propias vía `core-migrate`, cadena `core0001..core0055`.
  **En PRODUCCIÓN el worker está partido en tres** (`scripts/split_core_workers.py`):
  `swissjob-core-worker-r5`, `swissjob-core-harvest-worker-r5` y
  `swissjob-core-ops-worker-r5`; en local es uno solo. **La imagen del core es
  INMUTABLE** desde `ae7fbf2`: el compose base no monta `./jobhunt_core`, y para
  trabajar sobre el árbol hay que pedir el override
  `-f docker-compose.yml -f docker-compose.dev.yml` (ver «Perfiles de compose»)
- **La CDC sombra (Fase B) se RETIRÓ** el 2026-10-13 (`3ed6462`): no existen
  `jobhunt_core/shadow/`, el servicio `core-capture`, el slot lógico
  `jobhunt_shadow` ni el proyector. Lo único que sobrevive es el handler
  `providers/legacy_shadow.py`, que resuelve la identidad de las encarnaciones
  `legacy:*` que quedan en el corpus, y caduca con ellas (COTAS, ~enero 2027)
- **«AI Job Match» es la ventana del PORTFOLIO** (`/home/lothar/Public/ReactPortfolio`, tres repos git
  distintos; frontend en Cloudflare Pages desde GitHub), no la `/match` de SwissJob. Lee el mismo
  feed del core. Título traducido y resumen se sirven allí desde `job_enrichments` (2026-09-23);
  diagnóstico del panel de ofertas: `docs/audits/DIAGNOSTICO_PANEL_OFERTAS_2026-09-23.md`
- **Documentación de referencia**: **cotas aceptadas y decisiones deliberadas → `docs/COTAS_Y_DECISIONES.md`** (léelo ANTES de "arreglar" cualquier limitación: varias se intentaron cerrar y el intento fue peor que la cota); estado y contadores vigentes → `docs/unificacion/ESTADO_Y_HOJA_DE_RUTA.md` **§48** (la reconciliación del 2026-10-13; §47 y anteriores son fotos previas y NO deben leerse como vigentes), y la deuda viva → `docs/unificacion/DEUDA_TECNICA.md` (DT-148 arriba, con lo ejecutado y lo abierto); core → `docs/unificacion/PLAN_UNIFICACION_JOBHUNTING.md` (§23–§24) y `docs/unificacion/CONTRATOS_FASE_A.md`, los tres en `/home/lothar/Public/`; legacy → `docs/`

---

## Puertos Docker (mapeados al host para evitar conflictos)

| Servicio   | Puerto host |
|------------|-------------|
| PostgreSQL | **5435**    |
| Redis      | **6380**    |
| Redis core | **6381** (solo loopback) |
| Backend    | **8002**    |
| Core API   | **8003**    |
| Frontend   | **5174**    |

> Servicios en conflicto en el host: 5433, 5434 (postgres), 6379 (redis), 5678 (n8n), 8001

**Desde el 2026-09-24 (T5/C7) todos salvo el frontend se publican en `127.0.0.1`**
vía `${HOST_BIND_IP:-127.0.0.1}`: `postgres` y `redis` escuchaban en `0.0.0.0`
—alcanzables desde toda la LAN— y `redis` además **sin contraseña**. Para abrirlos
a la red hay que escribir `HOST_BIND_IP=0.0.0.0` en el `.env`, a propósito.

Dos consecuencias para quien trabaje aquí:

- **`docker compose up` no arranca sin `POSTGRES_PASSWORD` ni `REDIS_PASSWORD`**
  en el `.env` (`${VAR:?}`, sin defecto publicado). El mensaje dice cuál falta.
- **No declares `REDIS_URL`, `CELERY_BROKER_URL` ni `CELERY_RESULT_BACKEND` en el
  `.env`.** `config.py` las construye desde `REDIS_PASSWORD`. Si las declaras,
  **pon la contraseña dentro**: Celery lee `CELERY_BROKER_URL` del ENTORNO y esa
  variable gana sobre el valor que le pasa el código — con la URL sin credencial
  el BFF entra y **los dos workers se quedan fuera**, en bucle de `NOAUTH`.

El arranque del BFF además **muere** si `DATABASE_URL` trae una contraseña de dev
o un marcador de plantilla, salvo `ALLOW_DEV_CREDENTIALS=true` (este `.env` lo
declara). `scripts/check_compose_exposure.py`, también en CI, impide que esto se
deshaga: puertos en loopback, ninguna contraseña publicada como defecto y `redis`
con `--requirepass`.

---

## Perfiles de compose — operativo vs. desarrollo (auditoría externa 2026-08-27, P1-3)

`docker-compose.yml` es el perfil **OPERATIVO**: los cuatro servicios del core corren
el código **de la imagen**, sin bind mount. `RELEASE_SHA` se hornea como build arg
(ARG → ENV en el Dockerfile) y **no** aparece en ningún `environment:`, para que no
pueda desligarse del código que identifica.

`docker-compose.dev.yml` es el perfil de **DESARROLLO** y hay que **pedirlo**:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml <cmd>
```

Devuelve los cuatro bind mounts y pone `CORE_CODE_MUTABLE=1`, con lo que `/v1/ready`
responde `authoritative: false`. **No** es un `docker-compose.override.yml` a propósito:
un override implícito se aplicaría también al desplegar y devolvería el defecto en
silencio; olvidarse del `-f` deja el perfil **seguro**, no el mutable.

**Qué cambia para quien trabaja aquí:** todo comando del core que deba ver el árbol de
trabajo —los tests, sobre todo— necesita los dos `-f`. Sin ellos se probaría el código
de la IMAGEN.

Identidad de la release, comprobable en vez de confiada:

```bash
curl -s localhost:8003/v1/health   # {"release": "<sha>", "alembic_expected": "…", "authoritative": true}
curl -s localhost:8003/v1/ready    # {"release": "<sha>", "authoritative": true, ...}
```

`authoritative: true` exige código inmutable (sin el override de desarrollo) **y** un
`release` nombrable: con `RELEASE_SHA=unknown` la comparación «todos publican el mismo
SHA» se cumpliría entre `unknown`s sin significar nada (auditoría G9 P2-A/P2-B).

`core-api` tiene además healthcheck de compose contra `/v1/ready` (sin él, la API
estuvo dos días en 503 sin que nadie se enterara). `prod` y `qnap` **SÍ** lo tienen (comprobado el 2026-09-24); el que **no** lo
tiene es `docker-compose.prebuilt.yml`.

### La etiqueta del compose puede mentir — compruébalo (H16, 2026-09-24)

`image: swissjob-core:dev` es una etiqueta **mutable**, y mientras no se recree
nada puede apuntar a una imagen distinta de la que corre. Pasó: los contenedores
del core corrían `swissjob-core:d908ea2` (08-09) mientras el compose apuntaba a
un build del 04-09; al recrear `core-api`, la imagen vieja se encontró una base
más nueva y quedó `not_ready`. **Cualquier `docker compose up -d` lo habría
hecho.** El sistema lo detectó —para eso está `/v1/ready`— pero sólo *después*, y
el único síntoma era un contenedor `unhealthy` en un `ps` que nadie mira.

```bash
python3 scripts/check_core_release.py   # tras cualquier up -d / build del core
```

Comprueba tres cosas, cada una con su motivo de fallo: (1) los tres servicios del
core corren **la misma imagen a la que resuelve el compose**; (2) `/v1/ready`
responde `ready` —la cabeza de alembic de la base casa con la que espera el
código—; y (3) la release es nombrable y el código inmutable.

**Construye siempre con el SHA**, o la identidad no significa nada:

```bash
RELEASE_SHA=$(git rev-parse --short HEAD) docker compose build core-api
docker tag swissjob-core:dev swissjob-core:$(git rev-parse --short HEAD)  # pin inmutable
```

**El BFF de producción se construye con `backend/Dockerfile.prod`, NO con
`backend/Dockerfile`.** El de desarrollo corre `uvicorn --reload` como root; el de
producción crea el usuario `app`, que es el que exige el compose del NAS. Con la
imagen equivocada el contenedor ni arranca (`unable to find user app`) — pasó el
2026-09-24 y dejó el BFF caído hasta restaurar la imagen anterior:

```bash
docker build -f backend/Dockerfile.prod -t swissjob-backend:point5-<sha> backend/
```

Y el contenedor de producción hay que recrearlo con **su proyecto de compose**
(`-p swissjob` para el BFF, `-p swissjob-r5` para el core); sin `-p` choca con el
nombre y no recrea nada.

---

## Sesiones: el refresh token SE PUEDE revocar (T9, 2026-09-25)

Antes un refresh era una llave firmada de `REFRESH_TOKEN_EXPIRE_DAYS` días
(7 por defecto en `config.py`) que nada podía retirar:
`/auth/refresh` emitía tokens nuevos y dejaba el viejo igual de válido. Ahora:

- Cada refresh lleva `jti` (identidad revocable) y `fam` (la cadena de
  rotaciones que nace de UN login), con fila en `refresh_tokens`. Se guarda el
  `jti`, nunca el token.
- **Un refresh se canjea UNA vez.** Si reaparece uno ya canjeado hay dos copias
  en circulación y no se puede saber cuál es la del ladrón: cae la familia
  entera. Es la única señal de robo que ve un servidor sin estado.
- `POST /auth/logout` revoca la familia. Devuelve 204 SIEMPRE —también con un
  token ilegible o ya revocado— a propósito: un 401 le contaría a quien lo
  prueba si el token servía.
- `users.token_version` viaja dentro del access token (`tv`) y se comprueba en
  `get_current_user`. `token_store.revocar_todas_las_sesiones()` lo incrementa:
  es el corte que debe ejecutar un cambio de contraseña o una baja de cuenta
  (hoy no hay endpoint para ninguna de las dos; el mecanismo va antes).
- Ventana de migración: los refresh anteriores a T9 no traen `jti`, se aceptan
  UNA vez y salen con familia nueva. La cierra su propia caducidad.
- La política vive en `backend/services/token_store.py`; el router sólo la
  invoca. Las filas revocadas **no se borran**: son la memoria que distingue
  «reutilizado» de «desconocido». La purga va por caducidad.

Las credenciales del core también caducan: `create_credential` pone 90 d
(`CORE_CREDENTIAL_TTL_DAYS`) si no se le da fecha, `scripts/rotate_credential.py`
rota con solape en dos pasos, y `jobhunt.credentials.check_health` alerta del
solape que lleva más de 7 d sin cerrar.

## Operación: lo que existe desde el 2026-09-28 (T13, A19-04, A19-17)

- **La topología REAL del NAS está versionada en `deploy/nas/`** (A19-04): copias
  literales de `core.configured.yml` y `swissjob.configured.yml` con cada secreto o
  identificador sustituido por `${VARIABLE}`. `scripts/check_no_secrets.py` (en
  CI) impide que vuelva un literal. `deploy/nas/README.md` explica cómo renderizarlo.
  El del Portfolio vive en su repo (`ReactPortfolio/backend/deploy/nas/`).
- **`backend/.env.prod.example` se GENERA** con `backend/scripts/gen_env_example.py`
  desde `Settings`; `tests/test_env_example_covers_settings.py` falla si alguien lo
  edita a mano y se queda corto. El de la raíz (28 claves de 130) se retiró.
- **`scripts/deploy_nas.sh core|bff`** es el único camino de despliegue: retag POR
  SERVICIO (su primera versión fabricaba la deriva A19-21 con un `sed` global),
  migración del núcleo con contenedor desechable ANTES de recrear, `core-api` y
  los workers juntos, y `check_core_release.py` al final. El guard que excluía
  `core-capture` ya no hace falta: ese servicio no existe.
  **En producción los workers del core son TRES** (`swissjob-core-worker-r5`,
  `-harvest-worker-r5`, `-ops-worker-r5`), así que un despliegue los recrea
  todos; comprobar con `docker ps` que los tres vuelven.
- **Supervisor de contenedores (A19-17)**: lo ejecuta el **crontab del QTS** cada
  5 min (`/etc/config/crontab` → `/share/Public/swissjob/supervisor_contenedores.sh`),
  y alerta si falta o no corre cualquiera de los 15 esperados (incluido
  `portfolio_worker`, el proceso de fondo del Portfolio — ver su `backend/CLAUDE.md`),
  con anti-ruido (sólo cambios de estado + recordatorio cada 6 h). Fuente:
  `scripts/nas/`; al host se sube con `scp` y 700.
  **NO levantes además el contenedor `swissjob-supervisor`** (retirado el
  2026-10-07): los dos caminos comparten el fichero de estado, cada uno leía el
  cambio que escribía el otro y avisaba — **3.729 correos en 24 h**, y encima la
  copia del host era de antes de retirar `swissjob-redis-r5`, así que pedía un
  contenedor inexistente. El script lleva desde entonces un guard de ejecución
  única (240 s) que lo impide aunque se rearme el otro camino. El correo SÍ sale
  por esta vía: usa el `sendmail` (ssmtp) del QTS; el contenedor no lo tenía y
  caía al SMTP de Gmail, que sigue rechazando la credencial: la contraseña SMTP de producción tiene 14 caracteres (una de
  aplicación de Gmail tiene 16) y Gmail la rechaza —también para los cuatro avisos
  por email del BFF, que llevan tiempo sin salir—. Hasta que el propietario
  ponga una válida en `supervisor.env` y en el compose, el supervisor registra en
  `/share/Public/swissjob/supervisor/supervisor.log` y `estado`. El crontab del
  QTS no se pudo armar (exige suid); la entrada queda en `/etc/config/crontab`.
- **CI**: job `core-test` (postgres-core con wal2json construido desde
  `docker/postgres-core`, `CREATE EXTENSION vector`, bootstrap con
  `jobhunt_core.migrate`, suite completa). `jobhunt_core/requirements.txt` se fijó
  a las versiones de la imagen: en limpio, `sqlalchemy>=2.0,<3` traía la 2.1 y su
  dialecto por defecto pide `psycopg` v3, que no está.
- **Healthchecks de infraestructura a 60 s en el NAS** (A19-25): postgres, redis,
  redis-r5 y redis-core-r5 comprobaban cada 5-10 s; 16 de 23 contenedores lo
  hacían y la máquina arrancaba ~60 procesos/min sólo para vigilarse.
  `docker-compose.prebuilt.yml` se retiró (rompía la CDC); `docker-compose.core-local.yml`
  exige `CORE_IMAGE_TAG` en vez de una etiqueta de hace un mes.

- **Complejidad medida el 2026-10-20** (DT-148 F1): en código vivo quedan **dos
  grados E**, los dos `fetch_new` de `native_myscience` y `native_schuljobs` (32
  cada uno), y 23 funciones en grado D. La cota A19-13, que amparaba siete E/F
  de las herramientas de corte de la Fase E, se quedó sin objeto: esas
  herramientas se retiraron en P3 de DT-145. El estándar de CC ≤ 10 para código
  NUEVO sigue vigente (`radon cc -s -n C`). El bucle por oferta de la cosecha
  legacy (`tasks/harvest_persist.py`, T16) ya no existe: lo cosecha el core.
- **Análisis profundo de código del 2026-09-28: 23 hallazgos A20-01..23, TODOS
  corregidos el mismo día** (`docs/unificacion/DEUDA_TECNICA.md` §0.C, con una prueba
  por hallazgo en `backend/tests/test_a20_fixes.py`). Lo que cambia para quien opere:
  el `/v1` acepta `profile` opcional en PATCH/DELETE de candidaturas y el BFF lo
  envía en vez de drenar el feed — **desplegar el núcleo ANTES que el BFF**; el
  oyente SSE se reconecta solo tras un fallo de Redis; bcrypt y el parseo del CV
  van al threadpool; `alembic check` es paso del CI (los modelos declaran ya el
  índice HNSW, el GIN del `search_vector` y `ix_match_results_application_status`,
  que antes solo existían en la base y un `--autogenerate` habría borrado).
- **Los títulos de la pantalla principal se traducen EN EL FONDO** (A19-15 §D):
  `services/matching/warm.py`, tras calentar el feed de cada usuario, traduce ≤ 100 títulos por
  pasada que aún no estén en Redis (`TRANSLATION_WARMUP_*`); `/match` con
  `translate=false` los lee con un MGET y NUNCA llama al LLM (la prueba instala
  un Groq que lanza). Sin `GROQ_API_KEY` o sin Redis no se paga nada.

## Restricciones del proyecto

- **NO scraping PÚBLICO** de: jobs.ch, jobup.ch, Indeed, LinkedIn, Glassdoor, XING.
  La política sigue vigente; el `providers/restricted.py` que la implementaba se
  fue con los providers del BFF. Integrarlos exigiría un productor NATIVO en el
  core por ruta autorizada (credencial partner / feed oficial) y deshabilitado
  sin credencial — hoy no existe ninguno
- Nunca modificar `.env` ni `docker-compose.yml` sin confirmación explícita
- Tests siempre contra la DB `swissjobhunter_test` — nunca contra producción
- Tareas Celery con `def`, no `async def`. Patrón: `def task(): asyncio.run(_impl())`
- Comentarios en español para lógica no obvia; código y nombres en inglés

---

## Rendimiento del feed servido — invariante que cuesta caro romper

Desde el punto 5 (2026-09-22) el BFF **no recorre el feed entero** para servir
una página: el core informa del `total` en su primera página y el consumidor
deja de paginar al cubrir `offset + limit`. Antes servir 20 ofertas costaba 18
peticiones internas y 9-13 s; ahora 1 petición y 0,56-1,19 s en el método.

**El punto 5: matriz de aceptación EJECUTADA el 2026-09-24** — acta en
`docs/audits/MATRIZ_ACEPTACION_PUNTO5_2026-09-24.md`. En hardware holgado el
contrato SE CUMPLE en los cuatro recorridos (n=100, frío y caliente). En el NAS
**no**, y la causa está atribuida y no es el código: 2 CPUs con carga 9,4-14,9 y
un contenedor ajeno (`tinymediamanager`) llevándose hasta el 71 % de la CPU. El
mismo recorrido cuesta 0,084 s en copia y 1,45-3,09 s allí. Cierra por decisión
del propietario: dar CPU, rediseñar la carga (§10.5) o aprobar otro presupuesto.

**Historia previa.** Medido el 2026-09-23 sobre `point5-9d6b46e`
desplegado en los cinco servicios: la pantalla principal pasó de **79,3 s a
p50 2,147 s** (p95 2,554 s) y la primera carga de ~54 s a 10,196 s. Aun así
**ninguna lectura habitual baja del p95 de 2 s** (catálogo 2,420 s; feed 20
3,040 s) y la primera carga de 3.000 dobla su presupuesto de 5 s. Matriz
completa con veredicto por escenario en §9-bis del acta; lo que falta, en §10.
Los invariantes de abajo valen igual.

**El recorrido que de verdad pide la pantalla principal es `limit=3000`**
(`MatchPage.jsx:40` → `useMatchResults(3000, 0)`, `translate=false`), no una
página de 20. Es la LECTURA HABITUAL, no una exportación: el lote completo
alimenta las categorías y sus contadores, la pestaña Watchlist, el top score,
los matches ≥ 70 **y** las tarjetas visibles. Bajar el límite sin mover esos
agregados al servidor rompe las cinco cosas. (`useMatchResultsPage`, la única
consulta con `translate=true`, es código muerto.)

Tres cosas que NO deben deshacerse sin medir:

1. `MatchesPageDTO.total` es **aditivo y opcional**. Si falta, el consumidor
   vuelve al recorrido completo a propósito: sin ese dato el recorrido ES lo que
   produce el número, y cortar antes daría un total falso.
2. El recuento usa `effective_feedback_batch_sql`, **no** la forma correlacionada
   (1,1-9,3 s frente a 0,4-0,9 s para el mismo número), y se apoya en el índice
   parcial `ix_pvs_feed_current_eval` (`core0051`). El `current_eval_id IS NOT NULL`
   redundante del WHERE es lo que deja al planificador alcanzarlo.
3. La rama de `CORE_FEEDBACK_ENABLED = false` **conserva el recorrido completo**:
   allí sí hay exclusiones locales y el total es un subconjunto recalculado.

4. **El idioma de la oferta VIAJA; no se deduce al servir.** `VacancyDTO.language`
   (aditivo y opcional) → `_vacancy_dtos` → `_job_view` → router. Antes el campo
   se perdía en las tres capas y `_to_match_response` detectaba el idioma de
   CADA oferta servida: 50,1 ms x 1.800 = ~90 s por petición, el cuello real de
   la pantalla principal (79 s extremo a extremo). Un valor no-cadena se sirve
   como AUSENTE, nunca como error: es un indicador, no la identidad.
   Fijado por `backend/tests/test_language_transport.py` y
   `jobhunt_core/tests/test_vacancy_language.py`.
   **Pero sólo el 3,1 % del feed trae el dato.** Para el 96,9 % restante el
   idioma se DERIVA una vez por título y se PERSISTE en `job_title_languages`
   (migración `d3a7c1f60b84`): lo resuelve `tasks.language_tasks` cada 5 min en
   lotes acotados, y servir sólo lee. Los tres estados son explícitos — fila
   ausente = nunca visto, `language IS NULL` = encolado, `language = ''` =
   resuelto como DESCONOCIDO (y por eso no se reintenta).

5. **El camino de respuesta NO detecta idioma.** Ni con el dato ausente. Si
   falta, la tarjeta va sin indicador y la tarea de fondo lo resuelve para la
   carga siguiente. `tests/test_language_store.py` instala un detector que
   LANZA: devolver la detección al router hace explotar la prueba en vez de
   ponerla lenta otra vez. La memoización de `_detect_language` se queda, pero
   ya es de segundo orden — una caché en proceso no cubre la primera carga.

6. **El recorrido completo del feed se cachea por VERSIÓN, no por tiempo.**
   `GET /v1/profiles/{id}/matches/version` da un digest sobre
   `vacancy_id:current_eval_id:current_offer_revision_id:primary_incarnation_id:updated_at`
   de todo el feed; el BFF reutiliza su recorrido sólo si coincide EXACTAMENTE
   **y** lo relee al terminar el recorrido (un recorrido de 18 páginas no es
   atómico). Los dos últimos componentes se añadieron el 23-09 tras una
   regresión VIVA: el `state.feedback` viaja en el payload cacheado y, con
   `CORE_FEEDBACK_ENABLED=True`, un `thumbs_up` se servía como `null` tras el
   ACK. Además `CoreFeedback._write` invalida la caché del perfil tras cada
   escritura. **`feed()` NO excluye vacantes sin canónica a propósito**: es el
   feed que mide el nDCG del gate; recuento y versión sí las excluyen. Motivo medido:
   recorrer el feed es el **87-89 %** del coste de la pantalla principal
   (47,5 s frío / 11,5 s caliente, 18 páginas). Caliente seguía costando
   porque **un `If-None-Match` no ahorra cómputo**: el ETag se deriva del
   payload, así que el core construye la página igual para contestar 304.
   Cuatro cotas que NO deben deshacerse: sólo se cachea el recorrido
   **completo**; sólo se pregunta la versión si `needed > 100`; el overlay
   LOCAL (candidatura, urgencia, borrador) se relee siempre y el estado del
   core está cubierto por la versión; y sin versión fiable **se recorre**. Fijado por
   `backend/tests/test_feed_version_cache.py` y
   `jobhunt_core/tests/test_matches_version.py`.

Acta y mediciones: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md`.
La cola de latencia restante **no está atribuida**. Ni el loadavg del NAS ni el
mínimo observado la explican: un mínimo no separa trabajo de espera.

---

## Principios de diseño (máxima prioridad)

1. **Single Responsibility** — cada módulo/clase/función hace UNA cosa
2. **Cohesion** — la lógica relacionada permanece junta
3. **Low Coupling** — depender de abstracciones, no implementaciones
4. **Readability** — claridad sobre ingeniosidad; los nombres revelan intención

Estos principios tienen prioridad sobre velocidad, brevedad o DRY.

---

## Comandos clave

```bash
# Arrancar entorno completo
docker compose up -d

# Tests backend (750 passed, ~10-12 min — medido el 2026-10-20 tras DT-148; la
# cifra baja de 789 porque se retiraron los tests de las tareas y del vertical
# retirados. Reconfirmar con pytest tras cada crecida, no copiar la cifra)
# OJO: NO lances dos pytest a la vez CONTRA LA MISMA BASE — el teardown hace
# TRUNCATE ... CASCADE de swissjobhunter_test y las dos corridas se vacían las
# tablas entre sí (deadlocks + falsos rojos). La suite del core usa otra base
# (derivada de CORE_DATABASE_URL), así que esas dos SÍ pueden ir a la vez.
# `--timeout=N` NO funciona: pytest-timeout no está instalado y NUNCA lo estuvo
# (`git log -S pytest-timeout` sobre requirements.txt no devuelve nada). El
# comando que esta línea documentaba fallaba al arrancar, no al probar.
docker compose exec -T backend python -m pytest tests/ -q

# Tests core (1.785 passed · 1 skipped, ~8 min — medido el 2026-10-20)
# OJO 1 — el perfil: desde la auditoría P1-3 el compose BASE no monta ./jobhunt_core
# (imagen operativa inmutable). Los tests van con el override de desarrollo, que
# es el que monta el árbol de trabajo; sin él se probaría el código de la IMAGEN.
# OJO 2 — `pytest` YA NO VIAJA en la imagen del core (P1 de DT-145 separó las
# dependencias de producción y de desarrollo), así que el comando de abajo falla
# con «No module named pytest». Hay que layerear una imagen de test una vez:
#   printf 'FROM swissjob-core:dev\nUSER root\nRUN pip install --no-cache-dir pytest==8.4.2\nUSER core\n' > /tmp/Dockerfile.test
#   docker build -t swissjob-core:test -f /tmp/Dockerfile.test /tmp
# y pasarla con un override de una línea (`services: {core-migrate: {image: swissjob-core:test}}`).
# OJO 3 — `test_deploy_order.py` lee composes de la RAÍZ del repo, que el override
# de desarrollo no monta: añade `-v "$PWD:/repo" -w /repo` o sus 3 casos fallan
# por `FileNotFoundError`, no por una regresión.
# OJO 4 — desde DT-148 B5 el perfil dev monta ADEMÁS `./tools` (solo lectura).
# `dev_eval.py` y `train_cross_encoder.py` salieron del paquete para no viajar
# en la imagen de producción, pero dos tests del núcleo dependen de `dev_eval`
# (es el instrumento de medida del gate de matching) y lo alcanzan por
# `jobhunt_core/tests/dev_tools.py`. Sin el montaje, esos dos NO se recogen.
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm core-migrate python -m pytest jobhunt_core/tests tools/core/tests

# Linting — ruff FIJADO a 0.15.14 en CI: subir la versión es una decisión, no un
# accidente. OJO al orden si tocas ambos: formatear parte firmas largas y deja los
# `noqa` en otra línea. Primero arreglar, luego formatear, luego RE-comprobar.
# Y OJO a la versión DE LA IMAGEN: `requirements.txt` fija 0.15.14, pero una
# imagen construida antes de ese pin trae 0.16.8 y da rojos que el CI no ve.
# `ruff --version` dentro del contenedor ANTES de creerse un rojo masivo.
docker compose exec -T backend ruff check --no-cache .
docker compose exec -T backend ruff format --check --no-cache .

# `backend/` es lo único que el contenedor monta. Para el RESTO del repo —el core,
# `scripts/` y los .py de `docs/`, que hasta el 2026-09-24 nadie había pasado por
# el linter (A19-23)— hay que montar la raíz:
docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo backend \
  sh -c "ruff check --no-cache . && ruff format --check --no-cache ."

# Tres familias de `# noqa` que NO son deuda, y que borrar rompe cosas en silencio:
#  - `F401/F811` de fixtures de pytest: el import hace que pytest resuelva la
#    fixture por nombre y el parámetro la consume. Medido: quitar el `db` de
#    `jobhunt_core/tests/test_integration_school_feedback.py` deja el test en ERROR de fixture.
#  - `F401` de `pytestmark`: es un `pytest.mark.skipif` re-exportado. Quitarlo hace
#    CORRER tests que debían saltarse — y en verde, que es lo peor.
#  - `F401` de imports por efecto lateral: `arbeitnow` llama a `register_handlers()`
#    al cargarse (línea 102). Sin el import no se registra el extractor.

# Migraciones legacy (cabeza `a7c3d9e2f5b1`, leída de la base el 2026-10-20: es la
# de DT-148 que retira `job_applications` y el tipo `public.application_status`.
# La anterior, `d3a7c1f60b84`, puso clock_timestamp() en los defaults de
# jobs.first_seen_at/last_seen_at y match_results.created_at). `alembic check` es
# paso del CI: si tocas un modelo sin migrar, el CI lo dice.
docker compose exec backend alembic upgrade head
docker compose exec backend alembic revision --autogenerate -m "descripcion"

# Migraciones core (cadena core0001..core0055 — las aplica core-migrate al arrancar).
# Sin override: aplica las migraciones DE LA IMAGEN, que es lo correcto al operar.
# Para probar una migración nueva del árbol de trabajo, añade los dos -f del perfil dev.
# La última es core0055 (kill-switch de bloqueos: consecutive_blocks,
# last_blocked_at y disabled_by_blocks_at en source_scope_state).
docker compose run --rm core-migrate python -m jobhunt_core.migrate

# Logs en tiempo real
docker compose logs -f backend
docker compose logs -f worker
```

## Skills disponibles en este proyecto

| Skill | Alias | Cuándo usarla |
|-------|-------|---------------|
| `/audit` | `AUDIT` | Auditoría técnica profunda — deuda técnica, bugs, vulnerabilidades, cobertura de tests, rendimiento |
| `/audit-prod` | `AUDIT_PROD` | Auditoría de producción estricta — release blockers, seguridad crítica, fiabilidad, observabilidad |
| `/docsync` | `DOCSYNC` | Sincronizar documentación, memoria, skills y hooks — elimina obsoletos, optimiza lo que crece |

Los skills leen los prompts canónicos en `.ai/prompts/` y añaden contexto del proyecto (memoria, arquitectura) para análisis paralelo con subagentes especializados.

---

## Arquitectura en una página

> Reescrita el 2026-10-20 (DT-148 F1) comprobando qué ficheros existen: la
> versión anterior describía once módulos del BFF que se retiraron entre el 13
> y el 15 de octubre (`job_matcher`, `cursor_store`, `crawler_budget`,
> `scraper_stealth`, `compliance`, `job_classifier`, `pipeline_tasks`,
> `fetch_tasks`, `scraping_tasks`, `harvest_persist`, `matching_tasks`) más los
> 26 providers y los 15 scrapers. Nada de eso está en el árbol.

```
jobhunt_core/        # EL MOTOR. Cosecha, matching, dedup, catálogo y entrega
  harvest/           #   41 productores nativos registrados (9 de ellos la familia
                     #   swiss_schools_*) + runner + sink; kill-switch de
                     #   bloqueos (403/429/503, también los que cortan un barrido
                     #   parcial: DT-148 A2) y `SoftBlocked` para el 200 con desafío
  matching.py        #   2.684 LOC, único fichero con mantenibilidad C: diez
                     #   responsabilidades (recetas, léxicos, candidatos, cross-encoder,
                     #   políticas, exclusiones, evaluación, feed, estado). DT-148 D1
  dedup.py           #   3 brazos: ANN (pgvector/HNSW) + exacto intra + léxico (trgm)
  api/v1.py          #   contrato /v1: vacancies (con `category`/`since`), profiles,
                     #   matches, applications, documents, schools, saved_searches
  delivery.py        #   outbox + entregas por destino, dead-letter por veneno
  tasks/             #   tareas `jobhunt.*`, colas `core.*`, beat embebido

backend/             # EL BFF. API para el frontend + avisos por email
  routers/           #   FastAPI (NO existe backend/api/)
  services/
    catalog/         #   cliente del catálogo del core: search, detalle y `recent`
    matching/        #   cliente del feed /v1 + caché por VERSIÓN (ver más abajo)
    documents/ profiles/ schools/   #   un cliente por capacidad, todos al core
    groq_service.py  #   `get_chat_response` (traducción y documentos). La rama de
                     #   re-ranking que queda dentro está MUERTA desde el corte:
                     #   442 LOC sin llamador que DT-148 B1 retira
    gemini_service.py#   Gemini 3.6 Flash, PRIMARIO de CV/carta; fallback a Groq
    translation_service.py  # títulos a inglés (DE/FR/IT), caché Redis 30 d
    teacher_alert.py #   docencia de primaria: categoría H del catálogo + nivel
    email_service.py #   SMTP stdlib para los avisos
  scrapers/swiss_schools_config.py  # tabla de colegios vigilados, no un scraper
  tasks/             #   maintenance (purga de refresh), watchlist, alert, profile,
                     #   language. Sin cosecha: la hace el core
  models/            #   SQLAlchemy. `jobs` y `match_results` son ARCHIVO: se leen,
                     #   no se escriben

frontend/src/        # React + Vite. `/match` pide limit=3000 a propósito (agregados
                     # en cliente); «AI Job Match» es OTRA ventana, la del Portfolio
```

Modelos LLM (verificados contra el catálogo VIVO el 2026-09-15):
`GROQ_MODEL=openai/gpt-oss-120b` (fallback docs), `GROQ_RERANK_MODEL=qwen/qwen3.8-27b`
(hoy sólo TRADUCCIÓN: el re-ranking del Stage 3 murió con `job_matcher`),
Gemini `gemini-3.6-flash` (primario docs).
**Groq decomisa modelos sin avisar y el fallo es MUDO**: `qwen3.6-27b` devolvía
`model_not_found` y la traducción de títulos y el Stage 3 llevaban tiempo caídos sin
que nada lo gritara. Comprobar `GET /openai/v1/models` antes de dar por bueno un
modelo. Decomisos: `llama-3.3-70b-versatile` (2026-08-16), `llama-4-scout`
(2026-07-17), `qwen3.6-27b` (2026-09-15). `reasoning_effort=none` ya no es necesario
con 3.8 pero se mantiene como protección ante un futuro modelo que razone.

> Para detalles de cada componente, consultar `docs/` (legacy) y, para el core,
> `docs/unificacion/PLAN_UNIFICACION_JOBHUNTING.md` y `docs/unificacion/CONTRATOS_FASE_A.md` en `/home/lothar/Public/`
