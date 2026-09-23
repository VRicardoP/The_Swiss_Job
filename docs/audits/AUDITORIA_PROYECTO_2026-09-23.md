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
