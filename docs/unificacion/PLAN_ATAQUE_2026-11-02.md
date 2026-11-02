# Plan de ataque — toda la deuda · 2026-11-02

> Construido sobre `docs/audits/AUDITORIA_2026-11-02.md` (79 hallazgos nuevos) y
> sobre la verificación del inventario DT-148 (nueve puntos corregidos). **Nada de
> aquí se asume: cada decisión de orden cita la medida que la justifica.**

---

## 0. La idea en una frase

**La deuda estructural de DT-148 no es lo urgente.** Hay dos fallos CRITICAL
vivos en producción y nueve arreglos de corrección de menos de veinte líneas que
valen más que cualquiera de los quince puntos de los bloques C y D. El plan
ataca primero lo que está roto, después lo que es inseguro, después lo que
miente, y sólo entonces lo que es feo.

---

## 1. Restricciones reales que ordenan el trabajo

Medidas hoy, no supuestas. Cada una elimina opciones:

| Restricción | Consecuencia para el plan |
|---|---|
| `main` local = `89e0e5f`, **2 commits por delante de `github/main` (`6efebe5`)**, idéntico a `github/dt148/a1801-receipt` | El PR del recibo está **pendiente de merge del propietario**. `deploy_nas.sh` exige el SHA exacto de `github/main`, así que **no se puede desplegar nada hasta que ese PR entre**. Todo el trabajo nuevo se apila sobre un `main` que aún no es el remoto |
| `main` está protegida: PR obligatorio + 9 checks, y **`gh` no está instalado ni hay token** | Yo no puedo abrir ni fusionar PRs. Cada tanda termina en una rama + los nueve checks verificados en local, y el merge lo hace el propietario |
| Entorno local: lo levanté (`postgres`, `redis`, `backend`, `core-api` sanos) | Los tests del frontend y de `scripts/` corren ya |
| ⚠ **A-65 es un PREREQUISITO, no un ítem más de la lista** (añadido el 2026-11-03 tras subestimarlo) | `docker compose exec -T backend python -m pytest` devuelve **`ModuleNotFoundError: No module named 'pytest'`**, y `check_source_ownership.py:89-96` **prohíbe** devolver `pytest`/`ruff` a `backend/requirements.txt`. O sea: **ningún ítem de `backend/` se puede verificar hasta resolverlo.** Hacerlo bien una vez (servicio `backend-dev` con `requirements-dev.txt`) es más barato que layerear una imagen a mano en cada tanda — y es el propio arreglo de A-65. **Ponlo primero** |
| Cada corrida de la suite del BFF son **10-12 minutos** | Eso manda el AGRUPAMIENTO: los ítems de `backend/` se hacen en **una tanda con una sola corrida al final**, no uno a uno. Es el error que cometí al prometer que se podían encadenar «sin pararme» |
| **NO lanzar dos `pytest` contra la misma base** | La suite del BFF y la del núcleo sí pueden ir en paralelo (bases distintas); dos del BFF, nunca |
| Producción sana: núcleo `point5-6efebe5`, BFF/frontend `point5-14438b7`, 12 d arriba | Hay margen para trabajar sin prisa, salvo A-01 y A-02 |
| Rotación de secretos: **pendiente de confirmación del propietario** (relevo §5) | A-02 no la puedo cerrar yo. Puedo preparar todo lo demás |

---

## 2. Orden de ejecución, y el motivo de cada posición

### Tanda 0 — lo que no espera (hoy)

| # | Qué | Por qué primero | Coste |
|---|---|---|---|
| **0.1** | **A-01** · `/match` en blanco: sacar `facets.top_score`/`strong_count` de los arrays de dependencias + `ErrorBoundary` por ruta | **La pantalla principal del producto lleva 13 días caída en producción.** Verificado en el bundle servido. No hay nada en esta lista que compita | 2 líneas + ~40 de boundary |
| **0.2** | **Test de primer render** por cada página (`MatchPage`, `WatchlistPage`, `ProfilePage`, `SearchPage`, `LoginPage`, `OnboardingPage`) | Es **lo que habría parado 0.1**, y el arnés actual no puede verlo (A-69). Va inmediatamente después porque sin él 0.1 se repite | ~60 líneas |
| **0.3** | **A-02** · sustituir la credencial del doc por `${POSTGRES_PASSWORD}` **y enseñar al guard la sintaxis `.env`** | El guard devuelve **0 hallazgos** sobre el fichero que contiene la credencial: sólo entiende YAML. Probado con secretos sintéticos. **El fix de regex va ANTES de ampliar el alcance**, o el CI pasa en verde con el secreto dentro | ~15 líneas |

**La rotación de la credencial (A-02 paso 1) es del propietario y bloquea el
resto de A-02**: reescribir el historial no deshace lo publicado, así que el orden
correcto es rotar → limpiar → (quizá) `filter-repo`. Yo dejo los pasos 2 y 3
listos.

