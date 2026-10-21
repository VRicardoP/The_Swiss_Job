# DT-148 · Contexto de relevo

> **Qué es esto.** Lo primero que debe leer quien retome la auditoría de
> refactorización. No sustituye al plan: el inventario por ítem manda desde
> [DEUDA_TECNICA.md](DEUDA_TECNICA.md) §DT-148, las cotas aceptadas desde
> [COTAS_Y_DECISIONES.md](../COTAS_Y_DECISIONES.md) y la operación desde el
> `CLAUDE.md` de la raíz. Este fichero dice **qué está hecho, qué está vivo en
> producción, qué queda, en qué orden y qué trampas ya se pisaron**, para no
> reconstruirlo leyendo catorce actas.
>
> Última actualización: **2026-10-21**. Secciones A, B y E1 cerradas y
> desplegadas; A18-01 cerrada y desplegada; C, D, E2-E9 y F3 abiertas.

---

## 0. Índice

1. En una frase
2. Estado exacto (releases, ramas, migraciones, contadores)
3. Lo cerrado, con su despliegue
4. Lo abierto, en el orden recomendado y por qué ese orden
5. Decisiones que son del propietario, no del ejecutor
6. Comandos que funcionan de verdad (y los que parecen funcionar)
7. Trampas ya pisadas — **la sección más rentable de este fichero**
8. Disciplina de trabajo que esta auditoría ha ido fijando
9. Lo que el plan tenía mal, y cómo se detectó

---

## 1. En una frase

De los seis bloques del plan (A bugs, B código muerto, C duplicación, D
responsabilidad única, E consistencia, F documentación) están **cerrados A, B,
E1, F1, F2 y la reapertura A18-01**, todos verificados ejecutando contra
producción; quedan **C entera, D entera, E2-E9 y F3**, más dos decisiones del
propietario.

---

## 2. Estado exacto (2026-10-21)

