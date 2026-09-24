# Auditoría técnica — SwissJobHunter

Fecha: 2026-09-23 · HEAD `cda4a06` (`feat/fase-a-core`) · Producción: `point5-9d6b46e` en los cinco servicios del NAS.
Áreas cubiertas: backend BFF, core `jobhunt_core/`, seguridad, frontend, infraestructura/CI/tests, arquitectura, operación en el NAS.

Método: seis revisiones de área en paralelo (lectura + comprobaciones puntuales en los contenedores locales) y, aparte, verificación **ejecutando** de lo que esas revisiones tenían prohibido tocar: suites, git, NAS. Cada hallazgo lleva **CONFIRMADO** (ejecutado o inequívoco leyendo) o **PROBABLE**. Lo ya inventariado en `DEUDA_TECNICA.md` (A18-01..06) y en `COTAS_Y_DECISIONES.md` no se repite.

> Regla de oro del proyecto aplicada a esta auditoría: donde dos fuentes discrepaban, gana lo medido. Tres afirmaciones de los revisores que contradecían documentos del proyecto se comprobaron y **eran los documentos los que estaban mal** (§II.12, §II.24, §II.19 de infraestructura).

---

## Parte I — Qué falta para terminar el proyecto

### I.1 Qué significa «terminado» aquí

Hay tres definiciones escritas y las tres cuentan:

1. **El producto** (`PORTALES_EMPLEO_SUIZA.md` §1.1, «las 3 features que definen el éxito»): pipeline IA · volumen de datos (Jooble + Careerjet + SECO + scrapers) · **UX móvil nativa** (Capacitor + PWA + swipe + push + onboarding, «app nativa real en App Store/Play Store»).
2. **El plan de unificación** (`PLAN_UNIFICACION_JOBHUNTING.md`): Fases A–E hechas; queda la **retirada** (slot CDC, base legacy en retención, cron de retención) y la **aceptación final**.
3. **El contrato de rendimiento sellado** (`PREDECLARACION_PUNTO5_2026-09-22.md`): p95 ≤ 2 s lecturas habituales, ≤ 5 s primera carga, ≤ 3 s contenido útil en frontend, ≥ 100 muestras en copia.

### I.2 Estado por bloque, con evidencia

| Bloque | Estado | Evidencia medida hoy |
|---|---|---|
| Cosecha nativa (punto 4) | **Hecho salvo el slot** | 17/17 scopes a 0 fallos, `alertas: []`, outbox `oldest_pending_s: 0`, CDC 0 pendientes |
| Retirada del slot CDC `_r5_rehearsal` | **Pendiente** (ventana desde 23-09 22:40 UTC) | precondición de código cumplida hoy: `CORE_CAPTURE_ENABLED` existe en el worker vivo |
| Rendimiento (punto 5) | **ABIERTO — contrato incumplido** | pantalla principal p50 2,147 s / p95 2,554 s; catálogo p95 2,420 s; feed 20 p95 3,040 s; primera carga 3.000: 10,196 s |
| GO de calidad del ranking | **NO-GO** | «por ausencia de examen válido, no por métrica mala» (ESTADO §43/44) |
| Aceptación final + cron de retención | **Pendientes** | ESTADO §39/§44: separados a propósito, sin fecha |
| Feature 3 del producto (móvil/PWA) | **NO entregada** | sin `manifest`, sin service worker, sin `android/`/`ios/`, sin swipe; 7 deps `@capacitor/*` y 3 hooks nativos sin ningún consumidor |
| Feature 2 (volumen: Jooble/Careerjet/SECO) | **Parcial** | 4 providers de API-key con credencial vacía; SECO no existe como provider |
| CI | **Inoperante para este trabajo** | dispara sólo en `main`; **626 commits** desde `main` (2026-07-21) sin CI; 188 sin subir a `github/feat/fase-a-core` |
| Suites | Verdes, en serie | core 1.765 · BFF 2.562 + 4 xfail (deliberados) · frontend 15/16 (el fallo es de entorno: el test lee un compose de la raíz que el contenedor no monta; pasa en el host) |
| Lint | **Rojo** | backend `ruff check`: 31 errores (15 F811 en tests, 12 F401); `ruff format --check`: 98 ficheros; core: **358** errores (nunca se ha linted) |
| Complejidad | **Fuera del estándar declarado** (CC ≤ 10) | radon: 219 funciones C, 35 D, 15 E, **6 F** (máx. 66); media A (4,9) |

### I.3 Lo que falta, en el orden en que lo haría

**A. Antes de nada — riesgos operativos y de seguridad que no esperan (1–2 días)**

1. **Borrar el slot huérfano `jobhunt_shadow`** (base `swissjobhunter`, `active=f`, **40 GB de WAL retenido** y creciendo). Nadie lo consume: `erasure-cdc` no tiene variable de slot; el documento de retirada sólo cubre el `_r5_rehearsal`. Es una fuga monótona del disco del NAS (75 % usado). *Decisión del propietario: irreversible; comprobar `active=f` en el momento y ejecutar `pg_drop_replication_slot`.*
2. **Corregir la regresión de hoy**: con `CORE_FEEDBACK_ENABLED=True` en producción (verificado), la caché del feed por versión sirve `state.feedback` rancio — un «me interesa» no se ve hasta que el corpus cambie, porque el digest del core no cubre el feedback positivo y ninguna escritura llama a `clear_feed_cache`. Fix de 3 líneas + test (§II C2).
3. **Sacar la detección de idioma también de `translate=true`**: `translate_titles` sigue llamando a `_resolve_language` sin memoizar por título (20 ms/título local, 50 ms en el NAS) y **no recibe** el idioma ya persistido. `translate=true` es el default de `/match/results` y lo usan `/match/saved` y `/history`. `CLAUDE.md` §5 afirma lo contrario (§II H1).
4. **`.gitignore`: añadir `.env.core.capture.prod`** (DSN con privilegio REPLICATION; hoy un `git add .` lo subiría).
5. **Push de los 188 commits y CI en la rama** (`branches: ['**']` o fusionar a `main`). Dos meses de trabajo sin respaldo remoto ni puerta automática.
6. **Compose base**: Postgres y Redis del BFF publicados en `0.0.0.0` con credenciales de desarrollo y Redis sin `requirepass` — en la máquina de desarrollo con Tailscale. `127.0.0.1:` como ya hace `redis-core`.
7. **Frontend: refresh de sesión y logout sólo con 401/403.** Hoy la sesión muere a los 30 min sin recuperación y un 502 transitorio borra el refresh token de 7 días.

**B. Cerrar lo que el plan tiene abierto (3–6 días, con ventanas de observación)**

8. **Punto 5**: (a) calentar el recorrido en segundo plano al detectar versión nueva (la primera carga deja de pagarse en la cara del usuario); (b) los ~1,4 s de trabajo del BFF por petición sobre 1.800 items (resolución de identidad + overlay) — es hoy el mayor sumando en caliente; (c) atribución correlacionada de la cola de las rutas de 20; (d) los cuatro escenarios expresamente pendientes (≥100 muestras, escrituras, frontend, traducción) **exigen una copia autorizada que no existe** — o se crea, o se acepta por escrito medirlos como canario.
   Además, tres defectos del diseño de la versión encontrados hoy (§II H2–H4): el digest ignora URL/listings/primary y `saved`/`notes`; `total` puede sobre-contar y apagar la caché en silencio (hoy 0/1.800 en los tres perfiles: latente); lectura desgarrada en recorridos de 18 páginas.
9. **Retirar el slot `_r5_rehearsal`** siguiendo `RETIRADA_SLOT_CDC_PUNTO4.md` (reconfirmar la precondición completa: el beat se reinició hoy).
10. **Cron de retención + aceptación final del plan** (separados a propósito, sin fecha).
11. **GO de calidad del ranking**: no es código, es un examen independiente válido. Sin él, el ranking sigue en NO-GO indefinidamente.

**C. Feature del producto no entregada (decisión de alcance, 5–15 días si se hace)**

12. **Móvil/PWA**: hoy no hay PWA (ni manifest ni SW) ni shells nativos, y el origen de la API está soldado a `/api/v1` (un shell Capacitor daría 404 en todo). O se implementa (manifest + SW con `NetworkOnly` para `/api/`, `VITE_API_BASE`, shells, push real) o se retira de la definición de éxito y se borran las 7 dependencias y los 3 hooks muertos.
13. **Volumen**: credenciales de Jooble/Careerjet/Adzuna/JSearch (vacías) y decisión sobre SECO (no existe). Es gestión, no código.

**D. Higiene que hoy hace que las garantías escritas no sean ciertas (2–4 días)**

14. Lint: 31 + 358 errores y 98 ficheros sin formatear, con `CLAUDE.md` afirmando «ruff limpio». CI del core inexistente.
15. Rate limiting: `default_limits` es configuración muerta (no hay `SlowAPIMiddleware`); sólo 5 rutas limitadas; el bucket de login es global detrás del proxy.
16. JWT sin revocación ni rotación de refresh; enumeración de usuarios (409 en registro, timing y 403 en login); credenciales del core sin caducidad.
17. Límites de recursos y rotación de logs en los composes (ninguno hoy); healthchecks de workers; `restart` que no reacciona a `unhealthy`.
18. **Versionar la topología real** (`core.configured.yml`, `swissjob.configured.yml`) y las listas `LEGACY_DISABLED_*`, que hoy sólo existen en el `environment:` del NAS: en local los 17 providers y 15 scrapers legacy siguen construyéndose.

**E. Deuda estructural (sin fecha; sólo al tocar cada módulo)**

19. Las 6 funciones de grado F y las 15 de grado E (§II arquitectura): `_fetch_scrapers_async` 561 LOC/CC 47-56, `_fetch_providers_async`, `RawListingSink._canonicalize`, `generate_document`, `CoreMatching.results` (dos algoritmos bajo un flag).
20. Sin capa `repositories/` (25 SQL literales en `api/v1.py`); ciclos de import tapados con imports diferidos; 13 scripts `import_*` (4.869 LOC) como dependencia de runtime; taxonomía de 13 categorías duplicada literalmente en Python y JavaScript (y el frontend ignora el `job_category` que ya recibe).

### I.4 Estimación honesta

| Grupo | Esfuerzo | Bloquea «terminado» |
|---|---|---|
| A. Operativo/seguridad urgente | 1–2 días | Sí |
| B. Cierres del plan | 3–6 días + ventanas de observación; el GO de ranking depende de un examen externo | Sí |
| C. Feature móvil | 5–15 días **o** una decisión de alcance de una hora | Sí, salvo que se redefina |
| D. Higiene | 2–4 días | No, pero sin ello cada garantía escrita es sospechosa |
| E. Estructural | continuo | No |

Son estimaciones de orden de magnitud, no compromisos: este proyecto ha demostrado dos veces esta semana que un «par de días» se convierte en más cuando se verifica ejecutando.

---

## Parte II — Hallazgos consolidados

Duplicados fusionados; ordenados por severidad. Entre corchetes, quién lo encontró (BFF, CORE, SEC, FE, INFRA, ARQ, PROPIO).

### CRÍTICOS

#### [CRITICAL] C1 · Slot de replicación huérfano reteniendo 40 GB de WAL [PROPIO]
- **Área**: PostgreSQL del NAS, `pg_replication_slots`.
- **Problema**: `jobhunt_shadow` (base `swissjobhunter`), `active = f`, `restart_lsn F/1C0A60C0` frente a `19/C625138` actual → **40 GB** de WAL que el clúster no puede reciclar. `swissjob-erasure-cdc` no declara slot; `RETIRADA_SLOT_CDC_PUNTO4.md` sólo retira el `_r5_rehearsal`; `DEPLOY_NAS.md:1410` lo menciona como «se queda con la base apartada» sin dueño.
- **Impacto**: fuga monótona del disco (1,8 TB al 75 %). Con el ritmo de escritura del clúster, meses; pero sin techo ni alarma.
- **Fix**: confirmar `active=f` y ningún consumidor; `SELECT pg_drop_replication_slot('jobhunt_shadow')`. Decisión del propietario (irreversible). Registrar en el acta del punto 4.
- CONFIRMADO (consulta en producción).

