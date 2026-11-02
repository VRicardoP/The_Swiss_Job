# Auditoría técnica — SwissJobHunter · 2026-11-02

> **Pasada fresca con cinco auditores en paralelo** (núcleo, BFF, seguridad,
> frontend, infra/tests, arquitectura) sobre `HEAD = 89e0e5f`, más verificación
> propia del orquestador contra PRODUCCIÓN.
>
> **Qué NO está aquí:** la deuda ya inventariada en `DEUDA_TECNICA.md` §DT-148
> (bloques C y D abiertos), DT-146, DT-147 y las ~150 cotas de
> `COTAS_Y_DECISIONES.md`. Se pidió explícitamente no repetirlas. Lo que SÍ está:
> **hallazgos nuevos** y **evidencia que invalida lo que el inventario da por
> cierto**.
>
> **Disciplina aplicada:** cada hallazgo marcado CONFIRMADO se verificó leyendo
> el código o ejecutando contra producción. Los PROBABLE dicen qué falta para
> confirmarlos. Dos lecturas mías resultaron equivocadas y están rectificadas
> aquí mismo (A-03 y el log de `erasure-cdc`), porque un informe que esconde sus
> propios errores no sirve para decidir.

---

#---

## ESTADO DE EJECUCIÓN (2026-11-02, misma sesión)

Se ejecutaron **sólo las dos CRITICAL y lo imprescindible para cerrarlas**. El
resto queda planificado en `docs/unificacion/PLAN_ATAQUE_2026-11-02.md`.

| Hallazgo | Estado | Verificación |
|---|---|---|
| **A-01** `/match` en blanco | **CERRADO** | Acceso sacado del array de dependencias + `ErrorBoundary` por ruta. Confirmado en el artefacto COMPILADO: el bundle de producción tenía `m.top_score,n`, el nuevo tiene `u?.top_score,T`. Frontend: **67 tests en verde** (eran 63), eslint limpio, build correcto |
| **A-01** falta de cobertura | **CERRADO** | `MatchPage.firstrender.test.jsx`, 4 casos del primer render **sin dependencias nuevas** (`renderToStaticMarkup` ejecuta el cuerpo del componente, que es donde estaba el fallo). **Control negativo:** reintroducido el defecto, fallan 3 de 4 con el `TypeError` original y el cuarto sigue pasando — el test discrimina |
| **A-02** ceguera del guard | **CERRADO** | `check_no_secrets.py` entiende ahora sintaxis `.env` y URLs con credencial embebida, con criterio por FORMA (umbrales calibrados midiendo los valores reales de `docs/`). **8 tests nuevos** (3 → 11). **Control negativo:** desactivada la detección, fallan exactamente los 3 que afirman detección |
| **A-02** fuga en el árbol | **CERRADO** | 4 ocurrencias sustituidas por `${POSTGRES_PASSWORD}`. `git grep` del literal → **0 en el árbol de trabajo** |
| **A-02** alcance del CI | **CERRADO** | `ci.yml` ejecuta `check_no_secrets.py deploy/nas docs` → **106 ficheros, 0 hallazgos**. El orden fue deliberado: arreglar la ceguera ANTES de ampliar el alcance, o el CI habría pasado en verde con el secreto dentro |
| **A-02** rotación e historial | **ABIERTO — del propietario** | **La credencial sigue en el historial de git y en `github/main`.** Limpiar el árbol no deshace lo publicado. Orden correcto: rotar el rol `swissjob` → luego decidir sobre `git filter-repo` |

**Dos hallazgos quedaron rebajados al verificarlos contra producción**, y la
corrección está en sus fichas: **A-04** (el detonante está desarmado:
`jobgether` SÍ declara `max_pages`, las siete fuentes con `fallos=0`) y **A-08**
(no hay bucle absorbente; su mitad real es el 1,15 M de tombstones).

**Y una estimación MÍA quedó corregida el 2026-11-03.** Dije que los seis
arreglos siguientes eran «todos de menos de treinta líneas y no tocan composes»
y se podían encadenar sin pararse. Medido, estaba mal en tres cosas: **A-16 sí
está bloqueado** (la variable no vive en ningún compose), **A-10 no es tarea de
código** sino una decisión, y **faltaba el prerequisito A-65** — la suite del BFF
no se puede lanzar, así que ningún cambio de `backend/` es verificable hasta
resolverlo, y a 10-12 min por corrida eso obliga a agrupar. Los tamaños reales
están en `PLAN_ATAQUE_2026-11-02.md` §Tanda 1. **Una estimación equivocada en un
plan es deuda igual que el código**, y por eso se deja escrita en vez de
corregirse en silencio.

**No hay segunda fuga de credenciales**: los otros 10 hallazgos que el guard
nuevo levantó sobre `docs/` se clasificaron uno a uno y son todos marcadores
(`***` ya redactado, `audit-not-a-real-secret`, dummies `ci` del CI,
anotaciones de Python citadas). El guard quedó en **0 falsos positivos**.

**Sin commit**: `CLAUDE.md` prohíbe commit automático sin aprobación explícita.

---

# Resumen ejecutivo

La auditoría encontró **dos fallos CRITICAL vivos en producción que no estaban
en ninguna lista de deuda**, y ambos llevan semanas ahí:

1. **La pantalla principal del producto está en blanco** desde el 20-10 (13 días)
   para quien entre directamente en `/match`.
2. **Una credencial de producción está commiteada y publicada** en el remoto de
   GitHub, y el guard que debería impedirlo es estructuralmente incapaz de verla.

Por encima de eso, **la auditoría de arquitectura corrigió el propio plan
DT-148 en nueve puntos**, dos de los cuales habrían dejado tests en VERDE que ya
no prueban nada. Eso cambia el orden de ejecución recomendado.

**Conclusión operativa:** la deuda estructural de DT-148 (bloques C y D) **no es
lo más urgente**. Hay once arreglos de corrección y seguridad, la mayoría de
menos de veinte líneas, que valen más que cualquier refactor pendiente.

---

# CRITICAL

## A-01 · `/match` revienta en el primer render y deja la aplicación EN BLANCO
**Área:** `frontend/src/pages/MatchPage.jsx:178,185` · **CONFIRMADO en el bundle de producción**

`MatchPage.jsx:77` hace `const facets = facetsQuery.data;` **sin `?? {}`**, y
`useMatchFacets` (`hooks/useMatch.js:15-21`) no tiene `initialData` ni
`placeholderData` y lleva `retry: false`: en el primer render vale `undefined`.
Las líneas 178 y 185 ponen `facets.top_score` y `facets.strong_count` **dentro
del array de dependencias** de sus `useMemo`. Un array de dependencias se
construye en CADA render, al margen del ternario que protege el cuerpo: el
acceso ocurre antes de que `useMemo` llegue a llamarse. Los usos de `facets` en
las líneas 79, 103, 108 y 112 **sí** van guardados por `facetsEnabled`; los dos
de las dependencias son los únicos desnudos.

**No hay red debajo:** `grep -rn "ErrorBoundary|componentDidCatch|getDerivedStateFromError" frontend/src/`
→ **cero coincidencias**, y el router es `<BrowserRouter>` + `<Routes>`
(`main.jsx:21`, `App.jsx:57`), el NO-data router, que no admite `errorElement`.
El error sube a la raíz de React y desmonta el árbol completo.

**Nada precarga las facetas:** sólo dos consumidores (`MatchPage:45`,
`WatchlistPage:59`) y **cero** `prefetchQuery`. La ruta por defecto es
`SearchPage` (`App.jsx:58`).

**Verificado en producción, no en el fuente:** `git blame` → `922439f`
(2026-10-20); `git merge-base --is-ancestor 922439f 14438b7` → cierto, y
`14438b7` es el frontend desplegado. Dentro de `swissjob-frontend`,
`/usr/share/nginx/html/assets/MatchPage-rS5iEZig.js` contiene **`m.top_score,n`**:
el acceso desnudo, sin encadenamiento opcional, en el artefacto que se sirve.

**Impacto:** pantalla en blanco al entrar directamente en `/match` (enlace del
navbar, URL directa o F5). El único camino que funciona es pasar antes por
`/watchlist`, que llena la caché `["match-facets"]` — por eso ha podido pasar
13 días inadvertido.

**Fix:** sacar el acceso del array de dependencias (`const facetsTop = facets?.top_score`)
**y** añadir un `ErrorBoundary` por ruta, que es el fallo de fondo. **Y el fix de
proceso, que importa más:** ningún test monta `MatchPage`, `WatchlistPage`,
`ProfilePage`, `SearchPage` ni `LoginPage`. Un render del primer estado por
página —el patrón que ya usan `App.test.jsx` y `FiltersPage.test.jsx`— habría
parado esto por ~40 líneas.

## A-02 · Credencial de producción commiteada, publicada, y un guard que no puede verla
**Área:** `docs/DEPLOY_NAS.md:1543-1555` + `scripts/check_no_secrets.py:19-27` · **CONFIRMADO**

`docs/DEPLOY_NAS.md` pega la salida literal de una sesión de depuración sobre el
NAS, incluido un `grep -E "^POSTGRES_|^DATABASE_URL" .env.prod`. Las líneas
contienen un valor con forma de contraseña generada (cadena larga aleatoria) como
`POSTGRES_PASSWORD`, y la `DATABASE_URL` completa con esa credencial embebida. La
línea 387 del MISMO documento sí la redacta (`<el-mismo-en-las-dos-líneas>`), lo
que confirma que es un descuido y no una convención. Está en el árbol, en el
historial (desde `3443127`, *«feat(ops): T13 — topología versionada…»*) y
**en `github/main`** (verificado con `git merge-base --is-ancestor`).

**Lo que hace este hallazgo peor de lo que parece — y corrige la recomendación
obvia.** El guard del proyecto **no lo detecta**, y no por estar apuntado a otro
directorio: `python3 scripts/check_no_secrets.py docs/DEPLOY_NAS.md` devuelve
**«1 ficheros, 0 hallazgos»**. La causa está en los dos regex: `CLAVE_SENSIBLE`
(`:19-22`) y `LITERAL_LARGO` (`:25-27`) exigen los dos un separador `\s*:\s*`,
que es sintaxis **YAML**. La fuga está en sintaxis **`.env`**, con `=`.
Demostrado con secretos sintéticos:

| Línea de prueba | Resultado del guard |
|---|---|
| `POSTGRES_PASSWORD: <falso>` (YAML, dos puntos) | **1 hallazgo** — lo ve |
| `POSTGRES_PASSWORD=<falso>` (`.env`, igual) | **0 hallazgos** — CIEGO |

Por tanto **«añadir `docs/` al paso del CI» NO arregla nada**: el guard correría,
pasaría en verde y la credencial seguiría ahí. Es la peor clase de arreglo — el
que produce sensación de seguridad. El guard sólo entiende composes, que es para
lo que se escribió (A19-04), pero `docs/` está lleno de salidas de `.env` pegadas.

**No pude confirmar si la credencial sigue viva:** intenté compararla contra la
de producción sin imprimirla y el clasificador de permisos lo bloqueó, con razón.
No lo rodeé. Esa comparación es del propietario. Nótese que
`DT148_RELEVO.md` §5 ya tiene **«Rotación de secretos · Pendiente de confirmar»**
por una fuga anterior (tres credenciales en una transcripción, 2026-10-21): esto
es un **segundo vector**, y peor, porque una transcripción no se clona.

**Fix, en este orden:** (1) rotar la contraseña del rol `swissjob`; (2) sustituir
las ocurrencias por `${POSTGRES_PASSWORD}`; (3) **enseñar al guard la sintaxis
`.env`** (un segundo regex con `=`) y apuntarlo a `docs/` — en ese orden, porque
el paso 3 sin el 1 es cosmético; (4) considerar `git filter-repo` sólo DESPUÉS de
rotar: reescribir el historial no deshace lo publicado.

---

# HIGH

## A-61 · El redis del BFF corre en PRODUCCIÓN SIN CONTRASEÑA, y el guard que lo detectaría nunca mira el fichero equivocado
**Área:** `deploy/nas/containerstation-swissjob.yml:34-56` + `.github/workflows/ci.yml:252` · **CONFIRMADO EJECUTANDO CONTRA PRODUCCIÓN**

Comprobado en la máquina, no en un fichero. El contraste es lo que lo prueba:

```
swissjob-redis        → redis-cli ping → PONG          ← SIN credencial
swissjob-redis-core-r5 → redis-cli ping → NOAUTH …     ← correctamente protegido
```

`config get requirepass` devuelve vacío. Y es alcanzable desde cualquier
contenedor de la red: verificado con un socket crudo desde `swissjob-backend`
hacia `redis:6379` → `+PONG`. **Radio de alcance medido** (`network inspect
swissjob_swissjob-net`): 12 contenedores — los 9 de SwissJob más
`swissjob-erasure-cdc`, `portfolio_backend` y `portfolio_worker`. No alcanza a
los contenedores ajenos del NAS (kavita, novafeed, reddit-gallery, seshat), así
que el radio son servicios del propietario y el host; por eso es HIGH y no
CRITICAL. Pero es exactamente lo que C7 se propuso cerrar el 2026-09-24 («`redis`
además **sin contraseña**»): se cerró en el compose local y **quedó abierto en el
desplegado**.

**La razón de que el guard no lo vea es estructural, y es el hallazgo real.**
`scripts/check_compose_exposure.py` tiene `_revisa_auth_de_redis` precisamente
para esto (condición 3 de su docstring), pero el CI lo invoca con **un solo
fichero**: `python3 scripts/check_compose_exposure.py docker-compose.yml`. Y
`docker-compose.yml` **sí** lleva `--requirepass` (`:47`, `:151`). O sea: **el
guard comprueba el fichero que está bien y nunca mira el que está mal.** De los
nueve composes del repo, sólo uno pasa por él.

**Y hay un segundo agujero detrás, peor:** `deploy/nas/swissjob.configured.yml`
—el fichero que A19-04 versiona como «la topología REAL del NAS» y que
`deploy_nas.sh bff` retagea— contiene **sólo tres servicios**: `backend`,
`worker` y `frontend`. **No define `redis` ni `postgres`.** Los dos únicos
ficheros de `deploy/nas/` con una definición de redis son
`containerstation-swissjob.yml` y `rehearsal-r5.yml`. Así que la afirmación «la
topología REAL del NAS está versionada en `deploy/nas/`» es **incompleta en dos
sitios**: le falta `swissjob-erasure-cdc` (A-14) y le faltan los servicios de
datos del BFF.

