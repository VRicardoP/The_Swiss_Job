# DT-144 · Contexto de relevo

> **Qué es esto.** Lo primero que debe leer quien retome la centralización de la
> cosecha. No sustituye a nada: el objetivo manda desde
> [CONTRATO_CENTRALIZACION_TOTAL.md](CONTRATO_CENTRALIZACION_TOTAL.md), la
> evidencia por portal vive en los checkpoints de
> [DEUDA_TECNICA.md](DEUDA_TECNICA.md) §DT-144 y el inventario verificable en
> [source_ownership.json](source_ownership.json). Este fichero explica **cómo se
> está haciendo**, qué trampas ya se pisaron y qué queda, para que no haya que
> reconstruirlo leyendo veinte checkpoints.
>
> Última actualización: **2026-10-15** (cuatro residuos cerrados; 13 portales
> pendientes, todos de SwissJob).

---

## 1. En una frase

Core debe ser el único propietario de la captura externa de ofertas; SwissJob y
ReactPortfolio deben consumir el corpus común sin cosechar, sin credenciales de
portal y sin tráfico hacia ellos. Cada portal se migra como unidad vertical
completa (sección 7.1 del contrato) y **nada se corta en producción** hasta la
fusión final aprobada por el propietario.

## 2. Estado exacto (2026-10-15)

| Repositorio | Rama de trabajo | Cabeza | `main` remoto (INTACTO) |
|---|---|---|---|
| SwissJob/Core (`The_Swiss_Job`) | `feat/fase-a-core` | `0725a42` | `e59283b83ffc5deb0ff0bb82efffc1c04a28123b` |
| ReactPortfolio (`ReactPortfolioServer`) | `feat/dt144-free-gate` | `ffcc0ba` | `5bf457afbde5b44a995acce4ef241324f066f836` |

El worktree de ReactPortfolio está en `/tmp/reactportfolio-dt144-free-gate`
(**`/tmp` es volátil**: si no existe, recrearlo con `git worktree add` desde
`/home/lothar/Public/ReactPortfolio/backend`).

Inventario: **58 fuentes · 16 `local_removed` · 26 `local_pending` · 16
`retired`**, y el ratchet cruzado devuelve
`{"core_owned":16,"local_pending":26,"sources":58}`. Esas cifras **describen
producción** y por eso no se mueven al migrar en rama: una fuente migrada
conserva `state=local_pending` con `core_adapter: true` hasta el corte agrupado.

### Portales con adaptador nativo en rama

Jobicy, RemoteOK, Himalayas, SwissTechJobs, ICTJobs, SwissDevJobs, Job-Room,
Adzuna, JSearch, DynamiteJobs, JobScout24 (los once de ReactPortfolio) más
myScience y gastrojob (los dos primeros de SwissJob).

### Faltan 13, todos de SwissJob

`tes`, los ocho `swiss_schools_*`, `stelle_admin`, `proz`, `remoteco` y
`schuljobs`. Sus condiciones particulares están en §6.

## 3. Cómo se migra un portal (receta ya rodada)

Los pasos son los de la sección 7.1 del contrato; esto es lo que significan en
la práctica de este repositorio.

1. **Preflight.** Leer rama, upstream y los dos `main`; comprobar que el
   checkpoint anterior tiene su CI verde. `git status` antes de tocar nada: en
   SwissJob hay cambios ajenos a DT-144 permanentes (ver §7).
2. **Trazado.** Localizar el productor, su registro, su despacho, el consumidor
   y sus credenciales. **El consumidor cambia según el producto:**
   - **ReactPortfolio** expone seis rutas HTTP por fuente
     (`/recent`, `/stats`, `/by-tag`, `/by-location`, `/refresh-cache`,
     `/health`). Hay que conservarlas: las sirve el adaptador compartido
     `services/core_source_compat.py`, y basta añadir el `SourceSpec` para que
     `routers/core_source_compat.py` las genere.
   - **SwissJob** no tiene rutas por fuente. Su BFF ya lee el feed del núcleo
     (`backend/services/matching/core_client.py`, `/v1/profiles/{id}/matches`)
     **sin lista blanca por fuente**, así que el traspaso es sólo apagar el
     productor local.
