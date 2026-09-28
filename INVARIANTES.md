# Invariantes de SwissJobHunter — contrato ejecutable

Este fichero es el **contrato** que ejecuta `docs/AUDITORIA_INSTRUMENTADA.md`.
Cada fila afirma algo y dice con qué comando se comprueba. Redactar esta tabla
exige juicio y es un acto puntual; **ejecutarla no exige ninguno**, y ahí está la
frontera que permite que la auditoría la corra un solo modelo de principio a fin.

**Regla de oro del proyecto**: un documento puede afirmar por escrito una garantía
que el código no da. Aquí no se afirma nada que no tenga un comando al lado.

Línea base de la última verificación completa: **`fbab90e`**, 2026-09-27 (informe
`AUDITORIA-2026-09-27.md`). Contrato corregido el 2026-09-28 con lo que esa corrida
destapó: dos esperados mal escritos, dos controles negativos que no rompían y una
afirmación sin prueba.

---

## Cómo se lee cada fila

- **Comando**: se ejecuta tal cual. El veredicto sale de su **código de salida**.
- **Control negativo**: qué hay que romper para que el check se ponga rojo. Si no
  se ejecuta, la fila vale `CHECK_ROTO`, no `CUMPLE`.
- Los comandos del núcleo necesitan el perfil de desarrollo (`-f docker-compose.yml
  -f docker-compose.dev.yml`) o probarían el código de la IMAGEN, no el del árbol.

---

## A · Calidad del código

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-A1 | El linter sale limpio en los 820 ficheros del repo | `docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo backend ruff check --no-cache .` | código **0** | Añadir un import sin usar → 1 (*visto en vivo: un `timezone` huérfano dio F401*) |
| I-A2 | El formato está aplicado | `… ruff format --check --no-cache .` | código **0** | Desalinear un `=` en cualquier `.py` → 1 |
| I-A3 | La versión del linter es la que fija el CI | `docker compose exec -T backend ruff --version` | `ruff 0.15.14` | Contenedor desechable con otra versión: `docker compose run --rm --no-deps backend sh -c "pip install -q ruff==0.15.2 && ruff --version"` → `ruff 0.15.2` ≠ esperado. (El caso real fue una imagen construida antes del pin con 0.16.8: **48 errores que el CI no ve**.) |
| I-A4 | El linter del frontend sale limpio | `cd frontend && npx eslint src --max-warnings=0` | código **0** | Fichero con `const sinUsar = 1` en `src/` → 1. **El `cd frontend` es parte del comando**: desde la raíz `npx` resuelve otro eslint (10.x) y falla con «No files matching src» (salida 2), que no es rojo sino un check mal lanzado |

> **I-A3 no es burocracia.** Es el único check que impide perder media hora
> persiguiendo errores fantasma. Se comprueba ANTES de creerse un rojo masivo.

## B · Suites de prueba

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-B1 | La suite del BFF está en verde | `docker compose exec -T backend python -m pytest tests/ -q` | código **0** y **4 xfailed**. Recuento: **2631 passed** con core dev accesible y `CORE_CONSUMER_KEY` en el entorno; **2628 passed, 3 skipped** sin ellos (los 3 de `test_aseam_e2e.py` llevan `skipif` por ambos) | Una aserción rota en cualquier fichero → 1. *Ojo: `--timeout=N` NO existe (ver I-E2)* |
| I-B2 | La suite del núcleo está en verde | `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate python -m pytest jobhunt_core/tests -q` | **1817 passed**, 1 skipped | Cualquier fallo real |
| I-B3 | La suite del frontend está en verde | `cd frontend && npx vitest run` | **54 passed**, 11 ficheros | Cualquier fallo real |
| I-B4 | El frontend compila | `cd frontend && npx vite build --outDir <temporal> --emptyOutDir` | código **0** | Un import a un fichero borrado → 1 |

> **No lances dos pytest a la vez contra la MISMA base**: el teardown hace
> `TRUNCATE … CASCADE` de `swissjobhunter_test`. La del núcleo usa otra base
> (`jobhunt_suite_<uuid>`), así que esas dos **sí** pueden ir en paralelo.
>
> **Árbol congelado** mientras corren (mecánica 3.6): editar un fichero a mitad
> invalida el resultado, y no avisa.