**Fix:** `--requirepass` + healthcheck autenticado (copiar el patrón de
`docker-compose.yml:33-56`), y **ejecutar `check_compose_exposure.py` sobre los
nueve composes**, `deploy/nas/*` incluidos. Nótese que el orden importa: poner la
contraseña exige que `REDIS_PASSWORD` llegue al contenedor y que el BFF y los dos
workers la lleven en su URL, o se reproduce el bucle de `NOAUTH` que `CLAUDE.md`
documenta.

## A-62 · Ese mismo redis es un broker con política de EXPULSIÓN
**Área:** `deploy/nas/containerstation-swissjob.yml` · **CONFIRMADO EJECUTANDO**

Medido en producción: `maxmemory-policy allkeys-lru`, `maxmemory 512 MB`. Y el
keyspace dice que **es el broker**, no sólo caché:

```
db0: 255 claves (253 con TTL)   ← caché
db1:   5 claves, expires=0      ← broker de Celery: SIN TTL
db2: 290 claves (290 con TTL)   ← resultados
used_memory_human: 2.12M / 512M
```

`docker-compose.yml:149` documenta la regla contraria para `redis-core`:
*«noeviction (un broker jamás expulsa mensajes ni locks)»*. Las 5 claves de `db1`
sin caducidad son justo las que `allkeys-lru` elegiría bajo presión. El redis
legacy sirve `default`/`ai`: purga de refresh, watchlist, digest, idioma de
títulos y alerta de profesor. Bajo presión de memoria **se pierden tareas y
resultados en silencio**, sin error ni reintento.

Hoy no muerde (2,12 MB de 512 MB), lo que hace el arreglo barato y sin riesgo.

**Fix:** `noeviction` en el broker, o separar caché y broker como ya hace el
núcleo.

## A-63 · `deploy_nas.sh` promueve el compose ANTES de migrar, y el rollback sólo se imprime si todo fue bien
**Área:** `scripts/deploy_nas.sh:237-241` vs `243-270`, `341` · **CONFIRMADO**

El orden real es: `mv $CANDIDATO $E/$COMPOSE` (`:240`) → **luego** las migraciones
del núcleo (`:248-269`). Con `set -euo pipefail` y un `trap` que sólo borra `$TMP`
(`:145`), un fallo de `jobhunt_core.migrate` deja tres cosas desalineadas: base
con cabeza nueva (quizá parcial), compose apuntando a la imagen nueva, y
contenedores con el código VIEJO → `/v1/ready` en 503, `core-api` `unhealthy`, y
`restart: unless-stopped` no lo arregla. **La instrucción de rollback se imprime
en la línea 341, es decir sólo en el camino de éxito**; igual si fallan las
esperas de `healthy` (`:307`, `:325`).

Lo que sí hace bien, por si se duda: recrea los **cuatro** servicios del core y
verifica la imagen de los cuatro (`:87`, `:332-335`).

**Fix:** migrar **antes** de promover el compose, y un `trap ERR` que restaure
`$COMPOSE.before-$SERVICIO-$SHA`.

## A-64 · Seis de los diez tests de `scripts/` no los ejecuta nada
**Área:** `.github/workflows/ci.yml:58-61` · **CONFIRMADO**

CI lanza exactamente cuatro. Fuera de todo runner quedan **seis**:
`test_document_adapter_contract.py`, `test_document_inbox_contract.py`,
`test_frontend_proxy_isolation.py`, `test_measure_nas_resources.py`,
`test_portfolio_document_transactions.py`, `test_search_handover_contract.py`.

No hay escapatoria: `backend/pytest.ini` fija `testpaths = tests`, la suite del
núcleo recoge `jobhunt_core/tests` + `tools/core/tests`, y no existe `Makefile` ni
`.pre-commit-config.yaml`. **La lección de DT-148 B5 («un test que nadie lanza no
protege nada») se aplicó a cuatro de diez.** Y dos de los seis huérfanos son
*contratos* (`document_adapter`, `search_handover`), que es la clase de test cuyo
silencio cuesta más caro.

Sin test alguno: `check_compose_exposure.py`, `rotate_credential.py` (rotación con
solape), `backup_retention.py` (retención de copias) y `check_source_ownership.py`
(el F de A-33). **`split_core_workers.py` sigue invocado por `deploy_nas.sh:168` y
su test se borró** en `1f415d3 refactor(core): retire consumed cutover tooling`
(2026-10-18), junto a herramientas que sí se retiraron — pero ésta no.

## A-65 · Los comandos canónicos de test y lint del BFF NO funcionan sobre una imagen limpia
**Área:** `CLAUDE.md` «Comandos clave» + `DT148_RELEVO.md:175-178` · **CONFIRMADO**

`backend/Dockerfile:9-10` instala sólo `requirements.txt`, de donde
`68f21c6 build: slim and unify production images` sacó `pytest` y `ruff` (hoy en
`requirements-dev.txt`, excluido del contexto por `backend/.dockerignore`). Y **no
es reversible por descuido**: `check_source_ownership.py:89-96` **prohíbe**
explícitamente `pytest`, `pytest-asyncio` y `ruff` en `backend/requirements.txt`.

Aun así `CLAUDE.md` documenta `docker compose exec -T backend python -m pytest
tests/ -q` y `ruff check --no-cache .`, y el relevo los repite en su lista de los
nueve checks locales — **pese a avisar tres líneas antes de que «ruff desapareció
de la imagen del backend»**. El comentario final de `backend/requirements.txt`
(«`ruff` va a la MISMA versión que fija el CI») quedó huérfano: ya no hay ninguna
línea `ruff==`.

**Fix:** documentar la imagen auxiliar (como ya se hace para el núcleo) o añadir
un servicio `backend-dev` que instale `requirements-dev.txt`. Es DX puro, pero es
el primer tropiezo de cualquiera que retome el proyecto.

## A-03 · `warm_feed` sella como «verificado» un recorrido cuya versión NO se pudo confirmar
**Área:** `backend/services/matching/core_client.py:463-467,561-573` · **CONFIRMADO**

```python
items, _total = await self._fetch_full_feed(core_profile_id, None)
pid = str(core_profile_id)
if pid in _feed_cache:
    _feed_verified_at[pid] = time.monotonic()
```

`_feed_verified_at[pid]` es el ÚNICO criterio de frescura del atajo por categoría
(`:561-573`, sin consultar la versión), y aquí se refresca por el mero hecho de
que exista una entrada en `_feed_cache` — no por haberla confirmado. Tres caminos
llegan con una entrada VIEJA y la sellan como fresca: `_feed_version` devuelve
`None`; `_remember_feed` declina por `len(items) != total` (`:1065`); o
`_maybe_remember` declina por versión cambiada a mitad.

El segundo **no es hipotético**: `CLAUDE.md` §6 documenta que `feed()` NO excluye
vacantes sin canónica a propósito mientras recuento y versión SÍ las excluyen, así
que la discrepancia es el **régimen esperado**.

**Impacto:** con el calentador cada 60 s el sello se renueva indefinidamente y
`GET /match/results?category=X` sirve un recorrido de cuando la caché se llenó por
última vez. Rompe la cuarta cota del feed («sin versión fiable se recorre») por la
puerta de atrás. Es la clase de regresión que cerró T2.

**Fix:** que `_fetch_full_feed`/`_recorrer_feed` devuelvan si el resultado fue
*confirmado contra la versión*, y sellar sólo entonces. Mínimo:
`_remember_feed` escribe `_feed_verified_at` él mismo y `warm_feed` no lo escribe
nunca.

## A-04 · Cuatro providers reportan su PROPIO presupuesto como FALLO DE LA FUENTE
**Área:** `native_jobgether.py:205,281` · `native_irishjobs.py:164` · `native_gastrojob.py:502,544` · `native_schuljobs.py:382,415` · **CONFIRMADO**

`harvest/types.py:28-32` fija el contrato: `error` significa «barrido incompleto
**POR FALLO**», frente al incompleto «por el tope de páginas». `runner.py:224` lo
aplica literalmente (`failed = result.error is not None`). `arbeitnow.py:199-213`
lo tiene bien **y lo deja escrito**: *«PRESUPUESTO agotado: parcial, pero NI fallo
de la fuente (no cuenta backoff: la fuente no hizo nada mal)»*, con un campo
`timed_out` separado. Los otros cuatro escriben el mismo evento en `error`.

Cadena de consecuencias, toda en el código:
1. `failure_backoff_hours` (`tasks/harvest.py:240-248`) lleva el scope a 48 h,
   anulando su cadencia declarada (jobgether es de **6 h**).
2. `last_complete_at` no se refresca jamás → `health.py:214` emite
   `cosecha_sin_completar` en **cada** pasada horaria, para siempre; y `:197`
   emite `cosecha_fallando` a los 3 fallos. **Dos alertas permanentes que se
   aprenden a ignorar.**
3. `runner.py:253` sólo resetea `consecutive_blocks` con cosecha COMPLETA: un
   scope permanentemente «parcial por error» nunca lo resetea, así que tres
   bloqueos aislados a lo largo de meses **apagan la fuente con el kill-switch**.
   Es la patología que DT-148 E1 cerró para los items ilegibles: E1 tapió una
   puerta y éstos dejaron otra abierta.
4. Las vacantes que el barrido truncado nunca alcanza dejan de refrescar
   `last_seen_at`, y `archive.py` rama 2 las archiva a los 120 d **estando
   publicadas**.

### VERIFICADO CONTRA PRODUCCIÓN — el detonante está DESARMADO hoy

El auditor predijo que `jobgether` probablemente no declaraba `max_pages` (no
existe `provision_jobgether.py`), lo que haría saltar la rama `:281-283` en cada
barrido. **Consultada la base de producción, es falso.** Estado real el
2026-11-02:

| Fuente | `params` | fallos | bloqueos | `last_complete_at` | apagado |
|---|---|---|---|---|---|
| `jobgether` | `{"max_pages": 3, "admission_window_days": 7}` | **0** | 0 | 2026-11-02 18:31 | None |
| `irishjobs` | `{"max_pages": 3, "admission_window_days": 7}` | **0** | 0 | 2026-11-02 21:04 | None |
| `schuljobs` | `{"max_pages": 4}` | **0** | 0 | 2026-11-02 20:34 | None |
| `gastrojob` | `{}` | **0** | 0 | 2026-11-01 22:40 | None |
| `himalayas` | `{}` | **0** | 0 | 2026-11-02 18:33 | None |
| `swisstechjobs` | `{}` | **0** | 0 | 2026-11-01 22:57 | None |
| `ictjobs` | `{}` | **0** | 0 | 2026-11-01 22:42 | None |

Las siete con `consecutive_failures = 0`, `consecutive_blocks = 0`, cosecha
completa reciente y ninguna apagada. **El defecto de clasificación está
CONFIRMADO en el código; su impacto productivo hoy es NULO**, igual que el
auditor ya había concluido para `schuljobs` («su detonante está desarmado por
configuración, no por el código») — resulta que vale para los tres.

**Esto baja su prioridad**: es una trampa LATENTE que dispara el día que alguien
quite `max_pages` de un scope, provisione una fuente nueva sin él (y el defecto
del código es no tenerlo), o un barrido agote `SWEEP_BUDGET_S` bajo carga. Sigue
mereciendo arreglo —porque el arreglo es trivial y la trampa es silenciosa— pero
**no es una urgencia de producción.**

**Fix:** separar los dos conceptos en `FetchResult` como ya hace arbeitnow
(`exhausted=False` → `complete=False` **con `error=None`**) y reservar `error`
para el fallo real.

## A-05 · `native_json` declara `complete=True` aunque su propio tope truncó el feed
**Área:** `jobhunt_core/harvest/providers/native_json.py:631-641` · **CONFIRMADO**

El error simétrico de A-04: `complete = fetch_error is None`, sin noción de
`exhausted`. El barrido de `himalayas` (3 × 20), `swisstechjobs` (2 × 100) e
`ictjobs` (5 × 20) se declara COMPLETO sin comprobar que el feed se agotara. Como
la paginación de himalayas es por **offset desde 0 en cada barrido**, lo que esté
más allá de la oferta 60 **no es alcanzable nunca**, y el estado dice verde:
`last_complete_at` fresco, `consecutive_failures` a 0, health en verde. Esas
vacantes dejan de refrescar `last_seen_at` y se archivan a los 120 d estando
publicadas.

**Fix:** `complete=False` cuando la última página volvió llena. Va junto con
A-04, que es lo que impide que ese `complete=False` cuente como fallo.

## A-06 · `native_schuljobs` se come TODA excepción del bucle de detalles
**Área:** `jobhunt_core/harvest/providers/native_schuljobs.py:423-433` · **CONFIRMADO**

```python
except Exception as exc:
    # The listing stub is already a usable offer.
    logger.warning("%s: detail fetch failed for %r (%s)", SOURCE_NAME, identity, type(exc).__name__)
```

No toca `error`, no acumula nada que nadie lea. Si el portal empieza a responder
403/429 a **todas** las páginas de detalle: el barrido sale `complete=True` →
`runner.py:246-255` pone `consecutive_failures = 0` **y** `consecutive_blocks = 0`
y refresca `last_complete_at`; `health.py` no ve nada (schuljobs ni publica
`items_seen`/`invalid`); y las ofertas entran al corpus **degradadas** — sin
descripción, sin `date_posted` del JSON-LD — con el semáforo en verde. Es el falso
verde que `harvest/health.py:1-11` existe para impedir. Mismo patrón en
`native_swissdevjobs.py:159`.

**Fix:** contar los detalles fallidos y publicarlos en el cursor
(`details_failed`/`details_fetched`); la vigilancia por proporción ya existe como
molde en `health.py:_invalid_items_alert`. Y propagar `blocking_status(exc)` a
`blocked_with` cuando sea 403/429/503.

## A-07 · El kill-switch de bloqueos es ciego en 4 providers (10 fuentes)
**Área:** `arbeitnow.py:336` · `native_json.py:598` · `native_schuljobs.py:386` · `native_adzuna.py:241` · **CONFIRMADO**

DT-148 A2 se aplicó a 10 de los 14 providers que preservan parciales. Los cuatro
que faltan no rellenan `blocked_with`, así que `consecutive_blocks` no sube nunca
y el kill-switch de `core0055` **no puede dispararse**: seguimos pidiendo a un
portal que nos bloquea 2-4 veces al día para siempre — la lección de
[[native-harvest-cutover]]. `native_json.py:598-604` es el peor:
`except Exception:` **sin `as exc`**, la excepción se descarta por completo, y
cubre 7 fuentes.