### Tanda 1 — seguridad y fiabilidad de producción

> ### ⚠ CORRECCIÓN DEL 2026-11-03 — las estimaciones de esta tabla estaban mal
>
> La primera versión de esta tanda decía que los puntos 1.2 a 1.8 eran «todos de
> menos de treinta líneas y no tocan composes», y que se podían encadenar sin
> pararse. **Medido, era falso en tres cosas**, y se deja escrito porque una
> estimación equivocada en un plan es deuda igual que el código:
>
> 1. **«No tocan composes» es falso para A-16.** `RATE_LIMIT_TRUST_PROXY` existe
>    **sólo** en `backend/config.py:144` (defecto `False`) y en
>    `backend/.env.prod.example:44`. **No está en ningún compose**, así que
>    activarlo en producción exige tocar `deploy/nas/swissjob.configured.yml` o el
>    `.env` del NAS → **mismo bloqueo que 1.1**, no un arreglo libre.
> 2. **A-10 no es «decisión + ~30 líneas», y no es tarea de código.** El cuerpo de
>    `send_watchlist_digest` son ~60 líneas de lógica real (marcadores de
>    idempotencia, marca de agua, envío). Reescribirlo contra el feed del núcleo
>    tiene el tamaño que tuvo DT-148 A1, que era un ítem entero; retirarlo es
>    decidir si la capacidad se quiere. **Es del propietario.**
> 3. **Faltaba un PREREQUISITO que esta misma auditoría había encontrado (A-65):**
>    la suite del BFF **no se puede lanzar** — `ModuleNotFoundError: No module
>    named 'pytest'`. Cada ítem de `backend/` necesita layerear una imagen de test
>    antes, y cada corrida son 10-12 minutos. Eso cambia el AGRUPAMIENTO, no sólo
>    el coste.
>
> **Agrupamiento correcto**, que es lo que de verdad se puede hacer «de una
> tirada»:
>
> - **Paso 0, coste único:** layerear la imagen de test del BFF
>   (`requirements-dev.txt` son 4 líneas), o añadir un servicio `backend-dev`
>   —que es además el arreglo de A-65 y conviene hacerlo bien una vez.
> - **Tanda de BACKEND, una sola corrida de suite al final:** 1.3 (A-17) + 1.8
>   (A-68) + 1.4 (A-13/A-18).
> - **Tanda de FRONTEND, aparte y rápida** (la suite tarda 1,2 s): 1.5 (A-11) +
>   1.6 (A-12).
> - **Bloqueados a tu confirmación** (tocan compose, `.env` o `nginx.conf`): 1.1,
>   1.2 y 1.7.

| # | Qué | Por qué aquí | Coste MEDIDO (2026-11-03) |
|---|---|---|---|
| **1.1** | **A-61 + A-62** · `--requirepass` y `noeviction` en el redis del NAS, y `check_compose_exposure.py` sobre **los nueve** composes | Verificado ejecutando: `PONG` sin credencial, `allkeys-lru` sobre un broker. El guard existe y **mira el único fichero que está bien** | ~20 líneas + despliegue · **BLOQUEADO: toca compose** |
| **1.2** | **A-16** · `RATE_LIMIT_TRUST_PROXY=true` + test que fije el `proxy_set_header` | Hoy son **5 logins/minuto para todo el despliegue**, y la precondición de seguridad ya la cumple `nginx.conf:39` | 1 variable · **BLOQUEADO: no está en ningún compose, hay que añadirla al del NAS o a su `.env`** |
| **1.3** | **A-17** · que el rate limiter falle ABIERTO ante un fallo del almacén | Hoy un corte de Redis devuelve **500 en toda la API**, `/health` incluido | **~6 líneas** en un fichero de 68. Pequeño de verdad · necesita la imagen de test |
| **1.4** | **A-13 + A-18** · cota de descompresión y firma del fichero en la subida de CV | Un solo parche cierra los dos. Una petición autenticada mata el worker por OOM con `mem_limit: 1536m` | **~30-40 líneas en dos ficheros + tests** (no 25). `cv_parser.py` son 102 líneas: es MEDIO, no corto |
| **1.5** | **A-11** · `authApi.logout()` y llamarlo desde `useLogout` | **Resucita el mecanismo entero de T9**, hoy muerto | 1 método + 1 llamada + test · **el único corto y desbloqueado de la tanda** |
| **1.6** | **A-12** · ventana de gracia en el canje del refresh (BCP de OAuth 2.1) | Dos pestañas abiertas = logout sorpresa de las dos. Y **envenena la señal de robo** | ~15 líneas en `token_store.py` + listener de `storage` en el frontend · toca los DOS lados |
| **1.7** | **A-25** · cuatro `add_header` en `nginx.conf` | Única capa ante XSS es DOMPurify, con el JWT en `localStorage` | 4 líneas · **confirmación: la CSP hay que verificarla contra el bundle de Vite antes de desplegar** |
| **1.8** | **A-68** · `assert` de host/entorno en `_ensure_test_db` | El proyecto YA truncó producción una vez con el teardown. El **nombre** está fijado; el **servidor** no | ~4-8 líneas, **pero en `conftest.py`**: un error ahí pone TODA la suite en rojo. Necesita la imagen de test y diseñar la válvula de escape |