## C · Despliegue e identidad de la release

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-C1 | Los 3 servicios del núcleo corren la imagen a la que resuelve el compose, `/v1/ready` dice `ready` y la release es nombrable | `python3 scripts/check_core_release.py` | código **0** | **Ejecutado en vivo el 2026-09-27**: con la base en `core0052` y los contenedores en la imagen anterior devolvió 1 y nombró los tres servicios y el 503 de `/v1/ready` |
| I-C2 | Ningún puerto se publica fuera de loopback, ninguna contraseña va como defecto, y redis exige contraseña | `python3 scripts/check_compose_exposure.py` | código **0** | Poner `0.0.0.0` en un puerto del compose → 1 |
| I-C3 | Las migraciones del BFF están al día | `docker compose exec -T backend alembic current` | `f4c9b2e7a108 (head)` | Crear una revisión y no aplicarla → deja de decir `(head)` |
| I-C4 | Las migraciones del núcleo están al día | `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate python -m jobhunt_core.migrate` | «al día (head)», hoy `core0052` | Igual que I-C3 |

> **I-C1 encontró deriva real dos veces**: la primera en producción (A19-21, el
> compose apuntaba a un build de 19 días antes), la segunda en local el mismo día en
> que se escribió esta tabla. Es el check con mejor historial del proyecto.

## D · Seguridad

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-D1 | El token de acceso no viaja en la URL del SSE | `cd frontend && npx vitest run src/config/sse-ticket.test.js` | **3 passed** | **Ejecutado el 2026-09-27**: al reponer `?token=` en `useCvAnalysis` suspendió 2 de 3 casos |
| I-D2 | El stream rechaza el token por query y acepta el vale | `GET /api/v1/notifications/stream?token=x` y `?ticket=x` | **422** y **no-422** | Volver a ACEPTAR el parámetro: `ticket: str \| None = Query(None), token: str \| None = Query(None)` en `routers/notifications.py` → `?token=` deja de dar 422 (da 503, como `?ticket=`). Renombrar `ticket`→`token` también pone rojo, pero por `NameError`, no por semántica |
| I-D3 | Un refresh se canjea UNA vez y reutilizarlo tumba la familia | `docker compose exec -T backend python -m pytest tests/test_refresh_rotation.py -q` | **9 passed** | Quitar **las dos** marcas al rotar (`fila.replaced_by = nuevo_jti` **y** `fila.revoked_at = ahora` en `token_store.py`) → `1 failed`. Quitar sólo `replaced_by` NO rompe nada: la detección mira cualquiera de las dos |
| I-D4 | Cada consumer del núcleo tiene UNA sola credencial viva | `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate python -m scripts.rotate_credential listar portfolio` (y `swissjob-shadow`) | **una línea** por consumer | `emitir portfolio` → dos líneas; restaurar con `cerrar portfolio <key_id nuevo>`. **Sólo válido partiendo de una viva**: con más de una, `emitir` se niega («rotación a medias»), que es exactamente el estado NO_CUMPLE. Este check es por ENTORNO: la base local tenía 3 perpetuas hasta el 2026-09-28 aunque en producción se habían revocado el 25 |
| I-D5 | Toda credencial nueva nace con caducidad | `… run --rm core-migrate python -m pytest jobhunt_core/tests/test_credential_rotation.py -q` | **4 passed** | Volver a `expires_at=None` por defecto → falla «volvió a emitirse una credencial perpetua» |

## E · Documentación que promete cosas

Los checks de esta sección **ejecutan lo que la documentación dice**. No lo leen.

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-E1 | Ninguna referencia apunta a la ubicación retirada de `Public` | `grep -rE "/home/lothar/Public/(PLAN_UNIFICACION\|CONTRATOS_FASE\|ESTADO_Y_HOJA\|DEUDA_TECNICA)" --include=*.md --include=*.py .` | **sin resultados** | Escribir una ruta vieja en cualquier documento → aparece |
| I-E2 | `CLAUDE.md` no vuelve a documentar `pytest --timeout` | `grep -c "timeout=30" CLAUDE.md` | **0** ocurrencias como comando | El complemento **nunca estuvo instalado**: `git log -S pytest-timeout -- backend/requirements.txt` no devuelve nada |
| I-E3 | Los comandos de recuperación de `docs/ARCHIVO_HISTORICO.md` funcionan | Ejecutar los tres literalmente | los tres con salida legible | Uno apuntaba a un repositorio retirado y devolvía `not a git repository` |
| I-E4 | Los sha256 que declara la evidencia de la Fase D casan con sus ficheros | Comparar los 3 hashes de `RESULTADO.md` con `sha256sum` | los 3 iguales | Alterar un byte de cualquiera de los tres |

## F · Rendimiento (invariantes de coste, no de latencia)