**`native_adzuna` es un caso aparte y peor:** destruye el estatus HTTP antes de
que exista cadena que recorrer. En `:166-169` un 403 se convierte en
`ProviderResponseError(f"HTTP {status}")` lanzado **directamente**, no desde un
`except`, así que `__context__` es `None`; en `:212-216` el 429/503 viaja como
`_RetryablePageError("http_429")` y se re-envuelve, y ninguna de las dos clases
tiene `.response`. `provider.blocking_status()` recorre `__cause__ or __context__`
buscando `.response.status_code` y no encuentra ningún eslabón. **Adzuna es
invisible al kill-switch para los tres estatus de bloqueo.**

**Fix:** capturar `as exc` y pasar `blocked_with=blocking_status(exc)` en los
cuatro; en Adzuna, encadenar (`from exc`) o conservar el `HTTPStatusError`. **Y un
test de contrato** sobre el registro que afirme que todo provider que puede
devolver `error` puede devolver `blocked_with`: es la segunda vez que este arreglo
se aplica provider a provider dejando fuera a unos cuantos.

## A-08 · La primera búsqueda guardada recorre el corpus COMPLETO contra un timeout de 60 s, y el fallo es ABSORBENTE
**Área:** `jobhunt_core/saved_search_query.py:129-133` + `search_execution.py:163-183` + `tasks/searches.py:75` · **CONFIRMADO (código) / PROBABLE (que exceda)**

Con `include_nonmatches=True` los filtros se mueven del `WHERE` a la lista de
selección y `where` se vacía: la consulta devuelve **todas** las vacantes
presentables, más un INSERT por cada una en `saved_search_observations` y un
`WITH RECURSIVE` sobre las fusiones, todo bajo `SET LOCAL statement_timeout='60s'`.
El BFF envía **siempre** `execution_contract: "swissjob-v1"`
(`backend/services/saved_searches.py:142`), que activa la ejecución, y
`CORE_SAVED_SEARCH_EXECUTION_ENABLED: 'true'` está en los cuatro servicios de
producción. Está cableado hasta la interfaz: **la primera búsqueda que un usuario
guarde** dispara esto sobre las ~94.807 vacantes activas.

Evidencia de magnitud del propio repo: `core0057_index_catalog_location.py:12-20`
midió **44,9 s** en producción para un recorrido de forma equivalente, y eso **sin**
el INSERT de 95.000 filas ni el CTE recursivo.

Si excede los 60 s el fallo **no es transitorio, es absorbente**:
`record_failed_attempt` (`search_execution.py:247-259`) sólo toca
`last_attempt_at`, nunca `last_run_at`, así que la búsqueda vuelve a estar «due»
a los 5 minutos y repite el recorrido **indefinidamente**, quemando 60 s de
`core-ops-worker` (concurrencia 1) cada vez. Y el diagnóstico está mudo:
`tasks/searches.py:90` loguea sólo `type(exc).__name__` → `saved-search <uuid>
failed: DBAPIError`, cada 5 minutos, sin la consulta ni el tiempo.

**Segundo efecto, independiente:** `saved_search_observations` es un **tombstone
permanente** con PK `(saved_search_id, vacancy_id)`, **sin purga en `retention.py`
y sin TTL**. Cada búsqueda cuesta `|corpus|` filas para siempre.

### VERIFICADO CONTRA PRODUCCIÓN — una mitad confirmada, la otra descartada

Consultada la base de producción el 2026-11-02:

| Medida | Valor real |
|---|---|
| `saved_searches` | **10** |
| `saved_search_execution` con `enabled` | **10 — todas** |
| `saved_search_observations` | **1.151.678 filas** |
| `vacancies` (corpus total) | **208.085** |

**La mitad del tombstone está CONFIRMADA y es exactamente la predicha:** el
auditor estimó «10 búsquedas ≈ 1 M de filas» y son **1,15 M**. Sin purga, sin
TTL, y crece con `|corpus| × |búsquedas|` para siempre. Esta mitad es la
accionable.

**La mitad del bucle absorbente está DESCARTADA.** Si las búsquedas estuvieran
excediendo los 60 s y volviendo a estar «due» cada 5 minutos, las diez tendrían
`last_attempt_at` de hace minutos. Los valores reales están **repartidos**: cuatro
del 27-10, dos de las 09:05-09:06 de hoy y cuatro de las 05:26-05:29 de hoy,
siendo ahora las ~22:00. **No se están reintentando: completan y respetan su
cadencia.** Además la tabla **no tiene** las columnas `last_run_at` ni
`consecutive_failures` que el razonamiento del auditor suponía (sus columnas son
`saved_search_id, contract, notify_since, enabled, run_number, last_attempt_at`),
así que la mecánica descrita del fallo absorbente no es la que hay.

**Severidad revisada: de HIGH a MEDIUM.** El recorrido completo ocurre y es
inacotado, pero hoy cabe en los 60 s. El riesgo real es de **crecimiento**: el
corpus está en 208k y el muro no se mueve.

**Fix:** acotar con cursor por `(created_at, v.id)` y continuación, reutilizando el
patrón ya probado de `dedup_scan` (`tasks/maintenance.py:103-136` + `dedup.py:668-745`).
Subir el `statement_timeout` **mueve el muro, no lo quita**. Y decidir qué
significa olvidar una observación, para poder purgar el 1,15 M.

## A-09 · `CORE_HTTP_TIMEOUT_SECONDS=120` en producción, ×2 por tres clientes, con `-w 2`
**Área:** `deploy/nas/swissjob.configured.yml:22,152` + `schools/http_client.py:55` + `saved_searches.py:78` + `documents/core_client.py:113` · **CONFIRMADO**

El defecto del código es 5 s (`backend/config.py:250`) y `CLAUDE.md` lo da por tal.
**Producción lo pone a 120.** Tres clientes derivan su presupuesto TOTAL de esa
variable con `max(float(...), 1) * 2` → **240 s por operación**, dos de ellos
envolviendo bucles de hasta 100 páginas. Y `overlay_school_results` llama a
`SchoolClient().states(pid)` en **cada** `/match/results` con la caché de 60 s fría.

El feed sí está acotado (`CORE_FEED_TOTAL_BUDGET_S=60`), lo que hace la
inconsistencia más llamativa: el camino que se midió está a 60 s y los que no se
midieron a 240 s, con **dos** workers de gunicorn.

**Impacto:** dos peticiones lentas al núcleo dejan la API entera sin atender hasta
4 minutos. Mismo modo de fallo que motivó `CORE_FEED_TOTAL_BUDGET_S`, sin cerrar
en los otros tres clientes.

**Fix:** separar el timeout POR PETICIÓN del presupuesto TOTAL (variable propia,
como se hizo con el feed) y revisar si el 120 sigue siendo necesario o era un
parche de la época del NAS cargado.

## A-10 · `send_watchlist_digest` es un no-op GARANTIZADO: cita diaria en verde que no puede notificar nada
**Área:** `backend/tasks/watchlist_tasks.py:303-328` + `services/scheduler.py:164` · **CONFIRMADO**

Dos condiciones, cada una suficiente: (1) `enabled_users(db, extra_condition=core_profile_unlinked_sql(User.id))`
selecciona **sólo usuarios SIN vínculo** en `jobhunt_profile_map`, y en producción
todos están enrolados; (2) la consulta es `select(MatchResult, Job)` con
`created_at >= now-24h`, y las dos tablas son ARCHIVO sin escritor desde el corte.

Es el mismo fallo que DT-148 A1 cerró para la alerta de profesor, sin cerrar aquí.
La cita de las 18:00 se despacha, devuelve `{"status":"ok","users_notified":0}` y
nadie se entera. `CLAUDE.md` la lista como cita viva.

**Fix:** reescribirla contra el feed del núcleo (como se hizo con `alert_tasks`) o
retirarla con su cita. Dejarla devolviendo `ok` es la peor de las tres opciones.

## A-11 · El `logout` del frontend nunca revoca la sesión: T9 es mecanismo muerto
**Área:** `frontend/src/config/api.js` (no existe `authApi.logout`) · **CONFIRMADO**

`grep -rn "auth/logout" frontend/src/` → **nada**. `authStore.logout()` sólo borra
`localStorage`; `useLogout()` añade `qc.clear()` y navega. `POST /api/v1/auth/logout`
(`backend/routers/auth.py:185-210`) y toda la maquinaria de familias revocables de
`token_store.py` existen y están probadas, pero **ningún cliente las invoca**.
Cerrar sesión no invalida nada: el refresh sigue canjeable 7 días
(`REFRESH_TOKEN_EXPIRE_DAYS`) y vive en `localStorage`, así que sobrevive a un
«cerrar sesión» en un ordenador compartido. La garantía que el docstring de
`token_store` describe **no la da el sistema desplegado**.

**Fix:** una línea en `authApi` y llamarla desde `useLogout()` sin esperar su
éxito (el endpoint es idempotente y devuelve 204 siempre).

## A-12 · La detección de robo de refresh tumba la familia por una carrera NORMAL entre pestañas
**Área:** `backend/services/token_store.py:88-104` + `frontend/src/config/api.js:24-55` + `stores/authStore.js:9-18` · **CONFIRMADO (mecanismo) / PROBABLE (frecuencia)**

*(Dos auditores llegaron a esto por caminos distintos — seguridad y frontend —
y es el mismo defecto visto desde los dos lados.)*

El canje es correcto frente a carreras (`with_for_update()` sobre la PK), pero
**esa es justamente la señal que revoca la familia**. El frontend serializa los
refresh con una promesa compartida **por pestaña** (`let refreshing = null`),
mientras el token vive en `localStorage`, compartido, y `authStore` lo lee **una
sola vez al cargarse el módulo** (`:9-10`) sin escuchar el evento `storage`.

Secuencia: pestañas A y B con el mismo `rt1`. A refresca → el servidor rota a
`rt2`. B sigue con `rt1` **en memoria**. B sufre un 401 → presenta `rt1`, ya
canjeado → `revocar_familia` → **las dos pestañas fuera**.

**Impacto:** logout sorpresa con «sesión revocada» en uso normal con varias
pestañas o al despertar el portátil. Y **envenena la señal**: el log de robo deja
de significar robo.

**Fix (el del BCP de OAuth 2.1):** ventana de gracia en el canje — si el refresh
presentado ya tiene `replaced_by`, su `revoked_at` es de hace menos de 10-30 s y
el sucesor sigue vivo, devolver el sucesor en vez de revocar. Complementario:
listener de `storage` en el frontend y releer `localStorage` antes de canjear.

## A-13 · Bomba de descompresión en la subida de CV: sólo se acota el tamaño COMPRIMIDO
**Área:** `backend/routers/profile.py:177-216` + `services/cv_parser.py:56-62` · **CONFIRMADO**

El único límite es sobre los bytes recibidos (`CV_MAX_SIZE_MB=10`), y después
`Document(io.BytesIO(file_bytes))`. `python-docx` abre con `zipfile` y hace
`read("word/document.xml")` **completo en memoria** antes de pasarlo a lxml. En
ninguna capa hay cota del tamaño **descomprimido**. Un ZIP válido de 10 MB con
ratio ~1000:1 expande a ~10 GB.

**Impacto:** el contenedor de producción tiene `mem_limit: 1536m`. Una sola
petición autenticada mata por OOM al worker de gunicorn, que pierde además la
caché en proceso del recorrido del feed. Con 2 workers, dos peticiones dejan el
BFF sin servir — y el bucket de rate limiting es único (A-15), así que repetirlo
es trivial.

**Fix:** antes de `Document()`, abrir el ZIP con `zipfile.ZipFile` y rechazar si
`sum(zi.file_size)` supera un tope o si el ratio de cualquier entrada es
desproporcionado. Cota de páginas para el camino PDF.

## A-14 · `swissjob-erasure-cdc`: contenedor de producción SIN definición recuperable
**Área:** producción (NAS), proyecto de compose `swissjob-erasure` · **CONFIRMADO ejecutando**

Corre desde el 2026-09-13: `python -m services.profile_erasure --watch`, imagen
`swissjob-backend:d2a38e9`, `restart: unless-stopped`, redes
`swissjob-r5_core-net` + `swissjob_swissjob-net`, sin montajes. Es un **tercer**
proyecto de compose, distinto de `swissjob` y `swissjob-r5`.

Su compose vivía en
`/share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88/e13-erasure/replica.yml`
(`docs/ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md:611`). **Comprobado por SSH: ese
directorio ya no existe** — es un nombre de `mktemp -d` y se limpió. **No está
versionado en `deploy/nas/`**, que sólo trae `core.configured.yml` y
`swissjob.configured.yml`, así que A19-04 no lo cubre y `scripts/deploy_nas.sh`
(`core|bff`) no puede tocarlo.

Sí está en la lista del supervisor (`scripts/nas/supervisor_contenedores.sh:47`):
su muerte se **notaría**, pero no se podría **arreglar**. Implementa parte del
camino de borrado GDPR (`docs/BORRADO_COORDINADO_E13.md`), que es obligación
legal.

**Impacto:** un `docker rm`, una acción de Container Station, un prune de
imágenes o una pérdida del estado del daemon dejan el reconciliador de borrado
GDPR **irrecuperable** sin reescribir su compose desde un `inspect`.

**Fix:** versionar `deploy/nas/erasure.configured.yml` desde el `inspect` del
contenedor vivo (ya capturado), añadirlo a `check_no_secrets.py` y decidir si
entra en `deploy_nas.sh` como tercer objetivo o se fusiona en el proyecto
`swissjob`.

## A-15 · Los seis sliders de pesos son inertes, y además bloquean «Save profile»
**Área:** `frontend/src/pages/ProfilePage.jsx:36-57,370-415,443` · **CONFIRMADO**

`grep -rn "score_weights" jobhunt_core/ --include='*.py'` → **cero coincidencias**:
el motor real (`matching.py`, RRF + cross-encoder por políticas) no conoce estos
pesos. El BFF sólo los persiste y los devuelve como eco (`weights_used`,
`routers/match.py:236-240`). El comentario del propio fichero delata la antigüedad
—*«los SEIS pesos del motor (`job_matcher.DEFAULT_WEIGHTS`)»*— y `job_matcher.py`
se retiró el 2026-10-13. `DEFAULT_WEIGHTS` sobrevive como vestigio en
`backend/services/profile_defaults.py`.

La pantalla promete *«Fine-tune how the AI scores matches»* sobre un control
inerte. Y peor, `:443` → `disabled={!weightsValid}` **bloquea el botón «Save
profile» completo** según la suma de esos pesos muertos: un perfil cuyo
`score_weights` guardado no sume ≈1,00 no puede guardar **ningún** cambio de
título, skills o ubicaciones.

**Fix:** decidir si el núcleo va a honrar pesos por usuario. Si no, retirar la
sección y el gate. Mientras se decide, el mínimo es desacoplar `disabled` de
`weightsValid`.