3. **Línea base viva y SELLADA.** Capturar la respuesta real con las cabeceras
   que usará Core, guardarla en el scratchpad y **replayar el productor legado
   EXACTO sobre esa captura**. Las huellas SHA-256 de esa ejecución son el
   patrón contra el que se mide el adaptador. Capturar también las páginas de
   detalle que el productor pediría.
4. **Core mínimo.** Reutilizar las costuras existentes
   (`browser_headers`, `html_response`, `rss_text`, `jobicy_metadata`,
   `search_metadata`, `provision_scopes`). **Nunca una dependencia nueva**: el
   HTML se lee con la biblioteca estándar sobre `TagStackParser`.
5. **Paridad byte a byte.** Replayar el adaptador contra la MISMA captura y
   comparar campo a campo; cualquier diferencia se corrige o se documenta con
   evidencia. Las mejoras deliberadas se justifican midiendo (§5).
6. **Pruebas por capas** + doble ciclo contra PostgreSQL real + provisión opt-in.
7. **Apagado local** y actualización del inventario.
8. **Suites completas, lint, ratchets y CI.** Commit funcional estrecho, push de
   la rama feature, y sólo después el commit documental con la CI ya verde.
9. **Checkpoint** en DEUDA_TECNICA con el siguiente paso exacto.

### Comandos que se usan de verdad

```bash
# Pruebas del núcleo (el override dev es el que monta el árbol de trabajo)
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm core-migrate python -m pytest jobhunt_core/tests -q

# Suite del BFF de SwissJob (contenedor efímero; usa swissjobhunter_test)
docker compose run --rm -v "$PWD/backend:/app" -w /app backend \
  python -m pytest tests/ -q

# Lint de TODO el repo (lo que exige el CI)
docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo backend \
  sh -c "ruff check --no-cache . && ruff format --check --no-cache ."

# Ratchets
python3 scripts/check_source_ownership.py \
  --portfolio-root /tmp/reactportfolio-dt144-free-gate
python3 scripts/check_no_secrets.py

# ReactPortfolio (su CI sólo ejecuta estas dos cosas)
cd /tmp/reactportfolio-dt144-free-gate
python3 scripts/check_local_harvest_ownership.py
/tmp/dt144-backend-venv/bin/python -m pytest tests -q
```

El entorno `/tmp/dt144-backend-venv` tiene `bs4`, `aiohttp`, `pydantic-settings`,
`black` y `pytest-timeout`: es el que permite replayar los productores legados
fuera de Docker. El entorno de pruebas versionado de ReactPortfolio está en su
`tests/conftest.py`; sin esas variables `Settings` no importa.

## 4. Cómo se comprueba la CI

`The_Swiss_Job` **responde a la API anónima de GitHub**, así que la CI se puede
verificar sin credencial:

```bash
curl -sS https://api.github.com/repos/VRicardoP/The_Swiss_Job/actions/runs/<id>
curl -sS https://api.github.com/repos/VRicardoP/The_Swiss_Job/actions/runs/<id>/jobs
```

`ReactPortfolioServer` **es privado y aquí no hay token**: su CI NO se puede
comprobar desde este host. No se declara verde por reproducir su mandato en
local. Pendiente de confirmar por el propietario o con un token de lectura: los
runs de `04a020d` y `ffcc0ba`.

Los nueve trabajos del CI de SwissJob son `source-ownership`, `backend-lint`,
`core-lint`, `backend-test`, `core-test`, `frontend-lint`, `frontend-test`,
`compose-config` y `docker-build`. `backend-test` y `core-test` tardan ~15-20
min: son los últimos en cerrar.