#### [CRITICAL] C2 · La caché del feed por versión sirve feedback rancio en producción [BFF + PROPIO]
- **Área**: `backend/services/matching/core_client.py:137,401,666-668,742-752`; `backend/services/matching/feedback.py`; `jobhunt_core/matching.py:2066-2080`.
- **Problema**: el digest cubre pertenencia, evaluación y revisión canónica; **no** cubre `feedback` positivo, `saved` ni `notes`. Con `CORE_FEEDBACK_ENABLED=True` (**verificado hoy en el NAS**), `_match_view` lee `state.feedback` del payload cacheado, y ninguna escritura de feedback llama a `clear_feed_cache` (grep: sólo `profile_erasure`). Reproducido por el revisor: tras un `thumbs_up` en el core, la segunda lectura sirve `feedback: None` con 0 peticiones.
- **Impacto**: regresión funcional introducida hoy (`1368251`): «me interesa» no se refleja hasta que el corpus cambie. El docstring de la caché promete lo contrario («el estado local del usuario se relee en CADA petición» — en esta rama ese estado ya no es local).
- **Fix**: `CoreFeedback._write`/`clear_feedback`/`record_implicit_feedback` → `clear_feed_cache(profile_id)` tras el ACK; **y** excluir `state` de lo cacheado (releerlo siempre, como el overlay local). Test: escritura + lectura con caché caliente.
- CONFIRMADO (ejecutado + flag verificado en producción).

#### [CRITICAL] C3 · 626 commits fuera del alcance del CI; 188 sin subir [INFRA + PROPIO]
- **Área**: `.github/workflows/ci.yml:3-7`; git.
- **Problema**: CI dispara sólo en `main` (`ce2b924`, 2026-07-21). Todo el core, el punto 4 y el 5 se han hecho sin lint, tests ni build automáticos. `github/feat/fase-a-core` está en `2c19837` (2026-08-29): **188 commits** locales sin respaldo remoto. Y el CI, si corriera, estaría rojo: `ruff check` falla (31 errores).
- **Impacto**: un fallo de disco local pierde 25 días de trabajo; nada automatizado ha validado el código que corre en producción.
- **Fix**: `push` (con aprobación) y `on: push: branches: ['**']`; arreglar los 31 errores antes.
- CONFIRMADO.

#### [CRITICAL] C4 · La topología de producción no existe en el repositorio [INFRA]
- **Área**: `docs/DEPLOY_NAS.md:12-20`; `docker-compose.qnap.yml`; `backend/config.py:57-58`.
- **Problema**: los servicios vivos usan `core.configured.yml` y `swissjob.configured.yml` del NAS, que no están versionados. `LEGACY_DISABLED_PROVIDERS/SCRAPERS` no aparecen en `.env`, `.env.example`, `.env.prod.example` ni en ningún compose: en local se construyen **17 providers y 15 scrapers legacy** (medido) que cosechan las mismas 16 fuentes que ya cosecha el core desde la misma IP.
- **Impacto**: producción no es reproducible ni revertible desde git; un despliegue desde el repo levantaría otra topología sobre la misma base; el corte del punto 4 depende de un `environment:` que sólo existe en Container Station.
- **Fix**: versionar ambos ficheros (secretos por `env_file`); declarar las listas en las plantillas; arranque fail-closed si `SCHEDULER_DAILY_HARVEST_ENABLED` con listas vacías.
- CONFIRMADO.

#### [CRITICAL] C5 · `.gitignore` no cubre el DSN de replicación [INFRA]
- **Área**: `.gitignore:33-35`.
- **Problema**: se ignoran `.env.core.prod`, `.env.core.admin.prod`, `.env.core.redis.prod`; falta `.env.core.capture.prod` (`CAPTURE_DSN` con privilegio REPLICATION, «lee TODO el WAL del clúster»).
- **Fix**: `.env.core.*` + `!*.example`.
- CONFIRMADO.

#### [CRITICAL] C6 · La sesión del frontend no se recupera nunca y se destruye ante un fallo transitorio [FE]
- **Área**: `frontend/src/config/api.js:11-31,118`; `frontend/src/hooks/useAuth.js:12-32`.
- **Problema**: `authApi.refresh` está definido y **nunca se llama**; `request()` no tiene rama 401. `ACCESS_TOKEN_EXPIRE_MINUTES=30`. Y `useAuth` hace `logout()` ante **cualquier** error de `/auth/me` (`retry:false`), incluido un 502/503 transitorio → borra access **y** refresh de `localStorage`.
- **Impacto**: a los 30 min toda petición falla con texto genérico; un reinicio del backend o un corte de Tailscale de 2 s obliga a reescribir la contraseña. Es «se me cae la sesión sola».
- **Fix**: en `authRequest`, ante 401 → un único `refresh()` compartido → reintento → `logout()` sólo si el refresh falla; en `useAuth`, desloguear sólo con `status ∈ {401, 403}`.
- CONFIRMADO.

#### [CRITICAL] C7 · Compose base: Postgres y Redis del BFF publicados en `0.0.0.0` con credenciales de desarrollo [SEC + INFRA]
- **Área**: `docker-compose.yml:16-33`, `:202-204`.
- **Problema**: `"${HOST_POSTGRES_PORT:-5435}:5432"` y `"${HOST_REDIS_PORT:-6380}:6379"` sin prefijo de IP; `POSTGRES_PASSWORD` con default publicado en `.env.example` **y en uso** (verificado); Redis **sin `requirepass`** (verificado: `PING`→`PONG` desde fuera). `core-api` también sin prefijo por defecto (`8003`). `redis-core` sí lo hace bien.
- **Impacto**: en la máquina de desarrollo (con Tailscale): BD completa (CV íntegros, hashes de contraseña) y broker de Celery (encolado de tareas arbitrarias, envenenado de rate-limit y del pub/sub SSE) alcanzables sin autenticación. En prod los puertos no se publican, pero la ausencia de `requirepass` y de guardia sobre `POSTGRES_PASSWORD` sí aplica.
- **Fix**: `127.0.0.1:` en los tres mapeos; `--requirepass`; `${POSTGRES_PASSWORD:?}` y la misma lista negra que `jobhunt_core/config.py:_bad_secret`.
- CONFIRMADO (local).

### ALTOS

#### [HIGH] H1 · `translate=true` sigue detectando idioma en el camino de respuesta [BFF]
- **Área**: `backend/services/translation_service.py:140`; `backend/routers/match.py:241-247,265,325,413`.
- **Problema**: `translate_titles` llama a `_resolve_language` (sin memoizar) por cada título y **no recibe** el `languages` que `_build_results_response` acaba de leer del almacén. Medido: 20,2 ms/título en local (50 ms en el NAS). `translate=true` es el **default** del Query param y lo usan `/match/saved` (SavedJobsPage) y `/match/history`.
- **Impacto**: `/match/saved` paga ~5 s en el NAS; `/results?translate=true&limit=3000` hasta 150 s. `CLAUDE.md` §5 («el camino de respuesta NO detecta idioma. Ni con el dato ausente») es falso para esta rama; los tests con detector-que-lanza sólo parchean `_detect_language`.
- **Fix**: pasar `languages` a `translate_titles`; sin dato → no detectar (mandar al LLM o no traducir). Extender el fixture del detector a `_resolve_language`.
- CONFIRMADO (ejecutado).

#### [HIGH] H2 · El digest de versión no cubre URL/listings/primary [CORE]
- **Área**: `jobhunt_core/matching.py:2066-2080` vs `api/v1.py:201-270`; `harvest/sink.py:1341`.
- **Problema**: `primary_listing{url, apply_url, last_seen_at}` y `listings[]` salen de `source_listing_incarnations`, que el sink actualiza en cada cosecha sin tocar la canónica. Un cambio de URL con mismo contenido = mismo `content_hash` = misma versión. Igual para reasignación de `primary_incarnation_id`, de la que el BFF deriva `legacy_job_ref` (la clave del estado local).
- **Impacto**: rompe la única garantía del contrato («si el feed servido cambia, la versión cambia»); enlace de aplicación obsoleto indefinidamente.
- **Fix**: añadir `v.primary_incarnation_id` al digest (coste cero, el JOIN ya trae la fila) y declarar en `MatchesVersionDTO` qué queda fuera; test de cambio de listing.
- CONFIRMADO (lectura; frecuencia no medida).