---

# MEDIUM

## A-16 · El rate limiting es efectivamente GLOBAL: 5 intentos de login por minuto para TODO el despliegue
**Área:** `backend/core/rate_limit.py:11-27` + `backend/config.py:144` + `frontend/nginx.conf:39` · **CONFIRMADO**

`get_limiter_key` sólo usa `X-Forwarded-For` si `RATE_LIMIT_TRUST_PROXY`, cuyo
comentario pide «activar SOLO si el proxy sobreescribe/sanea la cabecera». **nginx
ya la sobrescribe** (`proxy_set_header X-Forwarded-For $remote_addr`, no
`$proxy_add_...`), pero el compose de producción no declara la variable, así que
vale `False`. Y en producción el backend no publica puerto, así que
`request.client.host` es **siempre** la IP del contenedor nginx.

**Impacto:** un único bucket para todos. El `@limiter.limit("5/minute")` de
`/auth/login` significa **5 intentos por minuto para todo el despliegue**:
cualquiera (incluido un usuario que se equivoque dos veces) deja a los demás sin
poder entrar. DoS del login con 5 peticiones/min. Y amplifica A-13.

**Fix:** `RATE_LIMIT_TRUST_PROXY=true` en el `.env.prod` — la precondición ya la
cumple `nginx.conf:39` — más un test que falle si ese `proxy_set_header` vuelve a
`$proxy_add_x_forwarded_for`.

## A-17 · El rate limiter falla CERRADO: un corte de Redis devuelve 500 en toda la API
**Área:** `backend/core/rate_limit.py:53-68` (línea 59) · **CONFIRMADO**

`if not limiter._limiter.hit(self._item, *identidad):` llama al API privado de
`limits` **sin try/except**, saltándose el manejo de errores de slowapi
(`Limiter` se construye sin `swallow_errors`). Un `ConnectionError` de
`RedisStorage.incr` sube por `BaseHTTPMiddleware` y se convierte en 500 para
**cada** petición, incluidas `/health` y `/api/v1/health`. Un corte de Redis pasa
de «se degradan SSE, bus de caché y scheduler» a «la API entera caída», y el
healthcheck del compose marca el contenedor unhealthy.

**Fix:** envolver el `hit` y ante fallo del almacén dejar pasar con un WARNING
(fail-open explícito y acotado), o `swallow_errors=True` + el camino de slowapi.

## A-18 · El parser del CV se elige con el `Content-Type` del cliente, sin comprobar la firma
**Área:** `backend/routers/profile.py:177-180` + `services/cv_parser.py:64-74` · **CONFIRMADO (impacto RCE: PROBABLE)**

`file.content_type` es la cabecera de la parte multipart — valor arbitrario del
atacante — y es la única validación de tipo. `CVParser.extract_text` enruta
exclusivamente por ella: `application/pdf` → `fitz.open(stream=file_bytes, filetype="pdf")`.
Sin magic bytes, sin aislamiento, sin cota de tiempo ni de páginas. Bytes
arbitrarios de un usuario autenticado llegan a MuPDF (biblioteca C) **dentro del
proceso del BFF**, que tiene la sesión de BD y la `SECRET_KEY` en memoria.

**Agravante medido:** el parseo va por `run_in_threadpool` y **comparte el pool de
anyio (40 hilos) con bcrypt** (`core/security.py:59,64`), que es el camino del
login: saturar el pool con PDFs pesados degrada la autenticación.

**Fix:** validar la firma y exigir que concuerde con el `content_type` declarado;
acotar páginas/tiempo; a medio plazo parsear en subproceso con `RLIMIT_AS`/`RLIMIT_CPU`
(cierra también A-13).

*No es problema (comprobado): XXE en DOCX está mitigado — `python-docx` 1.2.0
construye su parser con `resolve_entities=False`.*

## A-19 · El overlay escolar se cachea 60 s EN PROCESO y su invalidación no viaja al otro worker
**Área:** `backend/services/schools/presentation.py:13-29,68-84` + `schools/watchlist_state.py:183-185` · **CONFIRMADO**

`_overlay_cache[pid]` guarda `application_status` y `draft_content` con TTL 60 s.
`watchlist_state._write` invalida con `clear_school_presentation_cache(pid)` **sólo
en su propio proceso**: a diferencia de `CoreFeedback._write`
(`feedback.py:164-170`), **no publica en `cache_bus`**, y el oyente
(`cache_bus.py:58-70`) sólo llama a `clear_feed_cache`.

**Impacto:** con gunicorn `-w 2`, marcar «Applied» o guardar un borrador en el
worker A puede servirse como «detected» / sin borrador desde el worker B hasta
60 s. Es la regresión M3/T12 que motivó el cache bus, repartida entre procesos en
la otra caché. La cota «el overlay local se relee siempre» **no se cumple** para
los items de colegio.

**Fix:** publicar en el bus desde `watchlist_state._write` y que el oyente
invalide también la presentación escolar.

## A-20 · Un decomiso de modelo de Groq (404) se clasifica como transitorio → reintento en vano perpetuo
**Área:** `backend/services/translation_service.py:254-266,215-229` · **CONFIRMADO**

El SDK lanza `NotFoundError` (404) cuando el modelo ya no existe. El filtro sólo
propaga 401/403; el 404 cae en el `logger.warning` y `return {}`. En cadena:
(a) los 25 títulos van a `failed_titles` y se **reintentan** con el prompt
simplificado → 2 llamadas 404 por lote; (b) `translations.get(title, title)` deja
`result[title] = title`, y como `translated == title` **no se cachea nada**;
(c) el calentador (`warm.py:100`) vuelve a seleccionar los mismos ≤100 pendientes
cada 60 s, por usuario, para siempre.

El síntoma visible es «títulos sin traducir», **el mismo que una `GROQ_API_KEY`
caducada**, con un tratamiento distinto y mucho peor. Y el proyecto ya tiene tres
decomisos registrados: esto es el cuarto esperándonos.

**Fix:** tratar 404/`model_not_found` como error de CONFIGURACIÓN: propagar,
abortar el lote y loguear ERROR nombrando `GROQ_RERANK_MODEL` y el catálogo vivo.

## A-21 · `MatchResultService` sigue siendo un escritor LATENTE del archivo
**Área:** `backend/services/match_result_service.py:130-159` + `services/matching/feedback.py:39-44` · **CONFIRMADO (inactivo hoy)**

`pg_insert(MatchResult)` + `on_conflict_do_update` es un escritor real sobre una
tabla que `CLAUDE.md` y la memoria declaran ARCHIVO de sólo lectura. Hoy está
apagado (`CORE_FEEDBACK_ENABLED: 'true'`), pero `feedback_writer()` lo devuelve en
cuanto el flag se ponga a `false` — y **el defecto del código es `False`**
(`config.py:255`), igual que el `.env.prod.example` generado. Un despliegue que
olvide la variable **reactiva escrituras sobre el archivo** y devuelve la autoridad
del feedback a una tabla desincronizada (los `score_final=0.0` del upsert mínimo
quedarían bajo cualquier umbral).

**Fix:** decidir si la rama local sigue siendo camino de rollback. Si lo es,
documentarlo donde se afirma «archivo de sólo lectura» y hacer que el arranque
grite con `CORE_FEEDBACK_ENABLED=false`; si no, retirarla.

## A-22 · `check_watchlist_health` convierte cualquier fallo en SUCCESS, y una fuente tumba las ocho
**Área:** `backend/tasks/watchlist_tasks.py:84-118` · **CONFIRMADO**

`except Exception` → `return {"status": "error", ...}` sin `bind=True` y sin
reintento: Celery marca SUCCEEDED y nadie consume el dict. Además
`_core_watchlist_health` usa `asyncio.gather` **sin `return_exceptions=True`**,
así que **una** fuente que falle aborta la comprobación de las ocho. El monitor
puede llevar meses caído sin más rastro que una línea de log.

**Fix:** `return_exceptions=True` (comprobar lo comprobable y reportar lo que no)
y `bind=True` + `self.retry`, o una notificación de «el propio monitor no pudo
ejecutarse».

## A-23 · `_WATCHLIST_SOURCES` no incluye `swiss_schools_vis`, que sí existe en el núcleo
**Área:** `backend/tasks/watchlist_tasks.py:31-40` vs `native_swiss_schools.py:499,518` · **CONFIRMADO**

El núcleo registra 9 fuentes `swiss_schools_*` (zis, isb, ecolint, hautlac, iscs,
**vis**, nae, inspired, isp); el BFF vigila 8. Punto ciego permanente: si VIS se
apaga por el kill-switch o deja de completar cosechas, el monitor no lo dirá.

**Fix:** derivar la lista del núcleo (`GET /harvest/sources`) en vez de duplicarla,
o al menos un test que compare ambas. Es duplicación con deriva demostrada, del
tipo que DT-148 C persigue.

## A-24 · 15 migraciones crean índices sin `CONCURRENTLY`, y `core0057` confía en un `lock_timeout` que no hace lo que dice
**Área:** `core0057_index_catalog_location.py:28-30,50-54` (y el molde en `core0045`, `core0053`, `core0044`, `core0012`, `core0007`, `core0002:382`, `core0047`, `core0043`) · **CONFIRMADO**

El docstring afirma: *«incluido el `lock_timeout`: construir un GIN sobre una tabla
que la cosecha está escribiendo toma un lock, y es mejor que la migración falle
rápido»*. **`lock_timeout` acota cuánto se ESPERA por adquirir el lock, no cuánto
se RETIENE.** Un `CREATE INDEX` no concurrente toma `SHARE` sobre `offer_revisions`
y lo mantiene durante toda la construcción del GIN trigram — sobre las 227.053
revisiones que el propio docstring documenta, en 2 CPUs con loadavg ~11 —
bloqueando **todo INSERT** del sink. Y `deploy_nas.sh` migra **antes** de recrear:
los servicios viejos siguen cosechando mientras el lock está tomado.

Riesgo acotado (ya se aplicó una vez sin incidente), pero **el razonamiento es
incorrecto y está escrito como garantía**, así que se va a copiar: `core0057` dice
literalmente «mismo molde que core0045».

**Fix:** `CREATE INDEX CONCURRENTLY` (exige salir de la transacción de Alembic y
comprobar `pg_index.indisvalid` después) y **corregir el comentario de
`core0045`/`core0057`**, que es el que propaga el error.

## A-25 · Ninguna cabecera de seguridad, con el JWT en `localStorage`
**Área:** `frontend/nginx.conf` · **CONFIRMADO** *(dos auditores, mismo hallazgo)*

nginx sólo añade `Cache-Control`. No hay `X-Content-Type-Options: nosniff`, ni
`frame-ancestors`/`X-Frame-Options`, ni `Referrer-Policy`, ni CSP. El `access_token`
y el `refresh_token` viven en `localStorage`, alcanzables desde cualquier JS del
origen. Relevante porque el frontend inyecta HTML con `dangerouslySetInnerHTML` en
dos sitios (`JobDetailPage.jsx:166`, `DocumentGenerator.jsx:57`):
`utils/sanitizeHtml.js` está bien hecho, **pero es la única capa**. HSTS no aplica
(HTTP sobre Tailscale a propósito).

**Fix:** cuatro `add_header` (`nosniff`, `frame-ancestors 'none'`,
`Referrer-Policy`, CSP con `script-src 'self'` contemplando
`fonts.googleapis.com`/`fonts.gstatic.com`), verificando la CSP contra el bundle
de Vite antes de desplegar.

## A-26 · Cero timeouts y cero cancelación en el frontend
**Área:** `frontend/src/config/api.js` · **CONFIRMADO**

`grep -rn "AbortController|signal|timeout" frontend/src/` → **nada**. Ningún
`queryFn` recibe el `{ signal }` que react-query le pasa. Una petición colgada deja
el spinner girando para siempre; al desmontar una página la petición sigue viva; y
`retry: 1` global **duplica** cualquier petición fallida (en modo legacy, la de
`limit=3000`).

**Fix:** propagar `signal` desde los `queryFn` y un `AbortSignal.timeout(N)`
compuesto en `request`. Mecánico, toca `config/api.js` + firmas de hooks.

## A-27 · Un error al cargar el perfil en Onboarding sobrescribe el perfil guardado con valores vacíos
**Área:** `frontend/src/pages/OnboardingPage.jsx:84,99-107,129-138` · **CONFIRMADO**

`useProfile` tiene `retry:false` y el `isError` no se consume. Si la carga falla,
`isLoading` pasa a `false`, `profile` queda `undefined`, no se precarga nada y la
página se pinta **normal**. El botón «Find matches» hace
`updateProfile.mutateAsync({skills: [], locations: [], salary_min: null, ...})`:
**PUT destructivo** sobre el perfil real. Agravante: los dos `mutateAsync` van sin
`try/catch` y el paso 3 no pinta `updateProfile.isError`.

**Fix:** rama de error explícita antes de pintar los pasos (el patrón que
`ProfilePage.jsx:117-141` ya documenta como «el ORDEN importa»), `try/catch`, y
considerar PATCH en vez de PUT.

## A-28 · Un logout INVOLUNTARIO no vacía la caché de react-query
**Área:** `frontend/src/config/api.js:64` vs `hooks/useAuth.js:92-107` · **CONFIRMADO**

`useLogout` hace `qc.clear()` y promete *«nada de la sesión que acaba sobrevive»*.
El logout involuntario (cuando el refresh devuelve 401/403) va por otro camino:
`if (err.sessionEnded) useAuthStore.getState().logout();` — **sin `qc.clear()`**. Y
las claves `["match-results"]`, `["profile"]`, `["saved-jobs"]`, `["notifications"]`,
`["saved-searches"]`, `["match-facets"]` **no llevan `userId`** (sólo las de
`useDocuments`). En una webapp multiusuario, la cuenta siguiente que entre en el
mismo navegador ve matches, perfil y guardados de la anterior hasta que expire el
`staleTime` de 2 min.

**Fix:** que `api.js` emita el cierre por el mismo camino que `useLogout`, y meter
`userId` en las queryKeys de datos de usuario.

## A-29 · El borrado GDPR de producción invalida una caché que ya no existe
**Área:** `backend/services/profile_erasure.py:22-30` vs la imagen `d2a38e9` · **CONFIRMADO**

`swissjob-erasure-cdc` está clavado en `swissjob-backend:d2a38e9` (2026-09-13)
mientras el BFF corre `point5-14438b7`. Deriva medida: `profile_erasure.py` 176
líneas, `profiles/core_client.py` 288, `config.py` 323. **Caracterizada leyendo el
diff: casi toda es reformateo de `ruff`** (`02a906a`), pero hay **un cambio
funcional**:

- Lo que CORRE en producción: `clear_profile_cache(profile_id)`.
- Lo que dice el árbol: `clear_feed_cache` + `clear_school_presentation_cache` +
  `publicar_sin_esperar`.

`clear_profile_cache` es justamente la función que **DT-148 B3 retiró** por tener
un único consumidor y existir «para invalidar la caché que se iba».

**Impacto:** al confirmar un borrado, producción invalida la caché VIEJA y **no**
la del feed ni la de presentación escolar del perfil borrado. El payload cacheado
de un usuario borrado puede sobrevivir en Redis hasta su TTL. Es GDPR, acotado hoy
sólo porque ha habido UNA petición de borrado en toda la vida del sistema.

**Fix:** reconstruir el contenedor con la imagen vigente — que **requiere A-14
antes**, porque no hay compose con el que recrearlo. Los dos van juntos.

## A-30 · El parámetro que acota una escritura al perfil es OPCIONAL en `/v1`: el fallo es ABIERTO
**Área:** `jobhunt_core/api/v1_applications.py:383,407-409,438,453-455` · **CONFIRMADO (no explotable hoy)**

El *tenant* del core es el **consumer**, no el usuario final: todos los usuarios de
SwissJob comparten la credencial `swissjob-shadow`, así que
`profiles.consumer_id = :cid` no separa a Alice de Bob. El acotado por usuario
existe (`profile: uuid.UUID | None = Query(None)`) pero **su defecto es `None`**, y
con `None` la escritura procede sobre el perfil que resulte dueño del `item_id`.

No explotable hoy: tras DT-148 el BFF ya no llama a `/v1/applications`. Pero es un
**IDOR latente**: el día que un endpoint reenvíe un `item_id` del cliente sin pasar
`profile`, el aislamiento entre usuarios desaparece **sin que nada falle**. El
contraste con el resto del contrato es claro: feedback y búsquedas guardadas sí
fuerzan el perfil vía `run_idempotent(..., profile_id=...)`, que ejecuta
`SELECT ... FOR UPDATE` con `consumer_id` antes del handler.

**Fix:** hacer `profile` obligatorio (rompe compatibilidad con un llamador que ya
no existe) o rechazar con 400 si falta y el consumer tiene más de un perfil.

## A-31 · Inventario completo del patrón A18-03 — «un fallo de red se lee como no hay nada»
**Área:** 8 componentes + 2 mutaciones · **CONFIRMADO**

El `WatchlistPage` ya conocido (DT-148 A18-03) es **uno de ocho**. Esto es lo que
faltaba para cerrarlo bien:

| Componente | Query | Qué ve el usuario ante error |
|---|---|---|
| `WatchlistPage.jsx:70` | `["match-results",…]` | «Aún no hay vacantes en la watchlist» *(conocido)* |
| `WatchlistPage.jsx:59` | `useMatchFacets` | Degrada en silencio al filtrado en cliente — **reintroduce el bug A12** (colegios por debajo del puesto 500 invisibles) |
| `WatchlistPage.jsx:314` | `["draft",hash]` | Drawer con un `<pre>` **vacío** |
| `FiltersPage.jsx:255` | `useSuggestions` | «No suggestions yet» |
| `FiltersPage.jsx:258` | `useFilters` | «No active filters» — el usuario recrea filtros que ya existen |
| `NotificationBell.jsx:14` | `useNotificationHistory` | «No notifications yet» |
| `OnboardingPage.jsx:84` | `useProfile` | Formulario vacío → **A-27** |
| `MatchPage.jsx:42` | `useProfile` | «Upload your CV to unlock AI matching» a quien ya tiene CV |

Mutaciones sin superficie de error: `SavedSearchesPage.jsx:248-258` (pausar o
borrar una alerta falla sin decir nada) y `NotificationBell.jsx:98`.

Con `isError` bien consumido: `SearchPage`, `SavedJobsPage`, `ProfilePage`,
`JobDetailPage`, `DocumentGenerator` (el mejor del repo, con `role="alert"`).

**Fix:** un componente `<QueryState query={…} empty={…}>` que distinga los tres
estados, en vez de parchear ocho sitios con el mismo `if`.

## A-32 · `native_json.py` es un SEGUNDO fichero con mantenibilidad C, y nadie lo ha inventariado
**Área:** `jobhunt_core/harvest/providers/native_json.py` · **CONFIRMADO con radon 6.0.1**

`CLAUDE.md` y `DT148_RELEVO.md` §4 afirman los dos que `matching.py` es **«el único
fichero del repo con mantenibilidad C»**. Medido:

```
jobhunt_core/matching.py                       - C (0.59)
jobhunt_core/harvest/providers/native_json.py  - C (8.07)   ← 642 LOC
```

Y su grado **no** es artefacto de narrativa, al contrario que el 0,59 de
`matching.py` (que arrastra 509 líneas de docstrings): `native_json.py` tiene
**7 funciones en grado C** y sólo 11 líneas de comentario. Hace dos cosas: es el
provider **genérico** de los N endpoints JSON *y* el hogar de las **excepciones por
fuente** (`_ictjobs_*`, `_generic_content`).

**Fix proporcionado:** no es un D1. Basta extraer las ramas por fuente a un módulo
hermano. Medir con `radon mi` antes y después.

## A-33 · `scripts/check_source_ownership.py:242 validate` tiene CC **F (80)** y es el primer guard del CI
**Área:** `scripts/check_source_ownership.py:242` · **CONFIRMADO con radon 6.0.1**

Es **la función más compleja del repositorio**, con diferencia (la siguiente es
CC 32), en un fichero de 464 LOC, y corre como paso 1 de `.github/workflows/ci.yml:21`.
Un guard de CI con CC 80 es un guard que nadie puede auditar: cada rama se añade
por acumulación y no hay forma de saber si una condición queda muerta. El relevo §7
ya documenta un incidente con este mismo guard («busca el literal en TODO el
fichero, comentarios incluidos») — ese comportamiento sorpresa es exactamente lo
que produce una función de 80 ramas. **No está amparado por ninguna cota**: la
A19-13 se quedó sin objeto al retirarse las herramientas de la Fase E.

**Fix:** partir `validate` por **clase de invariante** (una función por familia de
error, cada una devolviendo su lista), con `validate` como concatenador. Movimiento
puro, sin tocar ni un mensaje de error: son los que el CI imprime.

## A-34 · `materialize_all` encola sin `expires` ni clave idempotente
**Área:** `jobhunt_core/tasks/materialize.py:186-193` · **CONFIRMADO (latente)**

Cada fragmento puede durar `MATERIALIZE_FRAGMENT_SECONDS = 1200` s y la cola
`core.matching` la atiende **un** worker de concurrencia 1 que además lleva
`run_profile` y `recover`. Si la tanda de las 06:15 no drena en 24 h, la del día
siguiente encola el mismo conjunto otra vez: **la cola compone, no converge**.

El contraste está en el mismo repo: `tasks/harvest.py:299-306` encola con `run_key`
estable **y** `expires=21600` precisamente para esto. Hoy es inocuo (no hay
políticas de CE activas); se vuelve real el día de la promoción del CE.

**Fix:** `expires` igual a la cadencia y clave idempotente por
`(perfil, política, día)`, del molde `native:{cadence}h:{window}:{scope}`.

## A-35 · «Saved jobs» corta en 100 sin paginación y lo dice sin decirlo
**Área:** `frontend/src/hooks/useMatch.js:72` + `pages/SavedJobsPage.jsx:24,80-84` · **CONFIRMADO**

`useSavedJobs(limit = 100, offset = 0)` y la página la llama sin argumentos, sin
ningún control de paginación. La cabecera pinta el `total` del servidor: con 140
guardados el usuario lee «140 matches you marked as relevant» y la suma de las
pestañas da 100. Las 40 restantes son inalcanzables.

**Fix:** un paginador (los botones ya existen en `MatchPage.jsx:390-409`), facetas,
o al menos un aviso cuando `total > data.length`.

## A-36 · `useActiveFilterChips` lee seis campos que el store ya no modela
**Área:** `frontend/src/pages/SearchPage.jsx:30-69` · **CONFIRMADO**

Los chips comprueban `canton`, `language`, `seniority`, `contractType`,
`salaryMin`, `salaryMax`. Nada de eso existe: `stores/searchStore.js:12-27` modela
`q, source, location, remoteOnly` — y `hooks/useJobSearch.test.js:12-24` **lo fija
con un test** que lista los campos muertos. El propio repo prueba que estos chips
no pueden activarse nunca. 40 líneas muertas de DT-148 A18-01.

**Y el efecto visible no es sólo código muerto: `location` no tiene chip.** Es el
filtro que A18-01 puso como sustituto de los cuatro desplegables retirados, y el
único que el núcleo filtra sobre el 100 % de las canónicas. Quien escriba «Zürich»
no ve ningún chip, el badge de «Filters» no lo cuenta, y la única forma de quitarlo
es «Clear all».

## A-37 · Accesibilidad: en login y registro las etiquetas no están asociadas a sus campos
**Área:** `frontend/src/pages/LoginPage.jsx:47-74`, `RegisterPage.jsx:51-78` · **CONFIRMADO**

Los cuatro `<label>` son **hermanos** del `<input>`, sin `htmlFor`, sin envolverlo,
y los inputs no tienen `id` ni `aria-label`. Un lector de pantalla anuncia «edición,
en blanco» sin nombre accesible, y el clic en la etiqueta no enfoca el campo. Es el
flujo de entrada de la aplicación. **Lo llamativo es que el repo ya tiene la pieza
correcta**: `components/ui/Input.jsx:27-43` genera `useId()` y pone
`htmlFor`/`id`/`aria-describedby`; estas dos páginas son las únicas que no la usan.
Además los recuadros de error no llevan `role="alert"`, así que un login fallido no
se anuncia.

## A-38 · `FilterPanel` es un `aria-modal` sin trampa de foco
**Área:** `frontend/src/components/FilterPanel.jsx:38-50` · **CONFIRMADO**

Declara `role="dialog" aria-modal="true"`, bloquea el scroll y cierra con Escape
(bien), pero el foco no se mueve al abrirse, no hay trampa de foco (Tab se escapa
al contenido de detrás, que `aria-modal` oculta a los lectores pero no al teclado)
y al cerrarse no vuelve al disparador. Un usuario de teclado queda tabulando por
una página que para él ya no existe.

## A-39 · Contraste por debajo de AA en dos pares de tokens
**Área:** `frontend/src/index.css:33,45-46` · **CONFIRMADO (calculado)**

- `--color-text-quaternary: #A1A1AA` sobre blanco → **2,53:1** (AA pide 4,5). Se
  usa como `placeholder:` en `SearchPage.jsx:146`, `ui/Input.jsx:65` y
  `TagList.jsx:76` — el placeholder de la caja de búsqueda principal.
- `--color-text-tertiary: #71717A` sobre `--color-surface-tertiary: #F4F4F5` →
  **4,36:1**, justo por debajo, y a `text-[10px]`: los contadores de
  `CategoryTabs.jsx:50-56`.

`text-tertiary` sobre blanco sí pasa (4,83:1), y los usos de `quaternary` en
iconos son decorativos con `aria-hidden`.

---

# LOW