### Tanda 2 — lo que miente en verde (observabilidad)

Esta tanda es la de mejor retorno por línea, porque todo lo de aquí **hoy informa
de salud mientras no funciona**.

| # | Qué | Coste |
|---|---|---|
| **2.1** | **A-10** · retirar o reescribir `send_watchlist_digest` (hoy devuelve `ok` sin poder notificar nada) | **DECISIÓN DEL PROPIETARIO, no tarea de código** (re-medido el 2026-11-03): el cuerpo son ~60 líneas de lógica real —marcadores de idempotencia, marca de agua, envío— y lo cubre `tests/test_scheduler.py`. Reescribirlo contra el feed del núcleo tiene el tamaño que tuvo DT-148 A1, que era un ítem entero. La estimación anterior («decisión + ~30 líneas») lo subestimaba |
| **2.2** | **A-22** · `return_exceptions=True` + `bind=True` en `check_watchlist_health` (una fuente tumba las ocho; cualquier fallo sale SUCCESS) | ~8 líneas |
| **2.3** | **A-23** · derivar `_WATCHLIST_SOURCES` del núcleo (le falta `swiss_schools_vis`) o test que compare | ~10 líneas |
| **2.4** | **A-20** · 404 de Groq como error de CONFIGURACIÓN, no transitorio (hoy reintenta en vano para siempre) | ~10 líneas |
| **2.5** | **A-03** · no sellar `_feed_verified_at` sin confirmar la versión | ~10 líneas |
| **2.6** | **A-19** · publicar la invalidación del overlay escolar en el `cache_bus` | ~10 líneas |
| **2.7** | **A-31** · componente `<QueryState>` y aplicarlo a los **ocho** sitios (cierra la mitad abierta de DT-148 A18-03) | ~80 líneas |
| **2.8** | **A-21** · decidir y cerrar el escritor latente de `match_results` | decisión + ~20 líneas |

### Tanda 3 — contrato de la cosecha (un arreglo, no cuatro)

**A-04 + A-05 + A-06 + A-07 son el mismo problema**: la terna
`(complete, error, blocked_with)` no está fijada por ningún test. En vez de
parchear provider a provider —que es lo que ya se hizo dos veces y dejó fuera a
unos cuantos— se hace **la tabla de contrato de 32 × 4** que el auditor del núcleo
propone, se arregla lo que destape, y **se fija con un test sobre el registro**.

Justificación del orden: va DESPUÉS de las tandas 1 y 2 porque **el detonante de
A-04 está desarmado hoy en producción** (verificado: las siete fuentes con
`fallos=0`, `bloqueos=0`, cosecha completa reciente, y `jobgether` SÍ declara
`max_pages`). Es trampa latente, no urgencia.

### Tanda 4 — infraestructura y despliegue

| # | Qué |
|---|---|
| **4.1** | **A-14 + A-29** · versionar `deploy/nas/erasure.configured.yml` desde el `inspect` capturado, y reconstruir el contenedor con la imagen vigente. Van juntos: hoy **no hay compose con el que recrearlo** |
| **4.2** | **A-61 (2ª mitad)** · meter `redis` y `postgres` del BFF en `swissjob.configured.yml`, que sólo define tres servicios |
| **4.3** | **A-63** · migrar ANTES de promover el compose en `deploy_nas.sh`, y `trap ERR` que restaure |
| **4.4** | **A-66** · regex de formas en `check_compose_exposure.py` (hoy busca **un** literal quemado y se le cuelan dos credenciales en nueve sitios) |
| **4.5** | **A-70** · ratchets que no se desactiven solos al faltar su clave |
| **4.6** | **A-64** · los seis tests huérfanos de `scripts/` al CI (dos son de *contrato*) |
| **4.7** | **A-67** · `CELERY_MAX_TASKS_PER_CHILD` y `CE_BACKEND` a `Settings`, y `CE_BACKEND=onnx-cpu` en producción **antes** de que la promoción del CE lo haga urgente |
| **4.8** | **A-71 / A-72 / A-73** · construir los `Dockerfile.prod` en CI, fijar las imágenes base por digest, validar los composes de `deploy/nas/` |
| **4.9** | **A-65** · servicio `backend-dev` o documentar la imagen auxiliar, y corregir los comandos de `CLAUDE.md` que **no funcionan** |