| Qué | Valor |
|---|---|
| `main` (remoto) | `6efebe5` (PR #31) |
| Pendiente de fusionar | rama `dt148/a1801-receipt` — sólo documentación y las etiquetas de `deploy/nas` |
| Núcleo en producción | `swissjob-core:point5-6efebe5`, Alembic **`core0057`** |
| BFF y frontend | `swissjob-backend:point5-14438b7`, `swissjob-frontend:point5-14438b7` |
| Cabeza Alembic legacy | `a7c3d9e2f5b1` |
| Suite del núcleo | **1.786 passed · 1 skipped** (incluye `tools/core/tests`) |
| Suite del BFF | **669 passed** |
| Frontend | **63 passed**, eslint limpio |
| Contenedores del núcleo en producción | **TRES workers** (`-worker-r5`, `-harvest-worker-r5`, `-ops-worker-r5`) + `core-api` |

`/v1/ready` debe responder
`{"status":"ready","alembic":"core0057","release":"6efebe5","authoritative":true}`.
Si `authoritative` es `false`, alguien dejó puesto el override de desarrollo.

**El BFF y el frontend van en un SHA distinto del núcleo A PROPÓSITO**:
`6efebe5` solo tocó el núcleo (índice `core0057`). Igualar números moviendo
siete contenedores no mejora nada; lo que importa es que los cuatro del núcleo
compartan imagen, y eso lo asevera el propio `deploy_nas.sh`.

---

## 3. Lo cerrado, con su despliegue

| Bloque | PR | Release | Verificación que de verdad se hizo |
|---|---|---|---|
| Sección A (18 bugs) + F1 + F2 | #24 | `8ffb8b3` | alerta de profesor extremo a extremo contra el catálogo del núcleo |
| E1 + sección B (B1-B4, B7, B8) + B5 pasos 1-2 | #26 | `500d8d9` | 3 barridos disparados a mano escriben `invalid` en el cursor; `check_harvest_health` limpio; bundle servido sin las clases inertes |
| Sincronía de `deploy/nas` + limpieza de marcadores retirados del compose vivo | #27 | `500d8d9` | `env` dentro de los dos contenedores: 0 apariciones |
| B6 · `DROP shadow_inbox` | #28 | `4ffaf7e` | tabla fuera de `pg_tables`; `purge_retention` corre sin ella |
| A18-01 · la UI deja de ofrecer lo que el catálogo rechaza | #30 | `14438b7` | filtro de ubicación contra producción con recuentos reales |
| A18-01 · índice del filtro de ubicación (`core0057`) | #31 | `6efebe5` | 86,4 s → **2,0 s**, plan de `Seq Scan` a `Bitmap Index Scan` |

**2.811 líneas netas fuera** sólo en B1-B4. Las actas completas, con las
mediciones, están en `DEUDA_TECNICA.md` §DT-148.

---

## 4. Lo abierto, en el orden recomendado

### Orden, y el motivo del orden

1. **D1** — partir `jobhunt_core/matching.py` (2.684 LOC, diez
   responsabilidades, **el único fichero del repo con mantenibilidad C**).
   Va primero **porque C2-C5 tocan ficheros que D1 va a mover**: hacerlo al
   revés es trabajo doble. Riesgo medio: es el motor de matching y arrastra
   re-apuntar ~19 parches de monkeypatch. NO baja LOC — saca el fichero del
   grado C.
2. **D3** — `RawListingSink` (1.337 LOC, `handle` de 190 líneas, ocho
   responsabilidades). Independiente de D1.
3. **C1** — transporte HTTP de la cosecha copiado nueve veces (~410 LOC). El
   de mayor retorno inmediato y menor riesgo si se prefiere empezar por algo
   visible. **Leer antes la advertencia del propio punto**: la POLÍTICA de
   reintento es dato por proveedor y NO se unifica (nav no reintenta 429 a
   propósito, arbeitnow obedece `Retry-After`, jobgether no reintenta
   bloqueos), y la persona del `User-Agent` también es intencional.
4. **C2-C5** (núcleo), después de D1.
5. **C6-C7** (BFF), **C8 + D7** (frontend), **D2** (tras D1), **D4-D6**.
6. **E2-E9** — bajas. E7 es rendimiento menor; E6 es política, no refactor.
7. **B5 pasos 3-4** — reorganización pura: reagrupar los 18 `provision_*.py`
   en un subpaquete y mudar las sondas de un solo uso. **Se dejó fuera a
   propósito**: cambia rutas citadas en un runbook operativo
   (`DT144_RELEVO.md`) por cero efecto en producción.
8. **F3** — espera al corpus legacy. Ver §5.

### Lo abierto que un usuario nota hoy

- **Segunda mitad de A18-03**: `WatchlistPage` no consume `isError`, así que un
  fallo de red se lee como «aún no hay vacantes en la watchlist». Un error
  disfrazado de vacío. Es pequeño y visible: buen primer bocado.
- **Reescritura de la sección 3 de COTAS**, que quedó de F2.

### Fuera de DT-148

- **DT-146 (JSearch)**: ABIERTO, deshabilitado, con sus gates sin cumplir.
- **DT-147**: desplegado; sólo falta observar los primeros ciclos naturales.
- **Las cinco tablas `shadow_*` pequeñas** (1,5 MB) son candidatas a un punto
  propio. **`shadow_change_log` (216 MB) NO**: `erasure.py:180` la bloquea y
  machaca en `scrub_capture`, parte del camino de borrado GDPR. Agruparlas por
  el prefijo del nombre sería el error.

---

## 5. Decisiones que son del propietario, no del ejecutor

| Qué | Estado | Lo que falta |
|---|---|---|
| **F3** · retirar `legacy_shadow.py` | Decidido el 2026-10-21: **priman las reparaciones inmediatas sobre mantener las candidaturas**. NO autoriza borrar candidaturas | La fecha: ~2027-01-19. Al ejecutar, decidir si se cierran a mano las 4 encarnaciones con candidatura (recomendado: sí, un efecto provocado y anotado es mejor que uno fortuito) |
| **A18-01** · los ocho filtros | CERRADO retirándolos | — |
| **Suelo de 3 caracteres** en la búsqueda por subcadena (COTAS §6bis) | Medido, NO mitigado | Exigir 3 caracteres antes de disparar quitaría los casos de 20-35 s de `q` y `location`. Cambia el comportamiento de `q`, que lleva semanas en producción, y debe VERSE en la interfaz |
| **Rotación de secretos** | Pendiente de confirmar | `GROQ_API_KEY`, `POSTGRES_PASSWORD` y `SECRET_KEY` quedaron en la transcripción de la sesión del 2026-10-21 (un `diff` de un compose vivo; error del ejecutor) |
| **Dos variables residuales del NAS** | RETIRADAS el 2026-10-21 con autorización | — |

---

## 6. Comandos que funcionan de verdad

### Tests del núcleo — tres condiciones, y las tres hacen falta

```bash
# 1) El override de DESARROLLO monta el árbol de trabajo. Sin él se prueba la IMAGEN.
# 2) `pytest` NO viaja en la imagen del núcleo (P1 de DT-145). Hay que layerear:
#      printf 'FROM swissjob-core:dev\nUSER root\nRUN pip install --no-cache-dir pytest==8.4.2\nUSER core\n' > /tmp/Dockerfile.test
#      docker build -t swissjob-core:dt148-test -f /tmp/Dockerfile.test /tmp
#    y pasarla con un override de UNA línea:
#      services: {core-migrate: {image: swissjob-core:dt148-test}}
# 3) `test_deploy_order.py` lee composes de la RAÍZ, que el override dev no monta:
#    sin `-v "$PWD:/repo" -w /repo` sus 3 casos fallan por FileNotFoundError.
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f /ruta/test-image.yml \
  run --rm -v "$PWD:/repo" -w /repo core-migrate \
  python -m pytest jobhunt_core/tests tools/core/tests -q -p no:randomly
```

### Lint del repo completo

`ruff` desapareció de la imagen del backend reconstruida (vive en
`requirements-dev.txt`). Imagen auxiliar, una vez:

```bash
printf 'FROM python:3.12-slim\nRUN pip install --no-cache-dir ruff==0.15.14\n' > /tmp/Dockerfile.lint
docker build -t swissjob-lint:0.15.14 -f /tmp/Dockerfile.lint /tmp
docker run --rm -v "$PWD:/repo" -w /repo swissjob-lint:0.15.14 \
  sh -c "ruff check --no-cache . && ruff format --check --no-cache ."
```

### Los nueve checks del CI, en local, antes de pedir el merge

`main` está protegida: PR obligatorio + 9 checks, y `gh` NO está instalado ni
hay token. El merge lo hace el propietario, así que los nueve se comprueban
aquí primero para que no sea una apuesta:

```bash
python3 scripts/check_source_ownership.py                     # source-ownership
docker compose exec -T backend sh -c "ruff check --no-cache . && ruff format --check --no-cache ."
docker run --rm -v "$PWD:/repo" -w /repo swissjob-lint:0.15.14 \
  sh -c "ruff check --no-cache scripts docs tools && ruff format --check --no-cache scripts docs tools"
docker compose exec -T backend python -m pytest tests/ -q      # backend-test
docker compose exec -T backend alembic check
cd frontend && npm run lint && npx vitest run && cd ..         # frontend-lint/test
# core-lint + core-test: ver arriba
cp backend/.env.prod.example .env.prod   # compose-config necesita los .env
POSTGRES_PASSWORD=ci REDIS_PASSWORD=ci CORE_IMAGE_TAG=ci docker compose -f docker-compose.yml config -q
python3 scripts/check_no_secrets.py deploy/nas
python3 scripts/check_compose_exposure.py docker-compose.yml
POSTGRES_PASSWORD=ci REDIS_PASSWORD=ci docker compose build   # docker-build
```

### Despliegue

```bash
bash scripts/deploy_nas.sh core --dry-run     # fija la etiqueta sin tocar nada
bash scripts/deploy_nas.sh core|bff|frontend
```

Exige el SHA **exacto** de `github/main` — esa guarda es la que impide que
`RELEASE_SHA` mienta y **no se puentea**. Aplica las migraciones del núcleo en
contenedor desechable ANTES de recrear. Tras desplegar, sincronizar
`deploy/nas/` (T13 §7) y comparar **estructuralmente**, no por texto.

### Producción (NAS)

`docker` no está en el `PATH` del SSH:

```bash
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
ssh nas "$D ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'"
```

Base del núcleo: `swissjob-postgres`, base `swissjobhunter_r5_rehearsal`, rol
`jobhunt_core`, esquema `jobhunt`. **Un agregado sobre todo el corpus agota la
memoria compartida**: `SET max_parallel_workers_per_gather = 0` y muestrear.

---

## 7. Trampas ya pisadas

**Las de infraestructura.**

- **Dos `pytest` contra la misma base se pisan.** El teardown hace
  `TRUNCATE ... CASCADE` de `swissjobhunter_test`: deadlocks y falsos rojos. Se
  perdió media hora con 51 errores que parecían del cambio. Peor: una corrida
  lanzada con `&` dentro de un comando **sobrevive al comando**, el arnés no la
  sigue y queda una conexión `idle in transaction` bloqueando a las demás. Si
  aparecen deadlocks: `pg_stat_activity` sobre la base de test, matar lo
  huérfano, y si hace falta `docker compose restart backend`.
- **Un bind mount a un FICHERO que no existe en el host no falla al crear el
  contenedor: falla el SIGUIENTE arranque**, porque Docker fabrica la ruta
  ausente como DIRECTORIO. Tumbó `swissjob-postgres` con `Exited (127)` y dejó
  producción abajo 34 min. Ante un `Exited (127)`, `docker inspect --format
  '{{.State.Error}}'` ANTES que `docker logs`: los logs son los de la última
  ejecución SANA. Para arreglarlo: `rmdir` (nunca `rm -rf`, que sí podría
  destruir datos) desde un contenedor con privilegios, porque el usuario de SSH
  no puede escribir lo que creó el daemon.
- **`frontend/dist/assets` es de root** (build en contenedor del 28-09): `vite
  build` no puede vaciarlo. Compilar a otro `--outDir` para verificar.

**Las de este plan.**

- **El inventario buscaba consumidores EXCLUYENDO los tests**, y eso ocultó
  dependencias reales tres veces: el doble de LLM importaba
  `RERANK_SYSTEM_PROMPT` (B1), dos tests del núcleo dependen de `dev_eval` (B5)
  y el único 501 superviviente iba de polizón en una parametrización que se
  retiraba (A18-01). **Buscar consumidores incluyendo tests, y luego decidir.**
- **Un guard en rojo por documentar la realidad no es un guard mal puesto.**
  `check_source_ownership.py` prohíbe marcadores del stack retirado en
  `deploy/nas/swissjob.configured.yml`; anotar ahí la deriva del compose vivo lo
  puso en rojo. El guard tenía razón: el NAS arrastraba el marcador. Y busca el
  literal en TODO el fichero, **comentarios incluidos**.
- **La copia versionada de `deploy/nas` se queda atrás en silencio.** Llegó a ir
  dos despliegues por detrás. Compararla **estructuralmente** (YAML parseado,
  valores sensibles excluidos por NOMBRE DE CLAVE) y no por texto: el escritor
  de Container Station serializa duraciones en ns y `mem_limit` en bytes, así
  que un `diff` enseña ruido y esconde lo real.
- **No pidas un `diff` de un fichero que lleva secretos.** Pasó, y tres
  credenciales de producción acabaron en la transcripción.

---

## 8. Disciplina que esta auditoría ha ido fijando

- **Verifica EJECUTANDO, no leyendo.** Es la regla de oro del proyecto y en esta
  auditoría se cobró nueve puntos en la sección A y cuatro en la B donde el
  código desmentía al plan.
- **Control negativo en cada prueba nueva.** Rompe lo que acabas de arreglar y
  comprueba que la prueba se pone roja. Se hizo en E1, A18-01, el filtro de
  ubicación y la limpieza de B3; detectó un test que no medía lo que decía.
- **Fija la FORMA cuando el fallo sólo se ve con cronómetro.** El test del
  filtro de ubicación asevera que la condición usa `LIKE` y no `position()`:
  con `position()` el resultado sigue siendo correcto y tarda 86 s.
- **Mide antes de decidir si un filtro merece implementarse.** A18-01 parecía
  decisión de producto hasta medir la cobertura: 0-9 %. Implementarlos habría
  servido controles que esconden el 91-100 % del corpus — peor que un 501,
  porque un error se ve y un 0 % se cree.
- **Al cortar bloques de código o de tests, corta por RANGO DE LÍNEAS
  verificado, no por patrón de texto.** En esta sesión se comieron helpers dos
  veces por cortar «hasta el siguiente `def test_`».
- **Un test que nadie ejecuta no protege nada.** Cuatro tests de `scripts/`
  llevaban meses sin correr; entraron al CI en B5.

---

## 9. Lo que el plan tenía mal, y cómo se detectó

Para calibrar cuánta confianza merece el inventario que queda por ejecutar: en
lo ya hecho, el plan se equivocó **trece veces**, y ninguna se detectó leyendo.

| Punto | Lo que decía | Lo que era |
|---|---|---|
| E1 | 17 proveedores | **19**; y había que sacar `invalid` también de la expresión `error`, que el plan no mencionaba |
| B1 | sólo el módulo y dos tests | también el doble de LLM y el test de contrato en vivo |
| B3 | `clear_profile_cache` lo usan tres módulos | lo usa **uno**, y existía para invalidar la caché que se iba |
| B4 | `CORE_ENV` muerto | lo lee el validador del aislamiento en producción |
| B5 | sin consumidores | dos tests del núcleo dependen de `dev_eval` |
| B6 | va junto con F3 | no hay dependencia; F3 es otra cosa |
| F3 | vence ~enero 2027 | la condición **como está escrita no se cumple nunca** (4 vacantes con candidatura); y su consulta de verificación no corría |
| A18-01 | decisión de producto | la medición la decide |
| COTAS | «el `canton` nuevo sí lo lleva» | 8,5 % medido |

**Método que los encontró**: leer el código que el punto nombra, ejecutar la
búsqueda de consumidores **sin** excluir tests, y medir contra producción antes
de elegir entre dos salidas.