| # | Hallazgo | Área |
|---|---|---|
| A-40 | `scrub_capture` toma `LOCK TABLE shadow_change_log IN SHARE ROW EXCLUSIVE` en el camino crítico de borrado GDPR, sobre una tabla **sin escritor** desde `3ed6462`. El comentario describe un escritor que ya no existe. Dos borrados concurrentes se serializan en una tabla congelada. **No tocar aún**: `core0056` la conservó a propósito; merece una nota que lo ate a la cota de ~enero 2027 | `jobhunt_core/erasure.py:183-185`, `erasure_restore.py:166-174` |
| A-41 | `dispatch_native` aplica `expires=21600` (6 h) también a las cadencias de 12 y 24 h: un scope no atendido en 6 h se **descarta en silencio** y no vuelve hasta la ventana siguiente. Sin log — Celery lo descarta en el worker. `expires = cadence_hours * 3600` | `jobhunt_core/tasks/harvest.py:300-306` |
| A-42 | `idempotency.purge_expired` sin `LIMIT`, cada hora, sobre una tabla en el camino caliente de toda escritura del `/v1`. Contrasta con `retention.py:51-54`, que razona explícitamente por qué toda purga va acotada | `jobhunt_core/idempotency.py:6-12` |
| A-43 | La rama de refresh sin `jti` **no implementa** la garantía documentada «se acepta UNA vez»: no se marca nada como consumido, se puede presentar cuantas veces se quiera hasta su `exp` acuñando familia nueva cada vez, y ni `logout` ni `revocar_familia` lo alcanzan. El test que la cubre comprueba que se acepta, **no** que la segunda vez se rechace. Exposición hoy nula (la ventana cerró el 02-10-2026) → **retirar la rama** | `backend/services/token_store.py:80-82` |
| A-44 | `detector_version` es columna de sólo escritura: el docstring promete que subirla marca lo anterior como re-derivable, pero `pending_titles` filtra sólo `language IS NULL`. Garantía escrita que el código no da | `backend/services/language_store.py:22-24,101-109` |
| A-45 | `GROQ_CONCURRENCY` no tiene **un solo lector** (barrido de las 60 claves de `Settings`). Sobrevivió a DT-148 B3, que retiró ocho mandos por este motivo, y como `.env.prod.example` se genera desde `Settings`, se le ofrece al operador | `backend/config.py:172` |
| A-46 | Fuga del cliente Redis en la alerta de profesor cuando el catálogo del núcleo falla: se cierra en éxito y en fallo de SMTP, no si `recent()` lanza. Una conexión por corrida fallida cada 6 h | `backend/tasks/alert_tasks.py:70,136` |
| A-47 | Inyección de ICS: `_ics_escape` no escapa `\r`. `SUMMARY`/`DESCRIPTION` incluyen `job.title` y el nombre del colegio, que vienen del corpus cosechado, y la normalización del núcleo sólo hace `.strip()`. Una oferta con `\r\n` en el título puede inyectar propiedades en el calendario importado | `backend/routers/watchlist.py:272-278` |
| A-48 | `INTERVALS[str(search.notify_frequency)]` sin cobertura de clave desconocida: el ENUM protege hoy, pero `run_one(force=True)` llega al `KeyError`, que se convierte en un retry de 120 s con el diagnóstico reducido a `KeyError` | `jobhunt_core/search_execution.py:153` |
| A-49 | `CORE_INBOX_TOKEN` y `CORE_DELIVERY_HTTP_TOKEN` son secretos simétricos estáticos **sin TTL ni rotación con solape** — lo contrario de las credenciales de consumer (90 d + `rotate_credential.py` + alerta de solape). Rotarlos hoy exige ventana de corte | `backend/config.py:244`, `jobhunt_core/config.py:77` |
| A-50 | `/v1/docs` y `/v1/openapi.json` sin credencial: publican el contrato completo a cualquiera que alcance `core-api`. Mitigado por topología (no publica puerto), pero un contenedor vecino de esas redes lo lee | `jobhunt_core/api/main.py:41-42` |
| A-51 | El catálogo es anónimo a propósito, pero cada petición consume la credencial del core; con `limit≤100` y el bucket único de A-16, un cliente sin cuenta extrae ~24.000 filas/min y consume él solo el presupuesto de esa ruta | `backend/routers/jobs.py:46-98` |
| A-52 | Refetch de 3.000 filas disparado por un pulgar arriba: `useSubmitFeedback` invalida el **prefijo** `["match-results"]`. En modo legacy recarga el `limit=3000` por cada feedback. Lo correcto es actualización optimista de la fila tocada | `frontend/src/hooks/useMatch.js:46-48,65-67` |
| A-53 | `setAuth` puede persistir la cadena `"undefined"` como token, que al rehidratar es *truthy*: `ProtectedRoute` daría la sesión por buena y se mandaría `Bearer undefined` indefinidamente. PROBABLE (exige un 200 incompleto) | `frontend/src/stores/authStore.js:14-18` |
| A-54 | UI mezcla inglés y español sin capa de i18n (`frontend/src/i18n/` sólo tiene `.gitkeep`). `WatchlistPage` está **íntegramente en español**, incluido un `alert()`; el resto es inglés | `frontend/src/pages/WatchlistPage.jsx` |
| A-55 | `role="tablist"` sin patrón de tabs: no hay `tabpanel`, ni `aria-controls`, ni navegación por flechas. Con 14 categorías son 14 paradas de Tab. O se completa el patrón, o se usan botones con `aria-pressed` (que es lo que de verdad son) | `frontend/src/components/CategoryTabs.jsx:15-28` |
| A-56 | Dos directorios fantasma con sólo `__pycache__` rancio: `backend/providers/` y `jobhunt_core/shadow/`. Hacen que cualquier `ls` del árbol mienta sobre la arquitectura — **es la causa raíz de que la memoria del proyecto siguiera describiendo providers del BFF**. Borrarlos es gratis | árbol |
| A-57 | `worker-ai` está definido en el compose y **corriendo**, pero no aparece en la «Arquitectura en una página» de `CLAUDE.md` | `docker-compose.yml` |
| A-58 | Entorno local medio caído: `postgres`, `backend`, `redis` y `core-api` exited hace 11 días, `frontend` exited(1) hace 3 meses, mientras `worker`, `worker-ai` y `core-worker` siguen **corriendo en bucle de reinicio** contra una base que no está. CPU y ruido de log a cambio de nada | local |
| A-59 | `bump_corpus_generation` serializa toda la cosecha en una fila única (`UPDATE corpus_generation ... WHERE id=1`) cuyo lock se retiene hasta el commit del run, anulando el paralelismo por fuente del `pg_advisory_xact_lock`. **No es un bug hoy** (harvest-worker con `--concurrency=1`); es **la razón por la que subir esa concurrencia no dará escalado**, y conviene que esté escrito antes de que alguien lo intente. No está en COTAS | `core0022`, `matching.py:1390,2167` |
| A-60 | Timeout HTTP de la cosecha: **13 literales, 4 valores distintos (20/25/30/60), ningún campo en `Settings`**. No hay forma de acortarlos en el NAS sin reconstruir la imagen; el `60` de jobroom es 3× el `20` de publicjobs sin comentario que lo justifique. Cabe en C1, **pero C1 no menciona este reparto** y su default de 30 cambiaría once sitios en silencio | 13 providers |

## A-66 · `check_compose_exposure.py` promete detectar defaults de contraseña y sólo busca UN literal quemado
**Área:** `scripts/check_compose_exposure.py:30,74-89` · **CONFIRMADO**

El docstring, punto 2: *«Un `${VAR:-contraseña}` devuelve una clave conocida»*. La
implementación es `CLAVES_QUEMADAS = ("swissjob_dev_2024",)` y un `in texto`.
`docker-compose.yml` publica **dos credenciales por defecto en nueve sitios**,
ninguna detectada:

- `CORE_REDIS_PASSWORD:-core_redis_dev` → líneas 144, 217, 218, 262, 263
- `CORE_DB_PASSWORD:-jobhunt_core_dev` → líneas 192, 194, 216, 261

La segunda es la contraseña con la que **`core-migrate` CREA el rol
`jobhunt_core`**. A diferencia de `POSTGRES_PASSWORD`/`REDIS_PASSWORD`, no usan
`${VAR:?}`. Y `CLAUDE.md` afirma que la guarda garantiza «ninguna contraseña
publicada como defecto».

**Fix:** una regex `\$\{[A-Z_]*(PASSWORD|SECRET|KEY|TOKEN)[A-Z_]*:-[^}]+\}` en vez
de una lista de literales. Es el mismo error de diseño que A-02: **un guard que
busca valores concretos en vez de formas**.

## A-67 · Dos ajustes operativos se leen con `os.getenv` y escapan a TODO el aparato de configuración
**Área:** `backend/celery_app.py:38`, `jobhunt_core/cross_encoder.py:28` · **CONFIRMADO (hechos) / PROBABLE (impacto)**

- `CELERY_MAX_TASKS_PER_CHILD` no está en `Settings`, luego no está en el
  `.env.prod.example` generado y `test_env_example_covers_settings.py` **no puede
  verlo**. Y sí se fija en `docker-compose.yml:126` para `worker-ai`: un mando
  real, invisible en la documentación generada.
- **`CE_BACKEND` es el caso peor.** `jobhunt_core/requirements.txt` dice
  literalmente que `onnx-cpu` es el *«único backend viable en el NAS (torch-cpu
  descartado con evidencia)»*, el defecto del código es `torch-cpu`, y
  `grep CE_BACKEND` sobre todos los composes, `deploy/nas/` y los `.env*.example`
  devuelve **cero**. El día que se promueva una política de cross-encoder,
  **producción usará el backend descartado con evidencia.** Además `BACKEND` forma
  parte de la clave del motor y de la receta versionada
  (`cross_encoder.py:181,199`), así que la identidad de la política depende de una
  variable indocumentada leída en tiempo de import.

Hoy contenido (`matching-materialize-ce` es no-op sin políticas CE activas).
**Fix:** subir las dos a su `Settings`. Y atar `CE_BACKEND=onnx-cpu` en el compose
de producción **antes** de que la promoción del CE lo haga urgente.

## A-68 · La suite del BFF puede crear y truncar en el servidor de PRODUCCIÓN
**Área:** `backend/tests/conftest.py:35-36,89` · **CONFIRMADO**

La base de test se deriva cambiando sólo el **NOMBRE**
(`DATABASE_URL.rsplit("/",1)[0] + "/swissjobhunter_test"`), y la línea 89 abre un
motor admin contra `…/swissjobhunter` del **mismo servidor** para el
`CREATE DATABASE`. **No hay ninguna aserción sobre el host ni sobre el entorno.**

`CLAUDE.md` dice «Tests siempre contra la DB `swissjobhunter_test` — nunca contra
producción»: el **nombre** está garantizado, el **servidor** no. Un `.env`
apuntando al NAS basta para que el teardown haga `TRUNCATE ... CASCADE` en el
servidor de producción. Y el proyecto ya tiene el antecedente registrado en
memoria: «production DB was being truncated by test teardown».

**Fix:** `assert` de host/entorno en `_ensure_test_db`, con una variable explícita
de permiso para el caso legítimo.

## A-69 · Ningún test del frontend puede detectar un fallo de interacción, efecto o estado
**Área:** los 14 `*.test.*` de `frontend/src` · **CONFIRMADO**

Todos resuelven con `renderToStaticMarkup` (SSR a cadena) o con aserciones sobre
módulos puros. `grep -rln "testing-library|fireEvent|userEvent|renderHook|document\."`
→ **ninguno**. No hay `jsdom` ni `happy-dom` en `devDependencies`, no existe
`vitest.config.js`, y `vite.config.js` no tiene bloque `test:` → el entorno por
defecto es `node`. **Con `renderToStaticMarkup` no corre `useEffect` ni hay
manejadores de eventos**: el arnés captura regresiones de marcado y nada más.

Consecuencias concretas y verificadas: `MatchPage.jsx` —la pantalla principal— no
tiene test, y **A-01 es precisamente un fallo de render que este arnés sí habría
visto** si alguien hubiera escrito el caso; mientras que `WatchlistPage` (la mitad
abierta de A18-03, que es «no consume `isError`») es un fallo de **estado** que
este arnés **no puede ver ni cuando se arregle**.

**Fix:** `jsdom` + `@testing-library/react` y `test: { environment: 'jsdom' }`.
Mientras no esté, **no contar los 14 ficheros como cobertura de interfaz**.

## A-70 · Los ratchets del manifiesto de fuentes se desactivan solos si falta su clave
**Área:** `scripts/check_source_ownership.py:322-332` · **CONFIRMADO (latente)**

`ratchet.get("min_core_owned_sources", 0)` y, peor,
`ratchet.get("max_local_pending_sources", local_pending)` y
`("max_active_local_publishers", local_publishers)`: **el default es el valor
actual**, así que **borrar la clave hace que la comprobación pase siempre**. Hoy
las cuatro están presentes (41 / 59 / 0 / 0), así que es riesgo latente. Es un
ratchet que se suelta solo, en el guard que ya tiene CC 80 (A-33).

**Fix:** exigir las cuatro y fallar si falta alguna.

## A-71 · CI nunca construye las imágenes de PRODUCCIÓN
**Área:** `.github/workflows/ci.yml:262` · **CONFIRMADO**

`docker compose build` va sobre el compose base → `backend/Dockerfile` y
`frontend/Dockerfile`. **`backend/Dockerfile.prod` y `frontend/Dockerfile.prod` no
se construyen en ningún job.** El incidente del 2026-09-24 (imagen de dev
desplegada en producción → `unable to find user app`, BFF caído) está cubierto por
el guard de runtime `docker run --entrypoint id "$IMAGEN" app`
(`deploy_nas.sh:138`), pero **un `Dockerfile.prod` que simplemente no compile se
descubre al desplegar**, tras los gates de git y de remoto.

## A-72 · Toda imagen base flota, mientras cada dependencia pip está clavada
**Área:** `backend/Dockerfile`, `jobhunt_core/Dockerfile`, `frontend/Dockerfile.prod`, `docker/postgres-core/Dockerfile` · **CONFIRMADO**

`python:3.12-slim` (×3), `node:24-alpine` + `nginx:1.27-alpine`,
`pgvector/pgvector:pg16` y un `apt-get install postgresql-16-wal2json` **sin
versión**. `backend/requirements.txt` dedica 13 líneas de comentario a por qué los
rangos son inaceptables; **la imagen de la base de datos del corpus de 94k
vacantes se reconstruye desde una etiqueta móvil.**

**Fix:** fijar por digest al menos `pgvector/pgvector` y las dos bases del
frontend de producción.

| # | Resto de hallazgos de infra (LOW) | Área |
|---|---|---|
| A-73 | Ningún compose de `deploy/nas/` se valida sintácticamente. `compose-config` valida `base`, `base+dev`, `prod` y `core-local`; sobre `deploy/nas/` sólo pasa `check_no_secrets.py`. **La topología que de verdad corre puede podrirse** (clave mal indentada, servicio renombrado) sin que nada lo note hasta un `up -d` en el NAS | `ci.yml` |
| A-74 | `check_no_secrets.py` sólo mira `*.yml` (`:81 rglob("*.yml")`) y **se salta los comentarios** (`:58`). Un `.yaml`, un `.env` de ejemplo, un `.sh` o un `.md` no se revisan — y eso, combinado con la ceguera a la sintaxis `.env` (A-02), es exactamente por dónde se fue la credencial. Acierto que sí tiene: devuelve **2** si no encuentra ficheros, así que no es un no-op silencioso | `scripts/check_no_secrets.py` |
| A-75 | `check_core_release.py:44-49` comprueba una lista **FIJA** de cuatro servicios: un quinto servicio del core añadido al compose queda fuera de «todos corren la imagen del compose» sin aviso. (Renombrar uno de los cuatro sí falla ruidosamente) | `scripts/check_core_release.py` |
| A-76 | `# noqa` de reglas que ruff **no ejecuta**: no hay `ruff.toml` ni `pyproject.toml`, así que el set es el de defecto (E4/E7/E9/F). `sse_manager.py:87` (`BLE001`), `translation_service.py:413` (`S324`) y `cross_encoder.py:169` (`D401`) silencian reglas inactivas: son inertes y **sugieren un rigor de linter que no se aplica** | 3 ficheros |
| A-77 | `docker-compose.rehearsal.qnap.yml` quedó fuera de A19-25: `redis` cada **10 s** y `redis-core` cada **5 s**, mientras la copia desplegada `deploy/nas/rehearsal-r5.yml` ya está a 60 s. Redesplegar el ensayo desde el repo **reintroduce el churn que A19-25 corrigió** | compose |
| A-78 | `.env.example` de la raíz (42 claves) **no lo cubre ninguna prueba** — `test_env_example_covers_settings.py` sólo valida `backend/.env.prod.example`. Y `.env.example` es el fichero que el CI copia a `.env` (`ci.yml:223,261`), con `Settings` declarando 69 campos | `.env.example` |
| A-79 | Política de reinicio incoherente en `docker-compose.yml`: `worker`, `worker-ai`, `redis-core`, `core-worker` llevan `unless-stopped`; `postgres`, `redis`, `backend`, `core-api`, `frontend` no llevan ninguna. En dev da igual, pero `core-api` sin reinicio junto a `core-worker` con reinicio **es la asimetría que se copia** (en `prod`/`qnap` están todos) | compose |

---

# Los nueve checks del CI: verificados, sin no-ops

Se comprobó porque un guard que no corre es peor que no tenerlo.
`.github/workflows/ci.yml` define exactamente nueve jobs: `source-ownership`,
`backend-lint`, `backend-test`, `frontend-lint`, `frontend-test`, `core-lint`,
`core-test`, `compose-config`, `docker-build`. `grep` por `continue-on-error`,
`|| true`, `allow_failure` y `passWithNoTests` → **cero coincidencias** (la única
mención de `--passWithNoTests` es un comentario explicando por qué NO se usa).
`core-lint` ya es bloqueante. Ningún paso depende de un glob que pueda quedar
vacío. **Los nueve son reales.**