### Tanda 5 — DT-148 bloques C y D, con el inventario CORREGIDO

**Sólo ahora.** Y con los nueve ajustes que la verificación destapó. Orden
revisado respecto al relevo:

1. **Antes de nada, dos líneas en el plan que evitan dos tests verdes-mentirosos:**
   - **D3 paso 4** debe incluir `_lock_vacancies` y `_write_dedup_candidates` en
     su lista de costuras. El plan afirma «grep: 0» y hay **dos**:
     `test_integration_archive.py:329-338` hereda y **sobreescribe
     `_lock_vacancies`** para reproducir una carrera; si deja de ser método, **el
     test pasa sin reproducir nada**.
   - **D1** debe asignar `MAX_SCAN_TUPLES` a un módulo explícito y re-apuntar
     `dedup.py:45` y `test_integration_matching.py:782`, que lo parchea para
     forzar la inanición.
2. **C3 antes que D1** — cambio respecto al relevo. Son **5** bloques de tenencia
   (no 4; el quinto en `v1.py:713-719`), el helper los cubre todos, es el único
   punto de C con riesgo nulo, y **no depende de D1**. Buen primer bocado real.
3. **D1** (`matching.py`, MI C confirmado 0.59) → **D3** (`RawListingSink`) →
   **C1** → **C2, C4, C5** → **C6** (recalculado: B3 ya invalidó dos de sus
   cifras) → **C7, C8** → **D2, D4, D5, D6, D7**.
4. **Recalcular TODA ubicación antes de tocarla**: `api/v1.py` se desplazó **+98
   líneas** desde que el plan se escribió, `sink.py` +5, `_persist_eval_rows` −68.
   El §8 del relevo manda cortar por rango verificado; con las líneas del plan se
   cortarían 98 líneas equivocadas.
5. **Recortes justificados:** **D4 a la mitad** (`review_suggestion` CC B(8) y
   `delete_all_user_data` CC A(5) **ya cumplen el criterio de cierre del propio
   punto**), y **la fila de `FilterPanel` de D7 está CADUCA** (A11 ya retiró el
   desplegable).
6. **Dos puntos nuevos al ledger antes de cerrar DT-148:** **A-32**
   (`native_json.py` es un **segundo** fichero con MI C, lo que contradice la
   premisa de D1) y **A-33** (`check_source_ownership.py:242` con **CC 80**, el
   peor número del repo, sin cota que lo ampare, en el primer guard del CI).

### Tanda 6 — accesibilidad, i18n y el resto

A-37, A-38, A-39, A-26, A-27, A-28, A-35, A-36, A-52…A-60, A-74…A-79. Todo
acotado y sin riesgo; se hace en bloque cuando lo anterior esté cerrado.

---

## 3. Lo que NO voy a hacer, y por qué

- **Rotar secretos** (A-02 paso 1, A-49): es del propietario y hay una decisión
  pendiente desde el 2026-10-21.
- **Tocar `.env` o los `docker-compose.yml`** sin confirmación explícita: lo
  prohíbe `CLAUDE.md`. Las tandas 1.1, 1.2 y 4.x **preparan** los cambios y los
  dejan a la vista.
- **Desplegar** nada hasta que `github/main` incorpore el PR del recibo.
- **Abrir o fusionar PRs**: no hay `gh` ni token.
- **A-08 (recorrido de búsquedas guardadas)**: producción desmiente el bucle. Se
  queda en la tanda 5 como mejora de crecimiento, no como urgencia.
- **A-40** (`shadow_change_log`): `core0056` la conservó a propósito; se retira con
  las encarnaciones `legacy:*` (~enero 2027). Sólo una nota que lo ate a esa fecha.

---

## 4. Disciplina por cada tanda

Tomada de lo que esta auditoría ha vuelto a demostrar:

1. **Verificar ejecutando.** En esta pasada la ejecución desmintió **tres** cosas
   que parecían ciertas leyendo: el log de `erasure-cdc`, el detonante de A-04 y
   el bucle de A-08. Y confirmó dos que parecían exageradas: A-01 en el bundle
   servido y el redis abierto.
2. **Control negativo en cada test nuevo.** Romper lo que se acaba de arreglar y
   comprobar que el test se pone rojo. Sin esto, 0.2 no vale nada.
3. **Buscar consumidores INCLUYENDO los tests.** Excluirlos ocultó dependencias
   reales tres veces en lo ya ejecutado, y otras dos en lo verificado hoy (H1, H2).
4. **Un guard que busca valores concretos en vez de formas no es un guard.** Es el
   error común de A-02 y A-66, y lo que hace que un CI en verde no signifique nada.
5. **Cortar por rango de líneas verificado**, nunca por patrón de texto.
6. Cada tanda termina con los **nueve checks en local** antes de pedir el merge.