La latencia depende de la máquina y **no se puede fijar como invariante** (ver
A19-25: el contrato se cumple en hardware holgado y no en el NAS). Lo que sí se
puede fijar es que no vuelvan los patrones que costaban caro.

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-F1 | El camino de respuesta NO detecta idioma | `docker compose exec -T backend python -m pytest tests/test_language_store.py -q` | **20 passed** | La prueba instala un detector que **lanza**: devolver la detección al router hace explotar la suite en vez de ponerla lenta otra vez |
| I-F2 | El idioma viaja de la canónica al DTO | `… run --rm core-migrate python -m pytest jobhunt_core/tests/test_vacancy_language.py -q` | todas en verde | Perder el campo en cualquiera de las tres capas |
| I-F3 | La caché del recorrido se invalida entre procesos | `docker compose exec -T backend python -m pytest tests/test_cache_bus.py -q` | todas en verde | Quitar `await publicar(pid)` de `services/matching/feedback.py` → falla el caso de cableado; quitar `clear_feed_cache` de `cache_bus.escuchar` → falla el de mecanismo. Hasta el 2026-09-28 este invariante apuntaba a `test_feed_budget_y_degradacion.py`, que **no prueba esto** (presupuesto, recorte y degradación escolar): quitar la publicación dejaba 4 verdes |
| I-F4 | Las cadencias del beat tienen suelo de 60 s | `… run --rm core-migrate python -m pytest jobhunt_core/tests/test_config_bounds.py -q` | **31 passed** (el 49 anterior era la suma con `test_vacancy_language.py`, 18) | Quitar `ge=60` de `CORE_SHADOW_PROJECT_EVERY_S` → `4 failed` |

## G · Operación (desde el 2026-09-28)

| ID | Afirmación | Comando | Esperado | Control negativo |
|----|------------|---------|----------|------------------|
| I-G1 | La topología versionada del NAS no lleva secretos | `python3 scripts/check_no_secrets.py deploy/nas` | código **0** | Una contraseña literal de 28 caracteres en una copia → 1 (ejecutado el 2026-09-28) |
| I-G2 | `.env.prod.example` cubre todos los campos de `Settings` | `docker compose exec -T backend python -m pytest tests/test_env_example_covers_settings.py -q` | **4 passed** | Borrar una línea del ejemplo → falla «faltan en .env.prod.example» |
| I-G3 | El arranque avisa si los productores legacy quedan sin restringir | `docker compose exec -T backend python -m pytest tests/test_startup_guards.py -q` | **4 passed** | Devolver `False` fijo en `_legacy_producers_unrestricted` → 1 failed |
| I-G4 | El supervisor del NAS está vivo y no ve nada caído | `ssh nas "head -1 /share/Public/swissjob/supervisor/estado; $D inspect swissjob-supervisor --format '{{.State.Status}} {{.HostConfig.RestartPolicy.Name}}'"` | `ok` · `running always` | `docker exec -e EXTRA_ESPERADOS=x swissjob-supervisor sh /supervisor_contenedores.sh` → `mal` y línea en el log (ejecutado el 2026-09-28) |
| I-G5 | Los servicios del NAS corren con límite de memoria | `ssh nas "$D inspect swissjob-backend swissjob-postgres swissjob-core-worker-r5 --format '{{.Name}} {{.HostConfig.Memory}}'"` | ninguno en **0** | `docker update --memory 0` a uno → aparece 0 |
| I-G6 | El job `core-test` del CI es ejecutable en limpio | los pasos del job en un venv nuevo contra `docker/postgres-core` (ver commit 3443127) | `migrate` al día y subconjunto en verde | Un rango en `jobhunt_core/requirements.txt` → SQLAlchemy 2.1 → `No module named 'psycopg'` (ocurrió) |

---

## PARA_EL_CONTRATO — invariantes que faltan

Entradas que **todavía no se pueden expresar** como comando + valor esperado. Cada
una es una tarea, no una excusa:

- **Nada avisa de un contenedor PARADO** (A19-17). El 2026-09-23 se pararon los 42
  del NAS y `unless-stopped` no levantó ninguno. Hace falta un supervisor antes de
  que esto pueda ser un invariante.
- **La carga del NAS** (A19-25). Medido el 2026-09-27: 0 % de inactividad, 16 de 23
  contenedores con comprobación de salud y ocho cada 5-10 s. No hay umbral honesto
  que fijar mientras la causa no se resuelva.
- **Reconciliación del registro de deuda**. Cuatro entradas describen un estado que
  no es el real; una de ellas (A19-12) tenía razón y destapó un fallo vivo. Un
  invariante que compare cada entrada cerrada con su prueba sería el check más
  valioso que falta.