`ruff`: `ci.yml:36,161` instalan `ruff==0.15.14` y `backend/requirements-dev.txt`
lo fija igual ✓. `jobhunt_core/requirements-dev.txt` **no** lo incluye (el CI lo
instala suelto en `core-lint`) — correcto pero asimétrico. Todas las dependencias
de los `requirements*.txt` están con `==`: **el riesgo de versión migró a las
imágenes base** (A-72).

# Inventario de tests (clasificado, sin ejecutar)

| Suite | Ficheros | `def test_` | Lo ejecuta |
|---|---|---|---|
| `backend/tests/` | 84 | 521 | CI `backend-test` |
| `backend/tests_live/` | 1 | 1 | **nadie** — fuera de `testpaths` **a propósito y documentado** (gasta dinero en Groq) |
| `jobhunt_core/tests/` | 175 | 1.262 | CI `core-test`; **28 ficheros de integración** tras `pytestmark = skipif(not CORE_ADMIN_DATABASE_URL)`, que el CI **sí** define (`ci.yml:176`) → no se saltan |
| `tools/core/tests/` | 1 | 5 | CI `core-test` |
| `scripts/test_*.py` | 10 | 18 | **4 de 10** (A-64) |
| `frontend` (vitest) | 14 | — | CI `frontend-test`; **sin DOM** (A-69) |

**Sin `assert True` en ninguna suite.** Los `skipif` del núcleo están todos
condicionados a la presencia de la BD; ninguno es un skip permanente olvidado. El
único `pytest.skip()` inline no condicionado a entorno es
`test_integration_dev_eval.py:554`, que es un control legítimo.

Cobertura por módulo (heurística: ningún test menciona el nombre):
`backend/routers` 0 huérfanos, `jobhunt_core/tasks` 0, `backend/services` 1
(`profile_inputs.py`), `backend/tasks` 1 (`watermarks.py`). **Los huecos reales no
están en los módulos, sino en los `scripts/` (A-64, A-33) y en el frontend
(A-69).**

---

# Tabla de deriva DOCUMENTACIÓN vs CÓDIGO

Medido el 2026-11-02 contra `CLAUDE.md`, que se declara «revisado contra el código
el 2026-10-20». Lo que lleva fecha y aun así miente es lo que más cuesta, porque
se cree.

| Afirmación | Medido hoy | Veredicto |
|---|---|---|
| 41 productores nativos | **41** fuentes (diff vacío en ambos sentidos contra el manifiesto). Matiz: son **30 módulos** `.py`; 41 son los *nombres de fuente* | ✓ |
| 9 de la familia `swiss_schools_*` | **9** (`schuljobs` es otra fuente) | ✓ |
| Cadena del núcleo llega a `core0057` | Cabeza única `core0057`, sin ramas; **58 ficheros** (`core0008b_shadow_capture.py` queda fuera de la numeración) | ✓ |
| Cabeza legacy `a7c3d9e2f5b1` | Cabeza única, 41 revisiones, sin ramas | ✓ |
| **14 cadencias** en el beat del núcleo | **13.** La 14ª (`saved-searches-run-due`) depende de `CORE_SAVED_SEARCH_EXECUTION_ENABLED`, que es `False` por defecto y **no se fija en ningún compose ni `.env*.example` del repo** — aunque sí está a `'true'` en los cuatro servicios de producción (ver A-08). Y `CLAUDE.md` la lista entre «las de cada 5 min» | ✗ |
| 5 citas en el APScheduler del BFF | **5** | ✓ |
| Resto de cadencias del núcleo (outbox/entrega/matching 5 min; idempotencia, salud de cosecha y credenciales 1 h; 4 diarias 05:20→05:35→06:15→06:40) | Todas confirmadas | ✓ |
| `matching.py` 2.684 LOC | **2.680** | ≈ |
| `RawListingSink` 1.337 LOC · `handle` 190 líneas | **1.338** · **188** | ≈ |
| **DOS grados E** (`native_myscience` y `native_schuljobs`, 32 c/u) | **UNO**: `SchulJobsProvider.fetch_new` E(32). `MyScienceProvider.fetch_new` es **D(30)** | ✗ |
| 23 funciones en grado D | **22** en `backend`+`jobhunt_core`; **24** contando `scripts`+`tools` | ✗ |
| Implícito: ningún grado F, y la cota A19-13 «se quedó sin objeto» | **Hay un F**: `check_source_ownership.py:242` = **CC 80**, la función más compleja del repo, en el camino de CI y de despliegue | ✗ **(A-33)** |
| El supervisor espera «cualquiera de los **15**» | **16** nombres en `ESPERADOS` | ✗ |
| «La topología REAL del NAS está versionada en `deploy/nas/`» | **Incompleta en dos sitios**: falta `swissjob-erasure-cdc` (A-14) y faltan `redis`/`postgres` del BFF, que no están en `swissjob.configured.yml` (A-61) | ✗ |
| «Tests siempre contra `swissjobhunter_test` — nunca contra producción» | El **nombre** está fijado; el **servidor** se hereda de `DATABASE_URL` sin aserción, y se abre un motor admin contra `…/swissjobhunter` | ✗ **(A-68)** |
| `check_compose_exposure.py` garantiza «ninguna contraseña publicada como defecto» | **Dos publicadas en 9 sitios** del compose base: `core_redis_dev` y `jobhunt_core_dev` | ✗ **(A-66)** |
| `docker compose exec -T backend python -m pytest` / `ruff check` | **Fallan en imagen limpia**: ni `pytest` ni `ruff` están en `requirements.txt`, y `check_source_ownership` los **prohíbe** ahí | ✗ **(A-65)** |
| `backend/.env.prod.example` se genera desde `Settings` y un test lo fija | ✓ Cierto. Cifras: `Settings` = **69** campos, ejemplo = **72** claves. El «28 de 130» del docstring es histórico, y el `.env.example` de la raíz **sigue existiendo** (42 claves, sin prueba) | ✓ con cifras viejas |
| `CORE_HTTP_TIMEOUT_SECONDS` = 5 s | **120 en producción**, ×2 en tres clientes = 240 s | ✗ **(A-09)** |
| «750 passed» backend / «1.785 passed · 1 skipped» núcleo | No verificable sin ejecutar (restricción deliberada de esta pasada) | — |
| Ajustes sin lector / lectores no declarados | 1 sin lector (`GROQ_CONCURRENCY`, A-45); **2 lectores no declarados** y por tanto ausentes del `.env.prod.example` generado: `CELERY_MAX_TASKS_PER_CHILD` y `CE_BACKEND` (A-67) | ✗ |

**Nota de método:** `radon` 6.0.1 está en el host, así que las cifras de
complejidad son medidas. Distribución total del repo: **3.729 A · 584 B · 209 C ·
24 D · 1 E · 1 F**.

---

# Correcciones al propio inventario DT-148

**Esta es la sección con más valor práctico del informe.** El relevo §9 documenta
que el plan se equivocó trece veces en lo ya ejecutado y que ninguna se detectó
leyendo. Se verificaron los 15 puntos de C y D midiendo. **Nueve tenían algo mal**,
y dos habrían dejado tests en VERDE que ya no prueban nada.

## Lo que rompería tests sin que nadie se enterase

### D3 paso 4 rompe dos costuras que el plan declara inexistentes
El plan afirma literalmente **«Ningún otro test llama a privados (grep: 0)»**. Hay
**dos**, y las dos caen en el grupo (4), que el paso 4 convierte en funciones de
módulo:

- `jobhunt_core/tests/test_integration_archive.py:329-338` —
  `class SinkConCarrera(RawListingSink)` **sobreescribe `_lock_vacancies`** para
  reproducir el interleaving del auditor. Si deja de ser método, **el override
  nunca se llama y el test pasa sin reproducir nada**.
- `jobhunt_core/tests/test_integration_identity.py:697` —
  `await sink._write_dedup_candidates(s, pairs)` directo sobre la instancia.

Un test de carrera que no reproduce la carrera es peor que no tenerlo.
**Fix:** añadir los dos a la lista de métodos-costura de D3 paso 4, o re-apuntar
los tests en el mismo commit.

### D1 omite `MAX_SCAN_TUPLES`, que es constante compartida Y objetivo de parche
`matching.py:38 MAX_SCAN_TUPLES = 20000` **no aparece en ninguno de los siete
módulos de destino** que D1 enumera, y tiene dos consumidores que el plan no
nombra:

- `jobhunt_core/dedup.py:45` lo importa de `matching` y lo usa en `:595`
  (`SET LOCAL hnsw.max_scan_tuples`): **`dedup` depende de `matching` por una
  constante de presupuesto ANN**.
- `test_integration_matching.py:782` →
  `monkeypatch.setattr(matching, "MAX_SCAN_TUPLES", 50)  # fuerza la inanición`.
  Si la constante se muda y el lector va con ella, **el parche deja de surtir
  efecto y el test pasa sin probar nada**. Es textualmente la trampa «objetivos de
  monkeypatch» que D1 advierte, en un nombre que D1 no vio.

## Desfases de línea: toda ubicación del plan hay que recalcularla
Las líneas del plan se tomaron en `8d01c50`. Desde entonces `api/v1.py` pasó de
**1057 → 1155 LOC (+98)**, `harvest/sink.py` **+5**, `matching.py` **−4**,
`_persist_eval_rows` **−68**. El §8 del relevo manda «corta por RANGO DE LÍNEAS
verificado»: aplicar esa regla con las líneas de C3 **cortaría ~98 líneas
equivocadas de `api/v1.py`**.

## Punto por punto

| Punto | Lo que dice el plan | Lo que es (medido) |
|---|---|---|
| **C1** | 10 ficheros con `_read`, 7 copias «idénticas salvo `SOURCE_NAME`», ~410 LOC | **9** ficheros (`native_isp_workday` no lo tiene). Variante (a) = **6**, no 7 (`native_swissdevjobs._read` no reintenta, no tiene `_pause`, UA distinto). **No son idénticas**: diffs de 9-27 líneas y **tres tipos de retorno** (`str`, `bytes`, `dict` — el último parsea JSON y valida contrato dentro del `_read`). LOC real **≈382**. El plan **sobrestima la homogeneidad** |
| **C2** | predicado `NOT IN` 4× | **5 veces**: la quinta en `matching.py:1357`, fuera de las cuatro funciones del feed. Líneas −4 |
| **C3** | 4 bloques de tenencia | **5**. El quinto en `v1.py:713-719` (`FOR UPDATE` + `error_404`) — justo el caso de `require_owned_profile(…, for_update=True)`. El plan **infravalora** el retorno; sus líneas están **+98** desfasadas |
| **C6** | `_PAYLOAD_ERRORS` ×3; 6 clases `CoreUnavailableError`; 10 `except` | ×**2** (B3 borró la de `profiles` el 21-10). **5** clases: **`backend/services/applications/` NO EXISTE**. `except` = 4 (+3 `isinstance`) = **7**, no 10. Guard en **6** formas, y una séptima que lanza otra excepción (`watchlist_tasks.py:81-82`). **Las cifras se midieron antes de ejecutar B3 y B3 ya invalidó dos** |
| **D1** | 2.684 LOC, MI C, 50 nombres externos | 2.680 LOC. **MI = C (0.59) CONFIRMADO**. `set_after_revalidation_hook` **no se parchea** (se invoca como inyector) y `_margen_cierre` tampoco (eso es D6). Omite dos parches reales (`materialize_misses` ×2, `MAX_SCAN_TUPLES`). «50 nombres» → limpio son **25** (el grep del plan contaminaba con `services.matching.*` del BFF) |
| **D2** | CC 28 | CC **D(27)** |
| **D3** | clase 1.337 LOC, `handle` 190, `unnest` ×3 | 1.338 y 189 ✓. Líneas **−5**. `unnest` ×3 ✓ **+2 variantes con `:ids`** (`:483,:1388`) que el plan no cuenta |
| **D4** | 4 filas; `generate_document` CC 20, `receive_event` CC 19 | Esas dos ✓. Pero **`review_suggestion` = CC B(8)** y **`delete_all_user_data` = CC A(5)**: **ya cumplen el criterio de cierre del propio punto**. D4 se puede recortar a la mitad — lo que queda de ellas es un argumento de capas, no de complejidad |
| **D5** | `_altas_exact_match`, **40 referencias** | **8** referencias. Y el paso 3 («derivar de `_PROVIDERS`») **perdería dos altas**: `portfolio_vacancies` y `school_ingest` están en la tabla y **no son providers** — derivarla dejaría sin normalizador a la fuente de las vacantes a las que el usuario se apuntó, que es el fallo que `registry.py:85-90` documenta como ya ocurrido (G4-P2-4). A la inversa, `Financejobs` e `IrishJobs` se registran **desde el `__init__` del provider**, por efecto lateral del orden de carga. Son **TRES** mecanismos para lo mismo, no dos, y E5 los describe mal |
| **D7** | `FilterPanel` carga fuentes con `useEffect`+`useState` | **Fila CADUCA**: DT-148 A11 ya retiró el desplegable; `useState` no está en el fichero y `getSources` no existe en `frontend/src/` |
| C4, C5, C7, C8, D6 | — | **Exactos o con desfase trivial.** C7 y D6 son los más precisos del inventario |

## Dos ciclos de import que D6 no cubre
Análisis AST del grafo completo:

| Ciclo | En el inventario |
|---|---|
| `matching ⇄ materialization` | **Sí** — D6 |
| `registry → portfolio_vacancies → sink → registry` | **No** |
| `registry → school_ingest → sink → registry` | **No** |
| `models.user ⇄ models.user_profile` (BFF, bajo `TYPE_CHECKING`) | No — y es **benigno** |

Los dos del `registry` son el mismo problema que D6 un nivel más abajo: D6 cubre
`school_ingest`/`portfolio_vacancies` importando privados del sink, pero no que el
sink importe de vuelta al registry. El código lo reconoce en `registry.py:101`.
Hay **56 imports perezosos** en el núcleo y 52 en el BFF: el síntoma, no la causa.
**Fix:** al ejecutar D6, romper también `sink → registry` moviendo `ensure_handler`
a un módulo sin dependencias.

## El censo de complejidad de `CLAUDE.md` está desfasado en las DOS direcciones

| | `CLAUDE.md` (2026-10-20) | Medido 2026-11-02 (radon 6.0.1, sin tests) |
|---|---|---|
| Grado F | — (no lo menciona) | **1** (`check_source_ownership.py:242` = **80**) |
| Grado E | 2 (myscience, schuljobs, 32 c/u) | **1** (`native_schuljobs.fetch_new` = 32) |
| Grado D | 23 | **20** (`native_myscience.fetch_new` = **30**, es D, no E) |