## 5. Decisiones de diseño ya tomadas (no re-litigar sin evidencia)

- **Core no reproduce el early-stop por cursor de los scrapers.** Barre el
  presupuesto declarado y deja que el sink resuelva identidad («emisión total»).
  Medido en myScience: el early-stop del productor dejaba **cuatro ofertas de 26
  invisibles** detrás de una página ya conocida.
- **Nada de dependencias de parseo en Core.** `TagStackParser`
  (`jobhunt_core/harvest/providers/html_response.py`) reproduce `select()` lo
  bastante para estos portales. Cierra implícitamente lo que quedó abierto,
  descarta cierres huérfanos y excluye `script`/`style`/`template`/`noscript`.
  Existe porque un parser que suponía etiquetas balanceadas **cortó una
  descripción real siete párrafos antes del final**.
- **`published_at` sólo del dato del portal.** Si no hay fecha utilizable se
  OMITE la clave: una cadena vacía llega al agregado como `published_at` vacío,
  que no es lo mismo que «desconocido».
- **Identidad estable y no derivada de campos mutables.** Donde el portal expone
  un id propio se usa (`id:<n>`), no la URL cuyo slug lleva el título.
- **Las descripciones no se recortan para que quepan en un cursor.** En
  myScience llegan a 9.866 caracteres: truncarlas alteraría cinco de 26 ofertas
  en cada ciclo, así que se paga el detalle por barrido.
- **Error ≠ vacío, siempre.** Un 200 ilegible es un fallo VISIBLE
  (`ProviderResponseError` o `error=` en el `FetchResult`), nunca «0 ofertas».
  El caso canónico es el veredicto de cinco vías de gastrojob.
- **Un barrido que no devuelve nada falla.** El productor conservaba su caché
  anterior en ese caso; Core, en silencio, archivaría un corpus vivo.

## 6. Los 13 que faltan, con su condición real

| Fuente | Estado medido | Nota para quien la coja |
|---|---|---|
| `tes` | sano, `__NEXT_DATA__`, 1 oferta por página | el siguiente de la lista |
| `swiss_schools_nae`, `_isp`, `_iscs`, `_ecolint`, `_hautlac` | sanos | misma receta |
| `swiss_schools_inspired`, `_isb`, `_zis` | tablero vacío | **preservar la semántica de «vacío verificado»**: un cero verificado no es un cero silencioso |
| `stelle_admin` | sano, Playwright | la imagen de Core **no lleva Chromium**; decisión del propietario: reimplementar sin navegador si es factible |
| `proz` | 46 errores seguidos, **ninguna cosecha correcta** | investigar si hay transporte funcional antes de migrar: sin línea base no hay paridad que medir |
| `remoteco` | 54 errores seguidos, **ninguna cosecha correcta** | igual que `proz` |
| `schuljobs` | último intento sin desenlace, cursor sin bootstrap completo | resolver paridad antes de migrar |

Los ocho `swiss_schools_*` tienen además una particularidad: **hoy publican
observaciones al núcleo** por `POST /v1/schools/{monitor_id}/jobs`. El núcleo ya
es dueño del corpus escolar, su ciclo de vida, el feedback y las candidaturas;
lo que NO es dueño es de la captura, que la hacen esos ocho scrapers. Eso es lo
que incumple los invariantes 1 y 5 del contrato y por eso cuentan como pendientes.

## 7. Trampas ya pisadas (no repetirlas)

1. **Cambios ajenos permanentes en SwissJob.** `CLAUDE.md`, varios documentos
   históricos y de unificación y algunas auditorías sin seguimiento están
   modificados o sin versionar desde antes de DT-144. **No añadirlos, no
   restaurarlos, no reformatearlos**: seleccionar fichero a fichero en cada
   commit.
2. **Los bucles de espera con `pgrep` se auto-detectan** (ya está en
   DEUDA_TECNICA §4.3): el propio `bash -c` contiene el patrón y el bucle no
   termina nunca. Esperar por el LOG (`until grep -qE "[0-9]+ (passed|failed)"`).