#### [HIGH] H3 · `total` puede sobre-contar y apagar la caché en silencio [CORE]
- **Área**: `matching.py:1968,2013,2077` (sin `current_offer_revision_id IS NOT NULL`); `api/v1.py:796`; `core_client.py:746`.
- **Problema**: el sink pone `current_offer_revision_id = NULL` en vacantes vivas no normalizables; el recuento y la versión las cuentan, `_vacancy_dtos` las omite → `len(items) != total` → `_remember_feed` **no cachea y no lo dice**.
- **Impacto**: la optimización del punto 5 se apaga para ese perfil sin rastro. **Hoy: 0/1.800 en los tres perfiles** (medido) — latente.
- **Fix**: `AND v.current_offer_revision_id IS NOT NULL` en la cláusula común; `logger.warning` en el descarte; usar el `total` del endpoint de versión para detectar el descuadre antes de recorrer (hoy se descarta, CORE#10).
- CONFIRMADO (lógica) / PROBABLE (ocurrencia).

#### [HIGH] H4 · Versión y recorrido no son atómicos; `saved`/`notes` fuera del digest [CORE]
- Lectura desgarrada: la versión se lee antes de 18 páginas y se guarda bajo ella; un `dismiss`+`undismiss` durante el recorrido devuelve la misma versión para un recorrido cosido. Fix: releer la versión al terminar y cachear sólo si coincide. PROBABLE.
- `saved`/`notes` no mueven la versión: el BFF se salva por releer estado local, pero la garantía la publica el core a cualquier consumidor. Fix: `s.updated_at` en el digest o rebajar el contrato. CONFIRMADO.

#### [HIGH] H5 · Rate limiting inoperante fuera de 5 rutas; bucket de login global [SEC]
- **Área**: `backend/core/rate_limit.py:9-33`; `backend/main.py:152-157`; `frontend/nginx.conf:35`.
- **Problema**: `default_limits=["100/minute"]` sin `SlowAPIMiddleware` → nunca se aplica (verificado sobre `slowapi`). Sin límite: `/documents/generate` (LLM), `/profile/cv`, `/notifications/stream`, `/match/results?limit=3000`, `/analytics/analyze` (12k filas), `/jobs/*` (sin auth). `RATE_LIMIT_TRUST_PROXY=False` → todos los clientes son la IP de nginx: 5 intentos/min de login **para toda la instalación**; activarlo sería peor porque nginx *añade* `X-Forwarded-For` en vez de sobrescribirlo.
- **Fix**: middleware o límites por ruta cara; `proxy_set_header X-Forwarded-For $remote_addr` + flag; contar login por `(email, IP)`.
- CONFIRMADO.

#### [HIGH] H6 · JWT sin revocación; enumeración de usuarios; credenciales del core sin caducidad [SEC]
- Sin `jti`, sin rotación de refresh (un refresh robado vale 7 días y en paralelo), sin logout ni cambio de contraseña (`security.py:26-59`, `auth.py:119-148`).
- Registro devuelve 409 «ya existe»; login salta `verify_password` si el usuario no existe (timing de un bcrypt) y comprueba `is_active` antes de la contraseña (`auth.py:40-45,94-104`).
- `create_credential(expires_at=None)` en todos los llamadores; tres credenciales `portfolio` vivas (dos con scopes de escritura), ninguna revocada (local; PROBABLE en NAS).
- CONFIRMADO.

#### [HIGH] H7 · El motor de matching comparte cola con el abanico de cosecha [CORE]
- `jobhunt.shadow.project` (el matching nativo) y `jobhunt.harvest.*` van a `core.harvest`; `dispatch_native` encola 16 tareas de golpe (~4.000 s de presupuesto) sobre `--concurrency=2`, `prefetch=1`. Cuatro veces al día el matching puede no ejecutarse durante decenas de minutos. Además la ventana de `dispatch_native` se deriva de `now.hour // 6` en UTC mientras el beat es Europe/Zurich: un retraso ≥ 50 min colapsa dos ventanas en un `run_key` y la cosecha se salta como `skipped`. Fix: `shadow.project` → `core.default`; ventana desde la hora Zurich del crontab. CONFIRMADO / PROBABLE.

#### [HIGH] H8 · Pérdida silenciosa por fecha ausente [CORE]
- `admission.py:150-160`: si el portal cambia el campo de fecha para el 90 % de los items, ese 90 % se descarta y el scope cierra `ok` con `last_complete_at` fresco. `check_harvest_health` no lee los contadores `_admission` que ya se persisten. Fix: umbral sobre `missing_date/(missing_date+accepted+refreshed)`. CONFIRMADO.

#### [HIGH] H9 · Sin presupuesto de tiempo en `GET /match/results`; gunicorn mata el worker [BFF]
- `MAX_FEED_PAGES=100 × CORE_HTTP_TIMEOUT=5 s` frente a `--timeout 420` con `-w 2`: un core degradado no produce un 503, produce un worker muerto con sus otras peticiones. Fix: `asyncio.timeout` agregado → `CoreUnavailableError`. CONFIRMADO (no forzado).

#### [HIGH] H10 · Infraestructura sin límites ni supervisión efectiva [INFRA]
- Cero `mem_limit`/`cpus`/`logging:` en los siete composes; `core-worker` (sentence-transformers) y `worker` (Chromium) sin techo junto a Postgres. Logs json-file sin rotación en el NAS.
- `restart: unless-stopped` no reacciona a `unhealthy` (uvicorn en 503 no sale): el incidente de «dos días en 503» se repetiría igual; en el compose base `core-api` ni siquiera tiene `restart:`.
- Ningún worker Celery tiene healthcheck; el beat va embebido en `core-worker`: un worker colgado apaga las 13 cadencias y la cosecha en silencio.
- CONFIRMADO.

#### [HIGH] H11 · Composes secundarios rotos o atrasados [INFRA]
- `docker-compose.core-local.yml` fija `swissjob-core:d908ea2` (`core0042`, 9 migraciones atrás) y es lo que corre en local (`/v1/health` → `authoritative: true`, que **no** detecta atraso).
- `docker-compose.prebuilt.yml`: `pgvector/pgvector:pg16` sin `wal_level=logical`, sin `core-capture`, `core-api`/`core-worker` bajo `profiles: ["core"]` (Container Station nunca los arrancaría), sin healthcheck.
- `docker-compose.qnap.yml` sin `build:` → `RELEASE_SHA` depende de un comentario.
- `.env.prod.example` sin `CORE_API_BASE_URL`, `CORE_CONSUMER_KEY`, `CORE_FEEDBACK_ENABLED`, `MATCH_SCORE_THRESHOLD` (42 real vs 35 default) ni `LEGACY_DISABLED_*`: un despliegue desde plantilla nace con el core desconectado en silencio.
- CONFIRMADO.

#### [HIGH] H12 · Frontend: cuatro defectos funcionales confirmados [FE]
- `MatchCard.jsx:134-139`: se renderiza «Translated from » con `job_language` `null`/`''` (la condición es `job_title_en`, el dato impreso es `job_language`) — desde hoy es el caso normal, no el borde.
- `DocumentGenerator.jsx:88-103,180,190`: con un CV pendiente, «Cover letter» reenvía la operación del CV (el botón no está bloqueado por `!!pending` y `docType` se descarta).
- `DocumentGenerator.jsx:67-76`: la clave de `sessionStorage` se evalúa con `userId` `undefined` en el primer render → la recuperación tras recarga nunca encuentra nada.
- `DocumentGenerator.jsx:90` + `nginx.conf:2` (HTTP sin TLS): `crypto.randomUUID` no existe fuera de contexto seguro → `TypeError` no capturado al generar. **Comprobable en 10 s**: `window.isSecureContext` en la URL real. PROBABLE.
- `MatchPage.jsx:136-143`: los tres handlers se recrean en cada render → `memo(MatchCard)` anulado; con 1.800 tarjetas sin virtualizar, un pulgar arriba congela la UI.
- CONFIRMADO salvo el indicado.

#### [HIGH] H13 · Token JWT en la query string del SSE [FE + SEC]
- `useNotifications.js:37`, `useCvAnalysis.js:37`, `routers/notifications.py:27-33`: el access token completo en `?token=`, con gunicorn `--access-logfile -` y nginx en `combined`. Fix: ticket de un solo uso o `fetch`+`ReadableStream`; mientras, excluir la ruta del access log. CONFIRMADO (en el código; el registro efectivo del NAS, PROBABLE).

#### [HIGH] H14 · Complejidad fuera del estándar declarado [ARQ + PROPIO]
- radon sobre `backend/` + `jobhunt_core/` (sin tests): **275 funciones con CC > 10** (219 C, 35 D, 15 E, 6 F). Grado F: `import_portfolio_manifest._classify_expected` (66), `scraping_tasks._fetch_scrapers_async` (47, 561 LOC), `school_source.reverse_sync` (41), `import_portfolio_durables.migrate_applications` (43), `import_portfolio_verify.verify_migration` (41), `sink.RawListingSink._canonicalize` (49, 9 parámetros).
- Módulos: `shadow/metrics.py` 2.228 LOC, `matching.py` 2.126 (cinco responsabilidades: SQL híbrido, recetas, políticas, evaluación, estado de usuario), `shadow/projector.py` 1.840, `harvest/sink.py` 1.380, `match_service.py` 907 (incluye notificación), `api/v1.py` 831 (25 SQL literales en la capa HTTP).
- `CoreMatching.results` son **dos algoritmos** bajo `CORE_FEEDBACK_ENABLED`; `generate_document` (173 LOC, CC 40) con cuatro `commit()` manuales en el endpoint.
- CONFIRMADO.

#### [HIGH] H15 · La base de producción del Portfolio escucha en toda la LAN [INFRA + SEC]
- Descubierto el 2026-09-24 al verificar T5. De los contenedores del NAS,
  **`portfolio_db` es el ÚNICO que publica un puerto**: `0.0.0.0:5435→5432`.
  Todo SwissJob (postgres, redis, backend, core-api) no publica ninguno.
- **Alcanzable, no sólo declarado**: la conexión TCP a `192.168.1.2:5435` se abre
  desde esta máquina (`192.168.1.19`), que es otro equipo de la red.
- Mitigación parcial ya presente: usuario `lothar`, base `proyecto`, contraseña de
  **20 caracteres** que no coincide con ningún patrón débil ni con un defecto
  conocido. El riesgo es la superficie, no una credencial trivial.
- Arreglo: `ports: "127.0.0.1:5435:5432"` en el compose del Portfolio del NAS —
  salvo que el propietario la abra a propósito para conectarse con un cliente
  gráfico desde el portátil, que es el uso que explicaría esta publicación.
  **Decisión del propietario**: toca un compose de producción.
- CONFIRMADO.

#### [HIGH] H16 · El compose base apunta a una imagen del core más vieja que la que corre [INFRA]
- Descubierto el 2026-09-24 al recrear `core-api` durante T5. `docker-compose.yml`
  fija `image: swissjob-core:dev` en los cuatro servicios del core; esa etiqueta
  era un build del **04-09**, mientras los contenedores vivos usaban
  `swissjob-core:d908ea2` del **08-09**. Nadie lo nota hasta que algo se recrea.
- Efecto medido: `core-api` pasó a `not_ready` — `alembic core0042` en la base
  contra `core0040` esperado por la imagen — y a `release unknown` /
  `authoritative: false`. **Cualquier `docker compose up -d` habría hecho lo mismo.**
- Mitigado reapuntando `:dev` a la imagen que corre el resto del core (la anterior
  se conserva como `swissjob-core:dev-20260904`). Tras ello: `ready`,
  `release d908ea2`, `authoritative: true`.
- **CERRADO el 2026-09-24.** La deriva de fondo también: la base local pasó de
  `core0042` a `core0051` con las nueve migraciones pendientes.
- Lo que hizo aceptable aplicarlas, comprobado antes y no supuesto: **ningún
  `upgrade()` contiene una sola sentencia destructiva** —los `DROP` están todos en
  el `downgrade()`, que es donde deben estar— y **producción ya corría `core0051`**.
  Eso último costó un susto: la primera consulta dio `core0029` porque pregunté a
  la base equivocada. El core de producción NO usa `swissjobhunter` sino
  **`swissjobhunter_r5_rehearsal`**; en la primera hay un esquema `jobhunt`
  residual de 50 tablas y 389 MB, congelado en `core0029`, que no usa nadie
  (→ A19-22). Los tres `ALTER` son aditivos: una `UNIQUE (id, consumer_id)` sobre
  una pareja que incluye la clave primaria —no puede encontrar duplicados—, dos
  `ADD COLUMN` anulables, y tres columnas más en `profiles`, dos de ellas
  `NOT NULL DEFAULT` (que desde PostgreSQL 11 no reescriben la tabla).
- Hecho, en este orden: imagen reconstruida del árbol con
  `RELEASE_SHA=$(git rev-parse --short HEAD)` → **`d63f74b`** (antes `unknown`),
  etiquetada también como `swissjob-core:d63f74b`; `core-migrate`
  `core0043→core0051`; recreados `core-api`, `core-worker` y `core-capture`.
- Verificado ejecutando: `/v1/ready` → `ready`, `alembic core0051`,
  `release d63f74b`, **`authoritative: true`**; los tres contenedores sobre la
  **misma** imagen a la que resuelve el compose; `shadow_change_log` sin aplicar
  **0**; slot `jobhunt_shadow` activo con 344 kB retenidos; **0 líneas de error**
  en los logs tras recrear; el BFF alcanza el core nuevo (`alembic_expected
  core0051`); `core-worker` con el beat arrancado y conectado a `redis-core`.
  Suite del core **1.769 passed, 1 skipped** (16 min, perfil dev). Recibo del
  estado previo y posterior en
  `audit-fixes-20260923/A19-21-core-migrate.receipt`.
- **Costura para que no vuelva a pasar en silencio**: `scripts/check_core_release.py`
  compara lo que el compose dice con lo que corre, y exige `ready` + release
  nombrable + `authoritative`. Cuatro controles negativos, cada uno mordiendo por
  su propia condición (etiqueta divergente —el caso H16 exacto—, `not_ready`,
  `release unknown`, `authoritative false`) y un control del control que pasa.
- **Beneficio que no se buscaba**: mientras `core-api` estuvo `not_ready`, los
  tres tests extremo-a-extremo de `test_aseam_e2e.py` se **saltaban en silencio**
  (su `skipif` exige que `/v1/ready` devuelva 200). La suite del BFF pasa de
  «2.587 passed, 3 skipped» a **2.590 passed, sin skips**.
- **El camino de vuelta ya no es reapuntar la etiqueta**: `d908ea2` espera
  `core0042` y la base está en `core0051`, así que volver a ella daría
  `not_ready` otra vez. La vuelta atrás exige además `alembic downgrade` —los
  `downgrade()` existen y son los que traen los `DROP`— o restaurar la base. Las
  dos imágenes anteriores se conservan (`swissjob-core:d908ea2` y
  `swissjob-core:dev-20260904`).

### MEDIOS

- **M1** [BFF] Escritura + `commit()` de la sesión de la petición dentro de un GET, reemitida en cada petición mientras haya pendientes (`lookup` filtra `IS NOT NULL`, así que un pendiente se reencola): `language_store.py:67-78`, `match.py:235-237`. Fix: devolver también las claves «ya vistas»; sesión propia para encolar. CONFIRMADO.
- **M2** [BFF] Carrera `clear_feed_cache` ↔ `_remember_feed` (no comprueba la generación; `_fetch_page` sí). Dos líneas. CONFIRMADO leyendo.
- **M3** [BFF] Invalidación por proceso con `gunicorn -w 2`: el borrado de cuenta purga un worker; el otro retiene títulos/descripciones en `_feed_cache` sin desalojo. Fix: pub/sub. CONFIRMADO leyendo.
- **M4** [BFF] Cachés acotadas por entradas, no por bytes: `_ETAG_CACHE_MAX=512` páginas × 100 descripciones completas por proceso. PROBABLE.
- **M5** [BFF] Drenaje escolar por petición aunque la caché de versión acierte (`school_job_refs` → `/school-jobs` entero, hasta 100 páginas) y un 503 escolar tumba el feed completo (`core_client.py:462-483`). Extiende A18-02/05. CONFIRMADO leyendo.
- **M6** [BFF] El almacén de idioma persiste para siempre una detección no determinista, sin versión de detector ni purga. Fix: `detector_version` y re-derivación al cambiar. CONFIRMADO.
- **M7** [BFF] `/analytics/analyze` y `/match/results|history|saved` sin rate-limit (la lectura más cara del sistema). CONFIRMADO.
- **M8** [SEC] `/docs` y `/openapi.json` abiertos sin auth ni gating por entorno en BFF y core (verificado). CONFIRMADO.
- **M9** [SEC] Catálogo `/jobs/*` público sin auth ni límite (`ts_rank` sobre 34k filas anónimo). Decisión de producto pendiente. CONFIRMADO.
- **M10** [SEC] Contraseñas truncadas a 72 bytes en silencio (bcrypt; verificado); `UserLogin.password` sin `max_length`. CONFIRMADO.
- **M11** [SEC + FE] nginx sin CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`; token en `localStorage`; sin `client_max_body_size` (default 1 MB contradice `CV_MAX_SIZE_MB=10`); `file.read()` entero antes de comprobar tamaño; tipo por `content_type` del cliente. CONFIRMADO.
- **M12** [SEC] `--reload` horneado en `backend/Dockerfile`; ningún servicio con `--proxy-headers`. CONFIRMADO.
- **M13** [SEC] `worker`/`worker-ai` reciben `.env.prod` entero (SECRET_KEY, SMTP, claves LLM) ejecutando Chromium sobre páginas de terceros. PROBABLE.
- **M14** [CORE] Cadencias del beat sin cota inferior (`schedule=0` = bucle); `_canonical_language` sin validar dominio; `core0051` sin `CONCURRENTLY` ni `INCLUDE (current_eval_id)`; endpoint de versión sin test de 403 y sin prueba de matriz ruta→scope sobre `app.routes`. CONFIRMADO.
- **M15** [FE] Sin PWA (ni manifest ni SW pese a `index.html` y `CLAUDE.md`); origen de API soldado (`BASE="/api/v1"`, cero `VITE_*`): un shell Capacitor daría 404 en todo; 4 módulos muertos (`useMatchResultsPage`, `useCamera`, `useOfflineStorage`, `usePushNotifications`) sosteniendo 7 dependencias. CONFIRMADO.
- **M16** [FE] El backend envía `job_category` en cada item y el frontend lo ignora (0 referencias) para reclasificar con 338 líneas duplicadas de `job_classifier.py`; tres keywords con `.*` evaluadas con `includes` nunca casan. CONFIRMADO.
- **M17** [FE] `JobDetailPage` desreferencia `job` sin cubrir `paused` (react-query v5), sin error boundary en `App.jsx`. PROBABLE.
- **M18** [FE] Login/Register con inputs sin `htmlFor`/`aria-*` pese a existir `ui/Input.jsx` accesible; sin `jsx-a11y`. CONFIRMADO.
- **M19** [INFRA] CI sin suite del core ni validación de composes de producción; `vitest --passWithNoTests`; sin script `test`; `App.test.jsx` no prueba nada. CONFIRMADO.
- **M20** [INFRA] Sin `.dockerignore` en `backend/` (tests dentro de la imagen) ni `frontend/` (`COPY . .` sobre 248 MB de `node_modules` del host tras `npm ci`). CONFIRMADO.
- **M21** [INFRA] La suite del core reconstruye 52 migraciones en 19 módulos + `sleep`s reales → ~16-20 min. Fix: BD plantilla. CONFIRMADO.
- **M22** [INFRA] Dependencias por rango sin lockfile ni hashes; `httpx` declarado dos veces con cotas distintas; `RELEASE_SHA` identifica el código, no las dependencias. CONFIRMADO.
- **M23** [INFRA] Módulos de producción sin ninguna prueba que los importe: `tasks/watermarks.py` (marcas de agua de notificación), `jobhunt_core/policy_ctl.py` (promoción de políticas), varios `api/v1_*`. PROBABLE.
- **M24** [ARQ] Ciclos reales de import tapados con imports diferidos (`matching`↔`materialization`, `erasure`↔`shadow.projector` importando un privado); 6 imports de símbolos `_privados` entre módulos; 13 scripts `import_*` (4.869 LOC) como dependencia de runtime (`applications.py:32`). CONFIRMADO.
- **M25** [ARQ] `default_client_factory` + guardia de credencial replicados 4 veces; **6 clases `CoreUnavailableError`** distintas; schemas Pydantic en routers; timeouts HTTP literales en 6 productores nativos. CONFIRMADO.
- **M26** [INFRA] **Deriva documental** verificada: `CLAUDE.md:82-84` niega el healthcheck de `core-api` en prod/qnap (lo tienen; el que no lo tiene es `prebuilt`); `CLAUDE.md:28` enumera 4 citas diarias del beat (son 5: falta `matching-materialize-ce` 06:15); `CLAUDE.md:209` cita la BD local en `b3c7d1a95e42` (está en `d3a7c1f60b84`); «25 providers registrados» (son **26**, `jobicy`); `memory/stack.md` cita `llama-4-scout` (decomisionado) y `pdfplumber` (no instalado; es PyMuPDF) y «Capacitor 6» (es 8); `memory/structure.md` head `e6f8a0b2c4d3` (hay 38 revisiones); `.env.example:78` `gemini-2.5-flash`; `memory/autonomous-daily-pipeline.md` umbral 35 (real 42); tres cifras distintas de la suite del core. CONFIRMADO.

### BAJOS

- **L1** [BFF] `store_resolved` hace N `UPDATE` por lote; el modelo `JobTitleLanguage` no declara el índice parcial que la migración sí crea (deriva modelo/migración). CONFIRMADO.
- **L2** [BFF] `services/routing._cache` sin desalojo de caducados; cliente `httpx` nuevo por página en `schools/http_client.pages()`; índice redundante `match_results.user_id`. CONFIRMADO.
- **L3** [SEC] PII (email) en logs de `digest_tasks.py:144` con `utils/redact.py` disponible; SQL por f-string en `feedback.py` correcto hoy sin barrera que lo mantenga. CONFIRMADO.
- **L4** [FE] `view_time` atribuido a la primera oferta del lote de 3.000 (que no se ve) y doble disparo en StrictMode; `useDebounce` sin limpieza; «3 rejecteds». CONFIRMADO.
- **L5** [PROPIO] `frontend/src/config/nginx.test.js` lee `../docker-compose.rehearsal.qnap.yml` de la raíz del repo: falla en el contenedor, pasa en el host — test acoplado al layout. CONFIRMADO.
- **L6** [ARQ] Comentarios en inglés y español mezclados en el mismo fichero; cabeceras de navegador duplicadas BFF/core (con salvaguarda: cota aceptada). CONFIRMADO.

---

## Parte III — Información faltante o asunciones

- **No hay copia autorizada** del entorno productivo: cuatro escenarios de aceptación del punto 5 no se pueden medir sin fabricar datos en producción.
- No se midió la **frecuencia real** de cambios de URL sin cambio de canónica ni de lotes con fechas parcialmente ausentes (H2, H8): una consulta cada uno.
- `crypto.randomUUID` (H12): depende del origen real de acceso (`http://capsule…ts.net:4000` no es contexto seguro); verificable en el navegador en 10 s.
- El estado de credenciales del core (H6) es el de la BD local; el del NAS no se consultó.
- `pg_ls_waldir` denegado: el tamaño real de `pg_wal` en disco no se midió; los 40 GB salen de la diferencia de LSN.
- Las suites se ejecutaron **hoy, en serie**, antes de esta auditoría (core 1.765, BFF 2.562 + 4 xfail); los revisores no las relanzaron.
- No se auditó a fondo `jobhunt_core/shadow/` (6.284 LOC; el proyecto lo declara «SOLO LOCAL» pero `core-capture` corre en el NAS) ni `import_*`.

## Top 5 fixes de mayor valor

1. **C2** — invalidar/excluir `state` de la caché del feed (regresión viva en producción; 3 líneas + test).
2. **C1** — borrar el slot `jobhunt_shadow` (40 GB y creciendo; una sentencia, decisión del propietario).
3. **C3 + C5** — push, CI en la rama y `.gitignore` del DSN de replicación (respaldo y puerta automática; media hora).
4. **C6** — refresh de sesión y logout sólo con 401/403 (el defecto que más ve el usuario; medio día).
5. **H1** — idioma persistido también en `translate_titles` (cierra la garantía que `CLAUDE.md` ya afirma; una hora).

## Próximos pasos de auditoría

- `jobhunt_core/shadow/` completo (capture/projector/gate/metrics) ahora que corre en producción.
- Medir H2/H3/H8 con las tres consultas indicadas y decidir cota o fix.
- Revisión de credenciales y `.env` **en el NAS** (fuera del alcance de esta pasada: sólo lectura remota acotada).
- Sesión de navegador real sobre `MatchPage` para el criterio «contenido útil ≤ 3 s» y para H12.
- Lint del core (358 errores) antes de decidir qué reglas se adoptan.

---

## Parte IV — Plan de acción: qué cambiar exactamente, y cómo demostrar que quedó bien

Cada paquete `T` es autocontenido y se cierra por separado. El orden es de riesgo, no de tamaño. Reglas comunes, que no se repiten en cada paquete:

- **Prueba roja antes, verde después.** Cada corrección va con un test que **falla contra el código actual** y pasa con el cambio. Si el test pasa antes del cambio, no es una prueba: se descarta.
- **Suites en serie**, nunca dos `pytest` a la vez. BFF: `docker compose exec -T backend python -m pytest tests/ -q`. Core: `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate python -m pytest jobhunt_core/tests -q`.
- **Un commit por paquete** (o por sub-paquete cuando se indica), mensaje que diga el *porqué*. **Nunca `push`** sin aprobación explícita.
- **NAS**: sólo lectura por defecto; toda escritura con copia `.before` y recibo con fecha UTC en `$W` (ver variables de `docs/ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md` §II.1). Recrear **un servicio por invocación**. Nunca `compose down`, `--remove-orphans`, `celery purge`, `up` global.
- **Producción**: no fabricar ofertas, avisos ni candidaturas; no llamar a proveedores facturables.
- **Cambios en `docker-compose*.yml` y `.env*`** exigen confirmación del propietario **antes** de aplicarlos (regla del proyecto). Se preparan como diff y se presentan.
- Verificar **en el proceso que corre**, no en HEAD: `release` de `/v1/ready`, `docker inspect --format '{{.Config.Image}}'`, `python -c 'from config import settings; print(...)'` dentro del contenedor.

### T0 · Preparación (30 min)

```sh
git status --short | grep -v '^ D \|^?? docs/' ; git log -1 --oneline   # esperado: sólo cambios ajenos del propietario
ssh -o ConnectTimeout=10 nas "echo NAS_OK"
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
C=/share/Public/swissjob/bin-docker-compose
E=/share/CACHEDEV1_DATA/Public/unification-e15-20260914
W=$E/audit-fixes-$(date -u +%Y%m%d); ssh nas "mkdir -p $W && chmod 700 $W"
```
Línea base: anotar en `$W/baseline.txt` la salida de `ssh nas "$D ps --format '{{.Names}}\t{{.Image}}'"`, `/v1/ready` de `core-api`, y los contadores de suites (core 1.765, BFF 2.562 + 4 xfail, frontend 15/16 con el fallo de `nginx.test.js` explicado en §II L5).

---

### T1 · Slot huérfano `jobhunt_shadow` (C1) — decisión del propietario, 15 min

**Precondición** (las tres, el mismo día):
```sh
PGC="$D exec -i swissjob-postgres psql -U jobhunt_core -d swissjobhunter_r5_rehearsal -A"
ssh nas "$PGC -c \"SELECT slot_name, database, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots;\""
#  esperado: jobhunt_shadow | swissjobhunter | f | ~40 GB     (si active = t → PARAR: alguien lo consume)
ssh nas "$D ps --format '{{.Names}}' | grep -c 'capture'"      # esperado: 1 (sólo swissjob-core-capture-r5, que usa el slot _r5_rehearsal)
ssh nas "$D inspect swissjob-erasure-cdc --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -ic slot"   # esperado: 0
```
**Acción** (irreversible; sólo con el visto bueno explícito del propietario):
```sh
ssh nas "$PGC -c \"SELECT pg_drop_replication_slot('jobhunt_shadow');\"" | tee -a $W/T1-drop-slot.log
```
**Verificación**: la consulta de slots devuelve **una** fila (`jobhunt_shadow_r5_rehearsal`, `active=t`); en los 30 minutos siguientes `df -h /share/CACHEDEV1_DATA` debe mostrar espacio recuperado conforme Postgres recicla WAL (checkpoint). Anotar tamaño antes/después en el recibo. Actualizar `docs/audits/POINT4_CUTOVER_2026-09-22.md` §8 y `docs/DEPLOY_NAS.md:1410`.

---

### T2 · La versión del feed cubre todo lo que se sirve, y las escrituras la invalidan (C2, H2, H3, H4) — 1 día

Cuatro huecos, un cambio de core y uno de BFF.

**T2.a — Core: el digest cubre estado de usuario y listing primario** (`jobhunt_core/matching.py`, `feed_version_sql`).

Sustituir la expresión del `string_agg` por:
```python
"  md5(coalesce(string_agg("
"    s.vacancy_id::text || ':' || s.current_eval_id::text || ':' "
"    || coalesce(v.current_offer_revision_id::text, '-') || ':' "
"    || coalesce(v.primary_incarnation_id::text, '-') || ':' "
"    || coalesce(s.updated_at::text, '-'), "
"    ',' ORDER BY s.vacancy_id"
"  ), '')) AS version "
```
`s.updated_at` lo escribe `set_vacancy_feedback` (`jobhunt_core/feedback.py:52-60`, `GREATEST(...)`) y `set_saved`/`set_dismissed`, así que **cualquier** feedback positivo, `saved` o `notes` mueve la versión sin coste adicional (la fila ya se recorre). `primary_incarnation_id` cubre reasignación de primary. Actualizar la docstring de `feed_version_sql` y la de `MatchesVersionDTO` (`api/schemas.py`) para decir **qué sigue fuera**: `url`/`apply_url`/`last_seen_at` de listings no primarios y cambios de listing que no toquen el primary (cota declarada).

**T2.b — Core: el recuento y la versión no cuentan lo que la página no sirve** (`matching.py`, cláusula común de `feed`, `feed_total_sql`, `feed_version_sql`): añadir `AND v.current_offer_revision_id IS NOT NULL` junto a `v.archived_at IS NULL AND v.merged_into IS NULL` en los tres sitios.

**Pruebas core** (`jobhunt_core/tests/test_matches_version.py`), añadir — cada una debe FALLAR antes del cambio:
```python
def test_la_version_cambia_con_feedback_positivo(db):          # UPDATE profile_vacancy_state SET feedback='thumbs_up', updated_at=clock_timestamp() ...
def test_la_version_cambia_al_guardar(db):                      # SET saved_at = now(), updated_at = clock_timestamp()
def test_la_version_cambia_al_reasignar_el_primary(db):         # UPDATE vacancies SET primary_incarnation_id = <otra incarnation del mismo vacancy>
def test_una_vacante_sin_canonica_no_cuenta_ni_versiona(db):   # UPDATE vacancies SET current_offer_revision_id = NULL → total baja 1 y len(items) == total
def test_la_ruta_de_version_exige_scope(db):                    # credencial sin matches:read → 403
```
Para el 403: `api._issue(factory, created, "tenant-x", ["vacancies:read"])`.

**T2.c — BFF: las escrituras invalidan; el recorrido se verifica; el descuadre se ve** (`backend/services/matching/`).

1. `feedback.py`, en `_write`, justo antes de `return ack` (y también en el `return None` tras `404`, porque el 404 no prueba que nada cambiara):
```python
        from services.matching.core_client import clear_feed_cache
        clear_feed_cache(pid)   # el feed cacheado por version contiene `state`: tras un ACK ya no describe lo que el core sirve
```
2. `core_client.py`, `_fetch_full_feed`: releer la versión al terminar el recorrido y cachear sólo si coincide (lectura desgarrada):
```python
                if cursor is None:
                    if version is not None:
                        confirmada = await self._feed_version(client, core_profile_id)
                        if confirmada == version:
                            self._remember_feed(pid, version, items, total)
                        else:
                            logger.info("feed de %s cambio durante el recorrido: no se cachea", pid)
                    return items, total
```
3. `_remember_feed`: el descarte por descuadre deja de ser mudo:
```python
        if total is None or len(items) != total:
            logger.warning("feed de %s: recorrido %d != total %s — no se cachea (¿vacantes sin canonica?)", pid, len(items), total)
            return
```
4. `_feed_version` devuelve también `total` (`tuple[str, int] | None`) y `_fetch_full_feed` lo compara con el de la primera página: si difieren, se sigue recorriendo pero no se cachea (misma advertencia). Ajustar `test_feed_version_cache.py::_Cliente` al nuevo retorno.
5. Carrera `clear_feed_cache` ↔ `_remember_feed` (M2): capturar `generacion = (_cache_generation, _profile_generations.get(pid, 0))` al entrar en `_fetch_full_feed` y en `_remember_feed` descartar si cambió.

**Pruebas BFF** (`backend/tests/test_feed_version_cache.py`), añadir:
```python
async def test_una_escritura_de_feedback_invalida_el_recorrido_cacheado(...)   # CoreFeedback con cliente falso que responde ACK; tras submit_feedback, _fetch_full_feed vuelve a pedir páginas
async def test_si_la_version_cambia_durante_el_recorrido_no_se_cachea(...)       # _Cliente que cambia .version tras servir la última página
async def test_un_descuadre_total_items_se_registra(caplog, ...)                 # WARNING presente
async def test_borrar_la_cache_mientras_se_recorre_no_deja_entrada_vieja(...)   # clear_feed_cache(pid) desde dentro de get() del cliente falso
```
Y en `backend/tests/test_core_feedback.py`: una prueba que, con `CORE_FEEDBACK_ENABLED=True` y `_feed_cache` caliente, haga `submit_feedback` y compruebe que `results()` sirve `feedback='thumbs_up'`. **Ésta es la que reproduce la regresión: debe fallar en HEAD.**

**Despliegue**: `core-api` (T2.a/b) y `backend` (T2.c), en ese orden, con la receta de `ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md` §II.1 (build desde `git archive`, `docker load`, `.before`, un servicio por invocación). Canario: `GET /match/results?limit=20&translate=false` antes y después de un `thumbs_up` real **del propietario** (no fabricado) — el segundo debe traer `feedback: "thumbs_up"`. Actualizar `CLAUDE.md` invariante 6 y `COTAS_Y_DECISIONES.md` (la fila «sólo la parte inmutable» cambia a «cubierta por la versión + invalidación en escritura»).

---

### T3 · El idioma persistido también en la traducción (H1) — 2 h

`backend/services/translation_service.py`, `translate_titles(self, titles_with_lang, languages=None)`:
```python
            # Idioma: 1) el que trae el item (core), 2) el persistido por titulo,
            # 3) heuristica de caracteres SOLO (barata). NUNCA langdetect aqui:
            # cuesta 50 ms por titulo y ya se resuelve en tasks.language_tasks.
            conocido = item.get("language") or (languages or {}).get(title.strip()[:500]) or ""
            lang = conocido or self._lang_from_chars(title)
```
Eliminar la llamada a `_resolve_language` de este método. En `backend/routers/match.py::_build_results_response`, pasar `languages` a `translator.translate_titles(titles_with_lang, languages=languages)`.

**Prueba** (`backend/tests/test_language_store.py`): fixture `detector_que_estalla` extendida a `_resolve_language` **y** `_langdetect_lang`; nueva prueba `test_traducir_no_detecta_idioma` que llame a `_build_results_response(..., groq=<GroqService falso con is_available=True y translate mockeado>)` sobre 5 títulos sin idioma. Falla en HEAD (`AssertionError` del detector). Corregir `CLAUDE.md` §5 sólo cuando la prueba pase. Desplegar `backend` (puede ir con T2.c).

---

### T4 · Respaldo, CI y secretos (C3, C5, lint) — 2 h

1. `.gitignore`: sustituir el bloque de secretos del core por
```
.env.core.*
!.env.core.prod.example
!.env.core.admin.prod.example
!.env.core.redis.prod.example
!.env.core.capture.prod.example
```
   Verificar: `git check-ignore -v .env.core.capture.prod` → coincide; `git ls-files | grep '\.env\.core\.' ` → sólo `.example`.
2. `ruff` en verde en `backend/`: los 15 `F811` son fixtures importadas y redeclaradas como parámetro — en cada fichero listado en §II C3 sustituir `from tests.test_x import seeded` + `def test_y(seeded)` por `pytest.importorskip`… no: la solución correcta es mover esas fixtures a `backend/tests/conftest.py` (una sola definición) y borrar los imports. Los 12 `F401`: `ruff check --fix --select F401`. Los 4 restantes (`E402`, `E701`, `E731`) a mano. `ruff format .` **sólo** si el propietario lo aprueba (98 ficheros; regla «no introducir formatters sin consenso» — el formatter ya está en CI, así que es aplicar lo acordado, pero se pregunta).
3. `.github/workflows/ci.yml`: `on: push: branches: ['**']` y `pull_request: branches: [main]`. Quitar `--passWithNoTests`. Añadir job `core-lint` (`ruff check jobhunt_core`) **en modo informativo** (`continue-on-error: true`) hasta que T13 decida las reglas; y job `compose-config` con `docker compose -f <cada fichero> config -q` (con `env_file` de ejemplo).
4. **Push**: presentar al propietario `git log --oneline github/feat/fase-a-core..HEAD | wc -l` y pedir aprobación explícita. Sin ella, no se hace.

---

### T5 · Exposición de red del compose base (C7) — **EJECUTADO el 2026-09-24**

> Lo de abajo es el plan tal como se escribió. Lo que realmente se hizo, y en qué
> cinco puntos el plan resultó estar equivocado al medirlo, está en
> **[T5 — acta de ejecución](#t5--acta-de-ejecución-2026-09-24)**, al final de esta parte.

Preparar el diff (no aplicarlo sin confirmación) en `docker-compose.yml`:
- `postgres.ports`: `"127.0.0.1:${HOST_POSTGRES_PORT:-5435}:5432"`; `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD es obligatorio}`.
- `redis`: `command: ["redis-server", "--requirepass", "${REDIS_PASSWORD:?}"]`, `ports: "127.0.0.1:${HOST_REDIS_PORT:-6380}:6379"`, healthcheck con `-a`. Copiar el patrón de `redis-core` (líneas 128-146).
- `core-api.ports`: `"127.0.0.1:${HOST_CORE_API_PORT:-8003}:8000"`.
- `backend/config.py`: `REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` con `redis://:${REDIS_PASSWORD}@redis:6379/N` (leídos de env); `.env.example` con `REDIS_PASSWORD=` y `POSTGRES_PASSWORD=` **vacíos** (sin default funcional).
- `backend/main.py`: extender la guardia de arranque de `SECRET_KEY` a `POSTGRES_PASSWORD` con la lista negra de `jobhunt_core/config.py:_bad_secret`.
**Verificación**: desde fuera del compose, `redis-cli -h 127.0.0.1 -p 6380 PING` → `NOAUTH`; `ss -ltn | grep -E '5435|6380|8003'` → sólo `127.0.0.1`. Suite BFF en verde (usa Redis con contraseña).

---

### T6 · Sesión del frontend: refresh y logout sólo con 401/403 (C6) — medio día

`frontend/src/config/api.js`:
```js
let refreshing = null;   // promesa compartida: N peticiones en vuelo → UN refresh
async function refreshSession() {
  if (!refreshing) {
    const rt = localStorage.getItem("swissjob_refresh_token");
    refreshing = (rt ? authApi.refresh(rt) : Promise.reject(new Error("no refresh token")))
      .then((data) => { useAuthStore.getState().setAuth(data.access_token, data.refresh_token, data.user ?? useAuthStore.getState().user); return data.access_token; })
      .finally(() => { refreshing = null; });
  }
  return refreshing;
}
async function authRequest(path, options = {}) {
  try {
    return await request(path, { ...options, headers: { ...getAuthHeaders(), ...options.headers } });
  } catch (err) {
    if (err.status !== 401 || options._retried) throw err;
    try { await refreshSession(); }
    catch { useAuthStore.getState().logout(); throw err; }
    return authRequest(path, { ...options, _retried: true });
  }
}
```
(Ojo al ciclo de imports `api.js` ↔ `authStore.js`: importar el store dentro de la función o inyectarlo.)

`frontend/src/hooks/useAuth.js`: `if (query.isError) { const s = query.error?.status; if (s === 401 || s === 403) logout(); else setHydrated(true); }` y `retry: (n, err) => ![401, 403].includes(err?.status) && n < 2`.

**Pruebas** (`frontend/src/config/api.test.js`, vitest con `fetch` mockeado): «401 → refresh → reintento con el token nuevo»; «refresh fallido → logout»; «tres peticiones concurrentes con 401 → un solo refresh». Para `useAuth`: añadir `@testing-library/react` + `jsdom` (dependencias de desarrollo; consenso del propietario) y probar «503 en /auth/me no borra el token». Añadir `"test": "vitest run"` a `package.json`.

---

### T7 · Defectos funcionales del frontend (H12, M16, L4) — medio día

1. `MatchCard.jsx:134-139`: `{match.job_title_en && match.job_language && (...)}`.
2. `DocumentGenerator.jsx`: (a) `const operation = pending && pending.docType === docType ? pending : { docType, language, operationId: newOperationId() };` y `disabled={isGenerating || !!pending}` en **ambos** botones; (b) `newOperationId()` en `utils/ids.js` con fallback `crypto.getRandomValues` cuando `crypto.randomUUID` no exista; (c) rehidratar `pending` en un `useEffect([userId, jobHash])` en lugar del inicializador de `useState`.
3. `MatchPage.jsx:136-143`: los tres handlers con `useCallback(..., [submitFeedback.mutate])` etc.; `PipelinePage.jsx:316-324` igual.
4. `jobCategories.js`: `classifyMatch` devuelve `match.job_category ?? <clasificación local>`; corregir las tres keywords con `.*` (o borrarlas). Test nuevo `jobCategories.test.js`: precedencia, fallback «otros», y que `job_category` del servidor gana.
5. `MatchPage.jsx:56-71`: retirar la señal `view_time` sobre `data[0]` (mide algo no visto) o medir por tarjeta con `IntersectionObserver`.
**Pruebas**: `MatchCard` con `job_language: null` y `''` no contiene «Translated from » (renderToStaticMarkup basta); `DocumentGenerator`: con un CV pendiente, «Cover letter» crea una operación nueva.

---

### T8 · Rate limiting real y login sin oráculos (H5, parte de H6) — medio día

1. `backend/main.py`: `from slowapi.middleware import SlowAPIMiddleware` y `app.add_middleware(SlowAPIMiddleware)` **antes** de CORS. Límites explícitos: `/documents/generate` 5/min, `/profile/cv` 3/min, `/match/results|history|saved` 30/min, `/analytics/analyze` 2/min, `/jobs/search` 60/min por IP.
2. `frontend/nginx.conf`: `proxy_set_header X-Forwarded-For $remote_addr;` (sobrescribir, no añadir). `backend/config.py`: `RATE_LIMIT_TRUST_PROXY` documentado en `.env.prod.example` con `true` **sólo** tras el cambio de nginx. `rate_limit.py`: `get_limiter_key` toma `request.client.host` cuando gunicorn corra con `--proxy-headers --forwarded-allow-ips=<ip del contenedor nginx>` (T13).
3. `backend/routers/auth.py` login: calcular siempre `verify_password` (contra un hash dummy si el usuario no existe), comprobar `is_active` **después** de la contraseña, mismo 401 en los tres casos. Registro: responder 201 genérico también si el email existe (sin crear) — o, si se prefiere conservar el 409, limitar `/auth/register` por `(email, IP)`.
4. `schemas/auth.py`: `password` con `max_length=72` en bytes (validator) en registro **y** login, o pre-hash SHA-256; documentar cuál.
**Pruebas**: `test_rate_limit_middleware.py` (una ruta sin decorador devuelve 429 al superar el default); `test_auth_no_enumeration.py` (tiempos de login inexistente vs contraseña mala dentro de ±20 %; mismo código y cuerpo); 72 bytes → 422.

---

### T9 · JWT con revocación y rotación de refresh (H6) — 1 día

Diseño mínimo: tabla `refresh_tokens(jti PK, user_id, family_id, expires_at, revoked_at, replaced_by)`; `create_refresh_token` incluye `jti` y `family`; `/auth/refresh` marca el presentado como usado y emite otro de la misma familia; **reutilización** de un `jti` ya usado → revocar la familia entera (robo detectado). `/auth/logout` revoca la familia. `users.token_version` incrementado en cambio de contraseña/borrado y comprobado en `get_current_user` (access tokens de ≤ 30 min: aceptable no revocarlos uno a uno). Migración Alembic aditiva. Pruebas: rotación, detección de reutilización, logout, `token_version`. Credenciales del core: `create_credential(..., expires_at=now+90d)` obligatorio (`jobhunt_core/credentials.py`), script `rotate_credential.py` con solape, alerta en `check_health` si un consumer tiene > 1 credencial activa.

---

### T10 · Token fuera de la query string del SSE (H13) — medio día

`backend/routers/notifications.py`: `POST /notifications/stream-ticket` (auth Bearer) → guarda en Redis `sse:ticket:<uuid>` = `user_id` con TTL 60 s y **un solo uso** (`GETDEL`); `GET /stream?ticket=…` canjea. Frontend: `useNotifications.js` y `useCvAnalysis.js` piden el ticket y abren `EventSource(`/api/v1/notifications/stream?ticket=${t}`)`; en reconexión, ticket nuevo. Mientras se despliega: `access_log off;` en un `location = /api/v1/notifications/stream` de `nginx.conf`. Pruebas: ticket caducado → 401; ticket reutilizado → 401; sin ticket → 401.

---

### T11 · Core: cola del matching, ventana del dispatcher, fechas ausentes, cotas del beat (H7, H8, M14) — medio día

1. `jobhunt_core/celery_app.py`: `"jobhunt.shadow.project": {"queue": "core.default"}` con el comentario actualizado (el single-flight protege el solape; los locks del sink son de Postgres). Prueba: `test_capture_retirement.py` o nuevo `test_task_routes.py` que fije la cola.
2. `jobhunt_core/tasks/harvest.py`: ventana desde la hora **Europe/Zurich** truncada al slot del crontab:
```python
    if window is None:
        zurich = datetime.now(ZoneInfo("Europe/Zurich"))
        slot = (zurich.hour // 6) * 6
        window = zurich.replace(hour=slot, minute=0, second=0, microsecond=0).isoformat()
```
   Prueba: con `freezegun`/monkeypatch de `datetime`, un disparo a las 12:59 Zurich y otro a las 13:00 dan ventanas distintas; dos disparos retrasados 50 min dentro del mismo slot dan la misma (idempotencia conservada).
3. `jobhunt_core/harvest/health.py::_scope_alerts`: leer `row.cursor["_admission"]` y alertar si `missing_date / max(1, missing_date + accepted + refreshed) > CORE_HARVEST_MISSING_DATE_ALERT_RATIO` (nuevo setting, default `0.5`). Prueba: un scope con 9 `missing_date` y 1 `accepted` produce alerta; 1/9 no.
4. `jobhunt_core/config.py`: `Field(ge=60)` en `CORE_SHADOW_OUTBOX_SAMPLE_EVERY_S`, `CORE_SHADOW_SLOT_HEALTH_EVERY_S`, `CORE_SHADOW_PROJECT_EVERY_S`, `CORE_DELIVERY_DISPATCH_EVERY_S`, `CORE_IDEMPOTENCY_PURGE_EVERY_S`, `CORE_HARVEST_HEALTH_EVERY_S`. Prueba: `Settings(CORE_SHADOW_PROJECT_EVERY_S=0)` → `ValidationError`.
5. `jobhunt_core/api/v1.py::_canonical_language`: `re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", v)` o `None`; `VacancyDTO.language: str | None = Field(default=None, max_length=5)`. Añadir casos a `test_vacancy_language.py` (`"deutsch"` → None).
Desplegar `core-worker` (1-4) y `core-api` (5).

---

### T12 · BFF: presupuesto de tiempo, encolado de idioma, cachés (H9, M1, M3, M4, M5) — medio día

1. `core_client.py::_fetch_full_feed`: envolver el bucle en `async with asyncio.timeout(settings.CORE_FEED_TOTAL_BUDGET_S)` (nuevo, default 60) y convertir `TimeoutError` en `CoreUnavailableError("recorrido del feed excede el presupuesto")` → 503 en vez de un worker muerto. Prueba con cliente falso lento.
2. `language_store.py`: `lookup` devuelve `(resueltos, vistos)`; el router encola sólo `titulos - vistos`. Encolar en **sesión propia** (`async with async_session() as s2`) para no commitear la de la petición. Prueba: la prueba `test_la_segunda_carga_no_vuelve_a_encolar` pasa a afirmar **que no se ejecuta ningún INSERT** (espía sobre `db.execute`), no sólo el `COUNT`.
3. `language_tasks.py` + `language_store.store_resolved`: un `UPDATE ... FROM (VALUES ...)`; añadir `detector_version` (nueva columna, migración aditiva) y re-derivar cuando cambie. `models/title_language.py`: declarar el índice parcial que la migración ya crea (`__table_args__`).
4. `_remember_feed`: recortar `description` a 500 y `tags` a `MAX_TAGS` **antes** de cachear (el cliente no consume más), y publicar `clear_feed_cache` por Redis pub/sub (canal `feed-cache:invalidate`) para que los dos workers de gunicorn la reciban; `profile_erasure` y T2.c publican en ese canal.
5. `school_job_refs` (`schools/presentation.py`): pedir `/school-jobs` sólo para los `vacancy_id` que carecen de listing `legacy:*` (filtro por `dedup_key`/ids), y degradar un fallo escolar a «sin identidad escolar» con `logger.warning`, nunca a 503 del feed. Prueba: cliente escolar que lanza → `results()` sirve el feed sin `school_id`.

---

### T13 · Infraestructura y CI (H10, H11, M19–M22, C4) — 1–2 días, con confirmación para los composes

1. Límites y logs, en `docker-compose.prod.yml` y `.qnap.yml` (diff a confirmar): `mem_limit`/`cpus` por servicio (`core-worker` 2g/1.0, `worker` 2g/1.0, `postgres` 2g, `backend` 1g, resto 512m) y `logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}` en todos. Verificar en el NAS con `docker inspect --format '{{.HostConfig.Memory}}'` tras recrear.
2. Healthchecks de workers: `test: ["CMD-SHELL", "celery -A celery_app inspect ping -d celery@$$HOSTNAME -t 15 | grep -q pong"]` (BFF) y equivalente con `jobhunt_core.celery_app` (core). Supervisor: contenedor `willfarrell/autoheal` con `autoheal=true` en las etiquetas de `core-api`, `core-worker`, `worker`, `backend`; **o** que `/v1/ready` fallido N veces termine el proceso (documentar cuál).
3. `docker-compose.core-local.yml`: `image: swissjob-core:${CORE_IMAGE_TAG:?define CORE_IMAGE_TAG}`; recrear el stack local con la release vigente.
4. `docker-compose.prebuilt.yml`: alinear con `prod.yml` (imagen `swissjob-postgres-core:pg16`, `wal_level=logical`, `core-capture`, sin `profiles:`) **o borrarlo** y quitar su fila de `DEPLOY_NAS.md`.
5. `.env.prod.example`: generarlo desde `Settings` (`python -c "from config import Settings; ..."` volcando cada campo con su default y un comentario) y una prueba `test_env_example_covers_settings.py` que falle si diverge. Incluir `LEGACY_DISABLED_PROVIDERS/SCRAPERS` con los valores del acta del punto 4, `CORE_*`, `MATCH_SCORE_THRESHOLD=42.0`.
6. `backend/config.py`: si `SCHEDULER_DAILY_HARVEST_ENABLED` y ambas listas vacías → `logger.error` en arranque con el texto «los productores legacy cosecharían las 16 fuentes nativas» (fail-loud; fail-closed sólo si el propietario lo aprueba).
7. Versionar la topología real: copiar `core.configured.yml` y `swissjob.configured.yml` del NAS a `deploy/nas/` **con las credenciales sustituidas por `env_file`** (nunca commitear los valores; `git diff` antes del commit debe estar limpio de contraseñas: `git diff --cached | grep -iE 'password|secret|dsn' `).
8. `backend/.dockerignore` (`tests/`, `tests_live/`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, `.env*`) y `frontend/.dockerignore` (`node_modules`, `dist`, `android`, `ios`). Verificar tamaño de contexto antes/después en el log de `docker build`.
9. CI: job `core-test` con servicio postgres + `pip install -r jobhunt_core/requirements.txt` + `pytest jobhunt_core/tests`; `requirements.lock` con `pip-compile --generate-hashes` usado por los Dockerfiles; borrar la línea `httpx` duplicada (`backend/requirements.txt:59`).
10. Suite del core: BD plantilla migrada una vez por sesión en `jobhunt_core/tests/conftest.py` (`CREATE DATABASE x TEMPLATE plantilla`) y que los 19 módulos que hoy hacen `CREATE DATABASE` la usen. Objetivo medible: < 8 min.

---

### T14 · Deriva documental (M26) — 1 h

Corregir, con la cifra medida y su fecha: `CLAUDE.md:28` (5 citas diarias: añadir `matching-materialize-ce` 06:15 y `shadow-preview-cycle`; qué retira `CORE_CAPTURE_ENABLED=false`), `CLAUDE.md:82-84` (el healthcheck falta sólo en `prebuilt.yml`), `CLAUDE.md:209` («ver `alembic current`» en lugar del hash), `CLAUDE.md:240` y `memory/structure.md:7` (26 providers, `jobicy`), `memory/stack.md:24,27,42` (qwen3.8-27b; PyMuPDF; Capacitor 8), `memory/structure.md:9` (38 revisiones, head por `alembic heads`), `.env.example:78` (`gemini-3.6-flash`), `memory/autonomous-daily-pipeline.md:45` (umbral 42 efectivo), y **una sola** cifra de la suite del core con fecha (en `CLAUDE.md`), el resto referencia. Comprobación: `grep -rn "llama-4-scout\|pdfplumber\|Capacitor 6\|25 REGISTRADOS\|gemini-2.5" CLAUDE.md docs/ ~/.claude/projects/-home-lothar-Public-SwissJob/memory/` → 0 fuera de contextos históricos.

---

### T15 · Decisión de producto: móvil/PWA (C) — 1 h de decisión, luego 0 ó 5–15 días

Presentar al propietario las dos vías con su coste y **no implementar ninguna sin decisión**:
- **Vía A — retirar del alcance**: borrar `useCamera`, `useOfflineStorage`, `usePushNotifications`, `useMatchResultsPage`, `useMatchHistory`, las 7 dependencias `@capacitor/*` y `capacitor.config.ts`; quitar las meta PWA de `index.html`; corregir `PORTALES_EMPLEO_SUIZA.md` §1.1 y `CLAUDE.md`. Medio día.
- **Vía B — entregarla**: `vite-plugin-pwa` con `manifest.webmanifest`, iconos, `NetworkOnly` para `/api/**` (prueba: ninguna respuesta con `Authorization` cacheada), `skipWaiting` controlado; `VITE_API_BASE` en `api.js`; shells `npx cap add android|ios`; push real (FCM/APNs) sustituyendo el hook muerto; onboarding y swipe según §1.1. 5–15 días y decisiones de tienda.

---

### T16 · Deuda estructural (E) — sólo al tocar cada módulo

Orden sugerido y forma, sin fecha: (1) `_fetch_scrapers_async`/`_fetch_providers_async` → `run_source()` común con `SourceRunResult`; (2) `CoreMatching.results` → dos clases detrás del puerto; (3) `generate_document` → `DocumentGenerationService`; (4) `matching.py` → `matching/{policies,scoring,feed,user_state}.py`; (5) `RawListingSink` → `IngestBatch`; (6) `import_*`/`*_cutover`/`dev_eval`/`train_cross_encoder` → `jobhunt_core/tools/` extrayendo los 5 símbolos que `applications.py` importa; (7) `repositories/` para los tres módulos `api/v1*.py` con SQL literal; (8) `CoreUnavailableError` base común + `services/core_http.py`. Cada refactor con test de caracterización previo y `radon cc` antes/después en el commit.

### T5 — acta de ejecución (2026-09-24)

**Qué estaba realmente expuesto.** Medido con `docker compose config`, que rinde
el fichero, y no con `docker compose port`, que rinde el contenedor en marcha:

| Servicio | Antes (lo que publica el FICHERO) | Ahora |
|---|---|---|
| `postgres` | `0.0.0.0:5435` con `POSTGRES_PASSWORD` por defecto **publicada en este repositorio** | `127.0.0.1:5435`, contraseña exigida |
| `redis` | `0.0.0.0:6380`, **sin contraseña** | `127.0.0.1:6380`, `--requirepass` |
| `backend` | `0.0.0.0:8002` | `127.0.0.1:8002` |
| `core-api` | `0.0.0.0:8003` | `127.0.0.1:8003` |
| `frontend` | `0.0.0.0:5174` | `0.0.0.0:5174` — **a propósito**: servidor de Vite, sin credenciales |

**Producción NO estaba afectada y no se ha tocado.** El NAS no corre este
fichero: `swissjob-backend` y `core-api` vienen de
`unification-e15-20260914/*.configured.yml` y `swissjob-postgres` de un compose
de Container Station (comprobado con la etiqueta `compose.project.config_files`
de cada contenedor). Los cuatro composes de despliegue del repositorio publican
**sólo el frontend** (`4000:80`, `4010:80` el ensayo): ningún plano de datos.

**Cinco puntos en los que el plan estaba equivocado, y por qué se supo:**

1. **`core-api` parecía ya resuelto y no lo estaba.** `docker compose port`
   devolvía `127.0.0.1:8003`, pero eso era **deriva de un contenedor viejo**: el
   fichero decía `0.0.0.0` y el siguiente `up -d` lo habría reabierto. Un puerto
   se audita en el fichero, no en el contenedor.
2. **La contraseña de Redis en las URLs no bastaba.** Celery lee
   `CELERY_BROKER_URL` **del entorno** y esa variable **gana** sobre la que le
   pasa el código: con `REDIS_URL`/`CELERY_*` declaradas sin credencial en
   `.env`, el BFF entraba y **los dos workers se quedaban fuera** con
   `Cannot connect … Authentication required` en bucle. Se vio porque se miró el
   log del worker, no sólo la salud del BFF. Arreglo: esas tres variables **ya no
   se declaran** en `.env` ni en `.env.example`; `config.py` las construye a
   partir de `REDIS_PASSWORD`, que es la única copia de la clave.
3. **No existe `settings.POSTGRES_PASSWORD`.** El backend usa `DATABASE_URL`; la
   guardia se escribió sobre la contraseña de esa URL.
4. **El backend legacy no tiene noción de entorno** (el core sí: `CORE_ENV`), así
   que una guardia de «credenciales de dev» no puede distinguir un portátil de un
   servidor. Se añadió `ALLOW_DEV_CREDENTIALS` (ausente ⇒ no arranca), que **no**
   aparece en `.env.prod.example`. Esta máquina lo declara `true` por escrito.
5. **`${VAR:?}` habría puesto CI en rojo.** Los jobs `compose-config` y
   `docker-build` hacen `cp .env.example .env`, y la plantilla ya no trae
   contraseñas. Se les dan valores de relleno explícitos.

En vez de fijar `127.0.0.1:` literal se usa **`${HOST_BIND_IP:-127.0.0.1}`**: el
defecto es cerrado y abrirlo a la LAN exige escribirlo en el `.env`.

**Verificación (ejecutada, no leída):**

| Qué | Resultado |
|---|---|
| `ss -ltn` | 5435, 6380, 6381, 8002, 8003 → **todos en `127.0.0.1`** |
| PING crudo al 6380 sin credencial | `-NOAUTH Authentication required.` |
| Los cuatro puertos **desde el NAS** hacia esta máquina | rechazados los cuatro (antes, alcanzables) |
| `compose config` sin las contraseñas | **falla** con el mensaje de cada variable (salida 1) |
| Healthcheck de `redis` (autenticado) | `healthy` |
| Celery: `control.ping()` por el broker | **2 workers responden**, 40 tareas registradas, 0 líneas `NOAUTH` |
| Guardia de credenciales | **7/7** casos: aborta con las cuatro claves de dev, con los marcadores de plantilla y sin contraseña; pasa con clave real y con el permiso explícito |
| `scripts/check_compose_exposure.py` | verde, y **3/3 controles negativos** muerden por su propia condición (puerto reabierto, clave quemada, redis sin auth) |
| `ruff check` / `ruff format --check` | limpio (orden: arreglar → formatear → volver a comprobar) |
| Suite BFF | ver tabla de estado |

**Lo que queda, y por qué no se ha hecho aquí:**

- **`redis` de producción sigue sin contraseña** (los cuatro composes de
  despliegue). No se publica ningún puerto, así que sólo es alcanzable desde
  dentro de la red de Docker. Cambiarlo exige reiniciar producción → **T13, con
  confirmación del propietario**.
- **La contraseña de dev de Postgres sigue siendo la publicada** en esta máquina.
  Con el 5435 en loopback ya no es alcanzable desde fuera, y rotarla toca la base
  local viva; queda como decisión del propietario:
  `ALTER ROLE swissjob PASSWORD '…'` + las dos líneas del `.env`.
- **Segundo hallazgo, destapado al recrear** (H16): el compose base fijaba
  `image: swissjob-core:dev`, una imagen del **4 de septiembre**, mientras los
  contenedores del core que de verdad corrían usaban `swissjob-core:d908ea2`,
  del **8 de septiembre**. Al recrear `core-api` desde el compose pasó a
  `not_ready` (`alembic core0042` en la base contra `core0040` esperado por la
  imagen). **Cualquier `docker compose up -d` habría hecho lo mismo**: era una
  mina puesta, no un efecto de T5. Restaurado apuntando `:dev` a la imagen que
  corre el resto del core (la vieja se conserva como `swissjob-core:dev-20260904`);
  `core-api` quedó `ready`, `release d908ea2`, **`authoritative: true`** — mejor
  que antes, cuando publicaba `release unknown` y `authoritative: false`.
  **Cerrado del todo el 24-09**: la base local subió a `core0051` y el core corre
  `d63f74b` — ver H16.
- **Hallazgo nuevo, fuera de T5** → ver **H15**: el **único** contenedor
  del NAS que publica un puerto es `portfolio_db`, en **`0.0.0.0:5435`**,
  alcanzable desde la LAN (comprobado abriendo la conexión desde esta máquina).
  La contraseña no es trivial (20 caracteres, no coincide con ningún patrón
  débil), pero es una base de producción escuchando en toda la red.

---

### Estado de ejecución

| Paquete | Estado | Evidencia |
|---|---|---|
| T2 | **CERRADO salvo el canario de escritura** | `7591da4` + `17e2b9e`; 9 pruebas rojas→verdes (5 core, 4 BFF); core 1.770, BFF 2.567 + 4 xfail; `core-api` y `backend` en `point5-17e2b9e` verificados en el proceso (`release`, `inspect`, `_write` contiene `clear_feed_cache`), 0 reinicios. **PENDIENTE**: el «me interesa» real del propietario que demuestre en producción que la lectura siguiente lo sirve. Tropiezo registrado: la cláusula de canónica en `feed()` bajó dos nDCG a 0,0 y se retiró de ahí (es el feed del gate) |
| T3 | **CERRADO** | `351c5a0`, desplegado en `swissjob-backend:point5-351c5a0` (0 reinicios). Medido EN EL PROCESO desplegado, con el espía en ese mismo proceso y sobre 100 títulos reales: el paso de idioma de la traducción pasa de **127,8 ms/título (230 s por feed) a 0,08 ms y 0 llamadas a langdetect**. 5 pruebas, 3 rojas contra HEAD. Suite 2.572 passed. Incluye A19-05: `.gitignore` por prefijo `.env.core.*` — el fichero del DSN **no existe** en el árbol, así que el riesgo era latente, no vivo |
| T1 | **CERRADO** | Slot huérfano `jobhunt_shadow` borrado con autorización del propietario. Siete precondiciones verificadas antes (inactivo y sin PID, sin `pg_stat_replication`, sin walsender en la base legacy, ningún contenedor lo declara, ningún compose lo nombra, `archive_mode=off`, `wal_keep_size=0`). **`pg_wal` 41 GB → 81 MB**, disco libre 432 → **472,3 GB**, tras forzar un `CHECKPOINT` (1m18s: no se recicló solo en 4,5 min). El slot vigente `_r5_rehearsal` sigue activo; core `ready`, CDC 0 pendientes, 0 reinicios. Recibo con el estado previo completo en `audit-fixes-20260923/T1-drop-slot.receipt` |
| T4 | **CERRADO** (salvo el push, que es del propietario) | `ruff check` **31 → 0** y `ruff format` aplicado a 99 ficheros (`6e30e89`, `02a906a`, `2060308`), con el hash del formateo en `.git-blame-ignore-revs`. CI: dispara en **toda rama** (626 commits se escribieron sin que corriera), `ruff` con **versión fijada** (un `pip install ruff` a secas lo pone rojo solo), `vitest` sin `--passWithNoTests`, job **`core-lint` informativo** (`jobhunt_core` nunca se ha linted) y job **`compose-config`** que valida los composes de despliegue — verificado localmente: `dev` necesita la base, `prod`/`prebuilt` necesitan cinco ficheros de entorno con plantilla, y `qnap`/`rehearsal` quedan fuera porque sus rutas del NAS no existen en CI. Suite 2.572 passed |
| T5 | **CERRADO** | `postgres` (5435) y `redis` (6380, **sin contraseña**) dejaban de escuchar en `0.0.0.0`; todo salvo el frontend va por `${HOST_BIND_IP:-127.0.0.1}`, `redis` con `--requirepass` y el compose se niega a arrancar sin `POSTGRES_PASSWORD` ni `REDIS_PASSWORD`. **18 pruebas nuevas** (`test_c7_credenciales.py`), **13 de las 18 mueren** al mutar el validador y la guardia (las 5 supervivientes son las que afirman «no cambia»/«no lanza»). Suite BFF **2.587 passed**, 3 skipped, 4 xfailed; ruff limpio. Verificado ejecutando: `ss -ltn` con los cinco puertos en loopback, PING crudo → `NOAUTH`, los cuatro puertos rechazados **desde el NAS**, `compose config` sin contraseñas → salida 1, `control.ping()` → 2 workers. **Producción no afectada** (corre `*.configured.yml`). Dos trampas medidas: `compose port` rinde el contenedor y `config` el fichero; y Celery lee `CELERY_BROKER_URL` del entorno y **gana** sobre el código — con la URL sin credencial el BFF entraba y los workers quedaban fuera. Destapó **H15** (base del Portfolio en `0.0.0.0:5435` en el NAS) y **H16** (tag `swissjob-core:dev` obsoleto) |
| T0, T6–T16 | PENDIENTES | T13 y T15 son decisiones del propietario |
| Fuera del plan — panel «AI Job Match» (Portfolio) | **HECHO salvo publicar el frontend** | `ReactPortfolio/backend` `c01a192`+`32fe475` desplegado (`enrich-32fe475`); frontend `a0bf889` sin push (Cloudflare Pages). Detalle: `docs/audits/DIAGNOSTICO_PANEL_OFERTAS_2026-09-23.md` §5 |

### Criterio de cierre de cada paquete

Un paquete está cerrado cuando: (1) su prueba roja→verde está en el commit; (2) las suites completas afectadas están en verde **y se citan con su cifra**; (3) si hay despliegue, el recibo en `$W` incluye `.before`, el `release`/imagen verificados en el proceso, 0 reinicios y el canario; (4) la documentación que afirmaba lo contrario está corregida en el mismo commit o en el siguiente. Si algo no se puede cerrar, se escribe **PENDIENTE** con el motivo, nunca «hecho salvo».

---

## Parte V — Prompt de ejecución

Pegar tal cual como primer mensaje a quien vaya a ejecutar el plan (persona o agente). Es autocontenido; el detalle está en el documento que referencia.

```
Vas a ejecutar el plan de acción de la auditoría técnica de SwissJobHunter:
/home/lothar/Public/SwissJob/docs/audits/AUDITORIA_PROYECTO_2026-09-23.md — Parte IV
(paquetes T0–T16). Lee ANTES, en este orden: ese documento entero; CLAUDE.md del repo;
docs/COTAS_Y_DECISIONES.md (muchas limitaciones son deliberadas y están medidas: no las
«arregles»); docs/CONTEXTO_TRAS_TRASPASO_2026-09-22.md (las diez trampas que ya
costaron caro); y ~/.claude/projects/-home-lothar-Public-SwissJob/memory/MEMORY.md.

CONTEXTO EN UNA FRASE. Agregador de empleo con un BFF FastAPI/Celery (backend/), un core
multi-tenant (jobhunt_core/) y un frontend React (frontend/), desplegado en un NAS QNAP de
dos núcleos que corre producción real con tres perfiles. Las suites están en verde (core
1.765, BFF 2.562 + 4 xfail deliberados) y los indicadores operativos también; la auditoría
encontró 7 críticos, 14 altos, 26 medios y 6 bajos que esos indicadores no ven.

REGLA DE ORO. Un documento —incluida esta auditoría— puede afirmar una garantía que el
código no da. Verifica EJECUTANDO, no leyendo: si al reproducir un hallazgo no falla como
se describe, páralo, anótalo y no apliques su fix.

ORDEN. T0 → T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T10 → T11 → T12 → T13 → T14 → T9 →
T15 → T16. No adelantes paquetes. T1, T5, T13 (composes) y T15 exigen decisión o
confirmación explícita del propietario: prepara el diff o la propuesta, preséntala y espera.
El resto ejecútalo de forma autónoma, eligiendo siempre la opción más simple que cierre el
paquete.

MÉTODO POR PAQUETE. (1) Reproduce el hallazgo con una prueba que FALLE contra HEAD; si
pasa, no es una prueba: descártala y busca otra. (2) Aplica el cambio mínimo indicado.
(3) Prueba en verde + suite completa afectada, EN SERIE (jamás dos pytest a la vez).
(4) Commit propio con mensaje que explique el porqué; nunca `git push` sin aprobación.
(5) Si despliega: imagen desde `git archive` del commit, `docker load`, copia `.before`
del compose, un servicio por invocación ssh, verificación en el PROCESO (release de
/v1/ready, `docker inspect`, `settings` dentro del contenedor), 0 reinicios, canario de
sólo lectura, recibo con fecha UTC en $W. (6) Corrige en el mismo commit la documentación
que afirmaba lo contrario. (7) Declara el paquete CERRADO o PENDIENTE con motivo. Nunca
«hecho salvo».

PROHIBIDO. Escribir en el NAS sin `.before` y recibo; `compose down`, `--remove-orphans`,
`celery purge`, `up` global; fabricar ofertas, avisos, candidaturas o feedback en
producción (el canario de T2 usa un thumbs_up REAL del propietario, que tú pides);
llamar a proveedores facturables (LLM) en pruebas; tocar `.env*` o `docker-compose*.yml`
de producción sin confirmación; instalar paquetes de sistema; lanzar suites en paralelo;
declarar cerrado un contrato midiendo otra cosa (p50 por p95, un método por el endpoint,
HEAD por el proceso).

ENTREGA. Al terminar cada paquete, dos o tres líneas: qué se cambió, la prueba que lo
demuestra (nombre y por qué fallaba antes), cifras de suites, y si hubo despliegue, el
release verificado. Al final, actualiza docs/audits/AUDITORIA_PROYECTO_2026-09-23.md con
una tabla T0–T16 → CERRADO/PENDIENTE (motivo), ESTADO_Y_HOJA_DE_RUTA.md con una sección
nueva, DEUDA_TECNICA.md (A18-* que cambien de estado) y la memoria
(audit-2026-09-23.md). Sin push.
```