Es la clase de cifra de la que el propio `CLAUDE.md` avisa, pero aquí **lleva
fecha y aun así miente**. **Omitir el único F del repo** es el error que importa:
quien lea ese párrafo concluye que no hay nada peor que un E.

---

# Verificaciones NEGATIVAS — comprobado y sin hallazgo

Se listan para que nadie vuelva a gastar tiempo en ellas.

**Frontera y contrato**
- `grep -rn "from jobhunt_core" backend --include=*.py` → **0 resultados**. El BFF
  no importa ni un símbolo del núcleo: todo pasa por `/v1`. Es el criterio de
  verificación 5 de D1 y **se cumple hoy, antes de ejecutarlo**.
- **43 × `Depends(get_current_user)`** en los routers y ninguna otra dependencia de
  identidad. Un solo patrón.
- **Nombres de cola**: las 20+ rutas están centralizadas en `celery_app.py`
  (`task_routes`). Ninguna hardcodeada en una tarea.

**Seguridad**
- **IDOR en el BFF**: los 11 routers derivan la identidad de `current_user.id`;
  ningún esquema de **entrada** acepta `user_id` ni `profile_id`. Cachés claveadas
  por perfil; la de documentos incluye el id de usuario; el SSE publica en
  `sse:{user_id}`. Las búsquedas guardadas las acota el BFF con
  `if row.profile_id != pid: raise`.
- **IDOR en `/v1`**: ownership por consumer verificado en los 12 routers. El caso
  que parecía abierto (feedback sin `profile_owner`) está cubierto por
  `run_idempotent(..., profile_id=...)`, que hace el `SELECT ... FOR UPDATE` con
  `consumer_id` **antes** del handler, también sin `Idempotency-Key`. Única
  excepción: A-30.
- **Inyección SQL**: ni un valor de usuario interpolado. Los f-strings dentro de
  `sa.text()` son identificadores de código o enteros. `migrate.py:85` interpola la
  contraseña pero la valida antes con `[A-Za-z0-9_-]{8,128}`. Los `LIKE` escapan
  `%`, `_` y `\` en los tres sitios.
- **SSRF**: `HttpDestinationTransport` resuelve contra el allowlist de
  configuración y **falla cerrado**; `/v1/providers/jsearch/search` sólo deja elegir
  query/país/página contra un endpoint constante. No hay punto donde el usuario
  influya en una URL de salida.
- **Secretos en logs**: filtro de redacción en todos los loggers vivos del BFF,
  incluido el traceback vía `record.exc_text`. Adzuna —el único provider con la
  credencial en la query— convierte `httpx.HTTPError` en `_RetryablePageError`
  `from None`, así que la URL nunca entra en el mensaje.
- **Autenticación**: sin oráculo de enumeración (hash señuelo precalculado,
  `is_active` comprobado DESPUÉS de la contraseña, mensaje único); bcrypt sobre
  `base64(sha256(pw))`, que elimina el truncado a 72 bytes; `HS256` fijado y pasado
  como lista cerrada (no hay confusión HS/RS ni `alg:none`); `SECRET_KEY` con
  defecto insegura **pero el arranque aborta** si sobrevive.
- **GDPR**: el borrado del núcleo cubre las 10 tablas de `_ERASE_TABLES` más las
  que cascadean desde `profiles`, los recibos de idempotencia y el outbox.
  **`erasure_restore.py` NO es un agujero**: no restaura nada, re-ejecuta el borrado
  sobre una base restaurada; exige artefactos 0600 y un sello sha256, y aborta si el
  perfil sigue presente.
- **XXE en DOCX**: mitigado (`resolve_entities=False`).
- **`VITE_*`**: `grep -rn "import.meta.env" frontend/src/` → **ninguna**. No hay ni
  una variable de entorno en el bundle.

**Rendimiento y caché del feed**
- Tres de las cuatro cotas de la caché del feed se sostienen en el código. La cuarta
  es A-03.
- La carrera «página de un recorrido cuya versión cambió a mitad» está cubierta por
  triple comprobación (`version_total == total`, relectura al terminar, y época
  capturada al entrar y verificada tras cada GET).
- `CoreFeedback._write` invalida `_feed_cache`, `_facets_cache` y `_etag_cache` del
  perfil **y** publica en el bus, también en el 404.
- `_feed_cache` comparte dicts sin copia profunda, pero el invariante «los items no
  se mutan jamás» se sostiene.
- **Virtualización sobre 3.000 elementos: no hace falta y no falta.** En modo
  facetas el servidor sirve la página de 50; en legacy `MatchPage.jsx:162-164`
  recorta. Los agregados están memoizados, `MatchCard`/`JobCard` son `memo()`, los
  handlers son `useCallback` sobre `mutate` estable, y las `key` son estables y
  prefijadas por lista. El trabajo de A17/L4/T7 está bien hecho.
- **Limpieza de efectos**: revisada una a una, toda correcta (debounce, observer,
  listeners, `body.style.overflow`, bandera `cancelado` + `clearTimeout` + `close`).
  El backoff exponencial del SSE con tope de 60 s y reset en `connected` está bien.

**Núcleo**
- **Patrón Celery**: ninguna tarea es `async def`; las 22 usan
  `def task(): asyncio.run(_impl())` con engine desechable. Las 5 colas `core.*`
  están cubiertas por los tres workers del split: **no hay cola huérfana**.
- **Timeouts HTTP**: los 11 helpers pasan timeout explícito. Ninguna petición sin
  cota.
- **Commit del cursor**: `harvest/runner.py` es correcto — lock, re-lectura,
  fencing por `claim_token`, rollback conjunto. No hay camino que avance el cursor
  tras un sink fallido.
- **`erasure.py` y `shadow_change_log`**: el camino **sigue siendo coherente**.
  `core0056` conservó la tabla a propósito y lo dejó escrito; el trigger
  `redact_erased_shadow_payload` existe y el consumer `swissjob-shadow` sigue vivo.
  Sólo queda el residuo de A-40.
- **Alembic del BFF**: una sola cabeza (`a7c3d9e2f5b1`), 41 revisiones, sin
  huérfanos. Las cuatro tablas de runtimes retirados están excluidas a propósito en
  `env.py:28-45`.
- **`jobs` no tiene ningún escritor** (grep exhaustivo: ORM, `pg_insert`,
  `sa.update/delete`, SQL crudo). El único `UPDATE jobs` está en una migración.
- **La alerta de profesor lee el catálogo del núcleo** (`alert_tasks.py:86`).
  Cierto.
- `profile_erasure.reconcile_replica` (CC 22) y `erasure_restore.reconcile` (CC 24)
  parecían duplicación a los dos lados de la frontera: **son lados distintos del
  mismo protocolo**. No hay duplicación.
- `notifications.py` tiene 7 accesos a datos en el router pero todas sus funciones
  son CC ≤ A(5) con `select()` del ORM: **no es un caso D4**.
- **No existe capa `repositories/`** y eso es divergencia **deliberada**: el
  `CLAUDE.md` del proyecto no la prescribe (sí el global) y el acceso a datos vive
  en `services/`. No es incumplimiento.

---

# Rectificaciones de este informe

Dos lecturas mías fueron equivocadas. Se dejan escritas porque el método importa
más que el resultado.

**1. El log de `swissjob-erasure-cdc` no indica avería.** Son 862 líneas, **todas**
avisos `profile erasure drain deferred (<error>)`, del 2026-09-13 a hoy, y a
primera vista parece que el borrado GDPR nunca ha funcionado. **Lo desmiente la
ejecución**: lanzando el camino exacto del bucle dentro del contenedor,
`drain_erasures` → OK en 1,5 s y `reconcile_replica` → OK en 3,24 s. El módulo
**sólo registra los fallos, nunca los éxitos**: a una iteración cada 30 s son
~2.880 intentos diarios, y los 62-120 fallos recientes por día son un **2-4 %**
transitorio (el NAS con 2 CPUs y carga 9-15). Lo reportable no es el fallo, es la
**observabilidad**: un bucle que sólo registra fallos no permite distinguir «2 %
sano» de «roto desde el primer día» sin entrar a ejecutar. Esa ambigüedad costó
cuatro sondas. **Fix pequeño:** registrar el éxito a nivel DEBUG con el recuento, y
dar al reconciliador su propio presupuesto de timeout en vez de heredar el del feed
(`CORE_HTTP_TIMEOUT_SECONDS` está sin declarar en ese contenedor y toma el defecto
`5.0`, pensado para el recorrido del feed — ver el comentario H9/T12 en
`config.py:245`).

**2. La recomendación obvia para A-02 era mala.** El informe de seguridad proponía
«añadir `docs/` al paso del CI». Al probarlo, el guard devuelve **0 hallazgos sobre
el fichero que contiene la credencial**: sólo entiende YAML. Añadir `docs/` habría
producido un CI en verde y una falsa sensación de seguridad. El fix real es
enseñarle la sintaxis `.env` **antes** de ampliarle el alcance.

---

# Información faltante y supuestos

- **No se pudo confirmar si la credencial de A-02 sigue viva.** El intento de
  compararla contra producción sin imprimirla fue bloqueado por el clasificador de
  permisos, con razón, y no se rodeó. Es una comprobación del propietario.
- **No se pudo leer la base de datos**: `postgres` local lleva 11 días parado
  (A-58). Por eso A-04 y A-08 quedan **PROBABLE en su impacto productivo** aunque el
  defecto esté CONFIRMADO en el código. Las dos consultas de verificación están en
  sus fichas y son de lectura.
- **No se ejecutó ningún `pytest`** (restricción deliberada: dos corridas contra
  `swissjobhunter_test` se pisan por el `TRUNCATE CASCADE` del teardown). La
  auditoría de tests se hizo leyendo.
- `jobhunt_core/delivery.py` (43 KB) sólo se recorrió por su esqueleto de estados
  (claim/lease/fence/dead-letter/veneno) y no apareció nada. Dado lo mucho que
  sostiene, **merece un pase dedicado que esta auditoría no le dio**.
- Los 32 providers no se barrieron exhaustivamente con la pregunta de contrato que
  A-04/A-05/A-06/A-07 sugieren (ver «Próximos pasos»).

---

# Top 11 arreglos de mayor valor

Ordenados por (impacto × certeza) / coste. Los cuatro primeros son de menos de
veinte líneas cada uno.

1. **A-01 · `/match` en blanco** — dos líneas más un `ErrorBoundary`. Es la
   pantalla principal del producto y lleva 13 días caída en producción.
2. **A-02 · credencial publicada** — rotar, y **enseñar al guard la sintaxis
   `.env`** antes de ampliarle el alcance.
3. **A-11 · `logout` que no revoca** — una línea en `authApi` resucita un mecanismo
   entero ya construido y probado (T9).
4. **A-61 + A-62 · cerrar el redis de producción** (`--requirepass` + `noeviction`)
   **y apuntar `check_compose_exposure.py` a los nueve composes**, no a uno. Hoy el
   guard comprueba el único fichero que está bien.
5. **A-16 · `RATE_LIMIT_TRUST_PROXY=true`** — una variable; la precondición de
   seguridad ya la cumple `nginx.conf:39`, y hoy son 5 logins/minuto para todo el
   despliegue. **Pero no es un arreglo libre** (comprobado el 2026-11-03): existe
   **sólo** en `backend/config.py:144` y `backend/.env.prod.example:44`, **en
   ningún compose**, así que activarlo en producción exige tocar
   `deploy/nas/swissjob.configured.yml` o el `.env` del NAS → **mismo bloqueo que
   el punto 4**.
6. **A-13 + A-18 · cota de descompresión y firma del fichero en la subida de CV** —
   un solo parche cierra los dos.
7. **A-03 · no sellar como verificado lo que no se confirmó** — restaura la cuarta
   cota de la caché del feed.
8. **A-14 + A-29 · versionar `erasure-cdc` y reconstruirlo** con la imagen vigente.
   Van juntos y es el camino de borrado GDPR.
9. **A-10 · retirar o reescribir `send_watchlist_digest`** — una cita diaria que
   devuelve `ok` sin poder notificar nada es peor que una que falla. **Es
   DECISIÓN del propietario, no tarea de código** (re-medido el 2026-11-03): son
   ~60 líneas de lógica real —marcadores de idempotencia, marca de agua, envío—
   cubiertas por `tests/test_scheduler.py`, y reescribirlo contra el feed del
   núcleo tiene el tamaño que tuvo DT-148 A1, que era un ítem entero.
10. **A-07 + A-04 + A-05 · el contrato de `fetch_new`** — `blocked_with` en los
    cuatro providers ciegos, separar «presupuesto» de «fallo», **y un test de
    contrato sobre el registro** para que no se vuelva a aplicar provider a
    provider. Los tres son el mismo arreglo y el test es lo que los cierra. *(El
    detonante de A-04 está desarmado hoy en producción — es trampa latente, no
    urgencia.)*
11. **A-68 · `assert` de host en el `conftest` del BFF** — cuatro líneas que
    impiden que un `.env` mal apuntado trunque producción, que es un accidente
    que el proyecto YA tuvo una vez.

**Deliberadamente fuera del top:** A-08 (recorrido de búsquedas guardadas) baja a
MEDIUM porque producción desmiente el bucle; su mitad accionable es el 1,15 M de
tombstones, que no es urgente pero sí creciente.

---

# Próximos pasos de auditoría

1. **Levantar `postgres` y correr las dos consultas** de A-04 y A-08 para convertir
   sus PROBABLE en CONFIRMADO o descartarlos.
2. **Barrer los 32 providers con una sola pregunta de contrato**, que es donde se
   concentra casi todo lo que apareció en el núcleo: *para cada salida posible de
   `fetch_new`, ¿qué terna `(complete, error, blocked_with)` produce, y coincide con
   lo que `runner.py:224-268` va a hacer con ella?* Una tabla de 32 × 4 cerraría
   A-04, A-05, A-06 y A-07 de golpe y evitaría la siguiente ronda de parches uno a
   uno.
3. **Un pase dedicado a `delivery.py`** (43 KB, sólo recorrido por su esqueleto).
4. **Un render del primer estado por cada página del frontend** como test — es lo
   que habría parado A-01 y detectaría el resto del inventario A-31.
5. **Meter A-32 y A-33 al ledger como puntos propios** antes de dar DT-148 por
   cerrada: el primero contradice la premisa de D1 («el único fichero con MI C») y
   el segundo es el peor número de complejidad del repositorio, sin cota que lo
   ampare, en el primer guard del CI.