3. **No editar el árbol mientras corre una suite** que lo monta: el resultado
   deja de ser atribuible a un candidato. Congelar, lanzar, esperar.
4. **Dos pytest contra la MISMA base se vacían las tablas entre sí.** La suite
   del BFF (`swissjobhunter_test`) y la del núcleo (otra base) SÍ pueden ir a la
   vez; dos del BFF no.
5. **`JOB_TAGS` no es una taxonomía de tecnologías**, es de idiomas y docencia
   (99 términos). Una oferta de «Python Developer» no produce tags. Comprobarlo
   ejecutando antes de escribir una expectativa.
6. **ReactPortfolio se formatea con `black`** (línea 100), no con `ruff format`,
   y su CI no linta. Hay deriva preexistente de black en tres ficheros: no
   mezclarla, pero no añadir deriva nueva (comparar contra `HEAD`).
7. **El hook de pre-commit de ReactPortfolio** sólo bloquea `.env` y ejecuta
   black. Si se omite, ejecutar sus dos comprobaciones a mano.
8. **Un `<meta content="…">` no pasa por la pila de elementos.** Leer un
   `itemprop` sólo en etiquetas de apertura no encuentra nada: va en `on_void`.
9. **`int()` lanza con más de 4.300 dígitos.** Un contador patológico que pasa un
   `[0-9]+` revienta igual; hay que capturarlo.

## 8. Los cuatro residuos estructurales (CERRADOS el 2026-10-15)

No eran de ningún portal y los cuatro bloqueaban la definición de terminado.
Evidencia completa en su checkpoint de DEUDA_TECNICA; resumen:

1. `jobicy` y `remoteok` ya no registran caché ni actualizador al importarse, y
   el censo vacío de los dos registros está fijado por una prueba.
2. Los barridos de fidelidad G7/G8 saltaban con «got empty parameter set» al
   quedarse sin cachés locales. Ahora saltan **con motivo**, y su garantía se
   prueba donde el dato vive: un agregado hostil del núcleo (número no finito,
   surrogate, NUL también como CLAVE de contador) contra TODAS las rutas
   públicas de las 18 fuentes del adaptador.
3. La costura ya no construye un catálogo Core + local: sin fuente local propia
   devuelve `CoreCatalog()` directamente. El compuesto se queda SÓLO como vía de
   rollback y su borrado pertenece al corte final.
4. **Core tiene su propio portero de bloqueo** (`core0055`): `BLOCK_STATUSES =
   {403, 429, 503}` —la lista exacta del legacy—, tres bloqueos seguidos apagan
   el scope, sellan `disabled_by_blocks_at` y gritan; una cosecha COMPLETA borra
   el contador; re-encender es manual a propósito; y el censo de salud publica
   `apagados_por_bloqueo` en cada pasada para que el apagado no se lea como
   silencio. La clasificación recorre la cadena de excepciones, así que no hubo
   que tocar ningún adaptador.

**Para quien migre un scraper a partir de ahora:** ya existe el kill-switch, así
que un portal que empiece a bloquear apaga su scope en tres barridos. Si tu
adaptador traga el error del transporte sin dejar rastro (`raise X` sin cadena),
el clasificador no lo verá: deja que `__context__` o `__cause__` lleve el
`httpx.HTTPStatusError`.

## 9. Lo que NO se ha hecho a propósito

- No se ha creado **ningún scope productivo** ni se ha habilitado ninguna
  cosecha nueva: todos los provisionadores nacen deshabilitados.
- No se ha desplegado nada. El corte en producción va **agrupado** al final y
  exige la aprobación explícita del propietario.
- Los dos `main` no se han tocado y se revalidan tras cada push.
- Los productores locales migrados siguen en el árbol, desconectados, como vía
  de rollback: revertir el commit del portal los reconecta.
