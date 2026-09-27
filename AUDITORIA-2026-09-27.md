# Auditoría instrumentada — SwissJobHunter — 2026-09-27

Protocolo: `docs/AUDITORIA_INSTRUMENTADA.md`. Contrato: `INVARIANTES.md` (25 filas).
Ejecutada de principio a fin por un solo modelo (Fable 5.1), sin delegar.

## Línea base

| | |
|---|---|
| SHA | `fbab90e` (al inicio, tras cada control negativo y al terminar: idéntico) |
| Árbol | `git status --short` vacío al inicio y al terminar; vacío tras restaurar cada control negativo |
| docker | Docker version 29.8.1, build 4a63305 |
| docker compose | v5.5.1 |
| ruff (contenedor `backend`) | 0.15.14 |
| ruff (host, sólo para el CN de I-A3) | 0.15.2 |
| python (contenedor `backend`) | 3.12.14 |
| pytest (contenedor `backend`) | 8.4.2 |
| alembic (contenedor `backend`) | 1.20.0 |
| python (host) | 3.12.3 |
| node / npm | v24.11.0 / 11.6.1 |
| eslint | v9.39.3 (dentro de `frontend/`; desde la raíz `npx` resolvió 10.11.0 y no encontró `src` — ver I-A4) |
| vitest | 4.0.18 |
| vite | 7.3.1 |

Las dos suites largas (I-B1, I-B2) se ejecutaron con el árbol congelado; el SHA y el
estado del árbol se anotaron antes de lanzarlas y al terminar, y no cambiaron. La
primera corrida de ambas se perdió al cerrarse la sesión anterior sin registro de
fin; se relanzaron íntegras. Ninguna otra comprobación se ejecutó contra
`swissjobhunter_test` mientras corría I-B1.

## Tabla

Columna «Salida» = código de salida del comando positivo. «CN» = control negativo:
qué se rompió, qué salió, y si se restauró (todos restaurados; árbol limpio verificado).

| ID | Afirmación | Comando | Esperado | Obtenido | Salida | Control negativo | Veredicto |
|----|------------|---------|----------|----------|--------|------------------|-----------|
| I-A1 | Linter limpio en el repo | `docker compose run --rm --no-deps -v "$PWD:/repo" -w /repo backend ruff check --no-cache .` | código 0 | `All checks passed!` | 0 | Fichero `backend/_cn_ruff_tmp.py` con `import os` sin usar → salida **1** (`1 fixable`). Borrado. | CUMPLE |
| I-A2 | Formato aplicado | `… ruff format --check --no-cache .` | código 0 | `819 files already formatted` | 0 | Fichero `x  =  1` → salida **1** (`1 file would be reformatted`). Borrado. | CUMPLE |
| I-A3 | Versión del linter = la del CI | `docker compose exec -T backend ruff --version` | `ruff 0.15.14` | `ruff 0.15.14` | 0 | Misma comparación contra el ruff del host (`0.15.2`) → `comparacion=distinta` (el check sabe distinguir versiones). | CUMPLE |
| I-A4 | Linter del frontend limpio | `cd frontend && npx eslint src --max-warnings=0` | código 0 | (sin avisos) | 0 | `src/_cn_eslint_tmp.js` con `const sinUsar = 1` → salida **1**. Borrado. *Nota: lanzado desde la raíz del repo el comando resuelve otro eslint (10.11.0) y falla con «No files matching src» (salida 2): el `cd frontend` del contrato no es opcional.* | CUMPLE |
| I-B1 | Suite del BFF en verde | `docker compose exec -T backend python -m pytest tests/ -q` | **2622 passed, 3 skipped, 4 xfailed** | **2625 passed, 0 skipped, 4 xfailed** en 1092,64 s | 0 | Aserción rota en `tests/test_refresh_rotation.py` (`== 200` → `== 999`) → `1 failed, 8 passed`, salida **1**. Restaurado. | NO_CUMPLE |
| I-B2 | Suite del núcleo en verde | `docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate python -m pytest jobhunt_core/tests -q` | 1817 passed, 1 skipped | 1817 passed, 1 skipped en 1779,03 s | 0 | Aserción rota en `test_config_bounds.py` (`== 60` → `== 61`) → `6 failed, 25 passed`, salida **1**. Restaurado. | CUMPLE |
| I-B3 | Suite del frontend en verde | `cd frontend && npx vitest run` | 54 passed, 11 ficheros | 54 passed, 11 ficheros | 0 | Aserción rota en `sse-ticket.test.js` (`toBeGreaterThan(20)` → `999999`) → `1 failed, 53 passed`, salida **1**. Restaurado. | CUMPLE |
| I-B4 | El frontend compila | `cd frontend && npx vite build --outDir <tmp> --emptyOutDir` | código 0 | `✓ built in 10.35s` | 0 | `import "./_no_existe_tmp"` añadido a `src/main.jsx` → salida **1**. Restaurado. | CUMPLE |
| I-C1 | 3 servicios del núcleo sobre la imagen del compose, `/v1/ready` `ready`, release nombrable | `python3 scripts/check_core_release.py` | código 0 | `Core coherente: release 2b59a92, alembic core0052, authoritative, y los 3 servicios sobre la imagen del compose` | 0 | `docker compose stop core-api` → salida **1** (`no se pudo consultar …/v1/ready: Connection refused`). `start` y verde a los 10 s. | CUMPLE |
| I-C2 | Sin puertos fuera de loopback, sin contraseñas por defecto, redis con contraseña | `python3 scripts/check_compose_exposure.py` | código 0 | `C7 OK: docker-compose.yml, 6 puertos publicados, ninguno a la LAN` | 0 | Copia del compose con `${HOST_BIND_IP:-127.0.0.1}` → `0.0.0.0`, pasada como argumento → salida **1** (nombra `postgres: publica 5435 en 0.0.0.0`, `redis: 6380`). Sin tocar el compose real. | CUMPLE |
| I-C3 | Migraciones del BFF al día | `docker compose exec -T backend alembic current` | `f4c9b2e7a108 (head)` | `f4c9b2e7a108 (head)` | 0 | Revisión temporal `cn0000000001` (down_revision `f4c9b2e7a108`) creada y no aplicada → salida `f4c9b2e7a108` **sin `(head)`**. Borrada. | CUMPLE |
| I-C4 | Migraciones del núcleo al día | `docker compose … run --rm core-migrate python -m jobhunt_core.migrate` | «al día (head)», `core0052` | `Migraciones del core al día (head)` | 0 | Revisión temporal `core9999` cuyo `upgrade()` lanza → `RuntimeError: control negativo`, salida **1**. Borrada; al relanzar, «al día (head)». | CUMPLE |
| I-D1 | El token no viaja en la URL del SSE | `cd frontend && npx vitest run src/config/sse-ticket.test.js` | 3 passed | 3 passed | 0 | `?ticket=` → `?token=` en `useCvAnalysis.js` → `2 failed, 1 passed`, salida **1**. Restaurado con `git checkout`. | CUMPLE |
| I-D2 | El stream rechaza `?token=` y acepta `?ticket=` | `GET /api/v1/notifications/stream?token=x` y `?ticket=x` (cliente ASGI in-process) | 422 y no-422 | `token->422 ticket->503` | 0 | Parámetro `ticket:` renombrado a `token:` en `routers/notifications.py` → `NameError: name 'ticket' is not defined`, salida **1**. Restaurado. | CUMPLE |
| I-D3 | Un refresh se canjea UNA vez; reutilizarlo tumba la familia | `docker compose exec -T backend python -m pytest tests/test_refresh_rotation.py -q` | 9 passed | 9 passed | 0 | Según contrato: eliminada la línea `fila.replaced_by = nuevo_jti` de `token_store.py` → **9 passed, salida 0. El check NO se puso rojo.** Restaurado. | CHECK_ROTO |
| I-D4 | Cada consumer del núcleo tiene UNA credencial viva | `docker compose … run --rm core-migrate python -m scripts.rotate_credential listar portfolio` | una línea | **3 líneas** (`de77a6248e8417d9`, `1c21205695e86317`, `1db3159cf164fe98`, las tres «caduca NUNCA (anterior a T9)») | 0 | No ejecutable: `emitir portfolio` se niega («ya tiene 3 credenciales vivas: hay una rotación a medias») — el estado actual es ya el que el CN debía provocar. `listar` antes y después: 3 y 3. | NO_CUMPLE |
| I-D5 | Toda credencial nueva nace con caducidad | `… run --rm core-migrate python -m pytest jobhunt_core/tests/test_credential_rotation.py -q` | 4 passed | 4 passed | 0 | Defecto de `expires_at` desactivado (`if expires_at is None` → `if False`) en `credentials.py` → `1 failed, 3 passed`, salida **1**. Restaurado. | CUMPLE |
| I-E1 | Ninguna referencia a la ubicación retirada de `Public` | `grep -rE "/home/lothar/Public/(PLAN_UNIFICACION\|CONTRATOS_FASE\|ESTADO_Y_HOJA\|DEUDA_TECNICA)" --include=*.md --include=*.py .` | sin resultados | sin resultados | 1 (grep: sin coincidencias) | `docs/_cn_tmp.md` con `/home/lothar/Public/DEUDA_TECNICA.md` → grep salida **0** (encontrado). Borrado. | CUMPLE |
| I-E2 | `CLAUDE.md` no documenta `pytest --timeout` | `grep -c "timeout=30" CLAUDE.md` | 0 | `0` | 1 (grep -c con 0 coincidencias) | Línea `pytest --timeout=30` añadida → `grep -c` = **1**. `git checkout`. Además `git log -S pytest-timeout -- backend/requirements.txt` → 0 commits. | CUMPLE |
| I-E3 | Los comandos de recuperación de `docs/ARCHIVO_HISTORICO.md` funcionan | Los tres, literales | los tres legibles | `git clone <bundle>` → 0; `git -C /tmp/public-hist show 2bcbcd91…:INFORME_SESION_2026-09-04_06.md` → primera línea `# Informe de sesión para análisis externo — 2026-09-04 → 2026-09-06`, 0; `git -C …/SwissJob show d9c8a0ae…:docs/CIERRE_LOCAL_E1_2026-09-08.md` → `# E.1 documentos — evidencia y pendientes, 2026-09-08`, 0 | 0, 0, 0 | `git show 0000…0000:docs/CIERRE_LOCAL_E1_2026-09-08.md` → salida **128**. | CUMPLE |
| I-E4 | Los sha256 de la Fase D casan con sus ficheros | `sha256sum -c` sobre los 3 hashes de `RESULTADO.md` (desde `docs/unificacion/FASE_D_MIGRACION_2026-09-04`) | 3 iguales | `plan_fase_d.json: OK`, `manifiesto_fase_d_real.json: OK`, `backup_fase_d.log: OK` | 0 | Copia del directorio con un byte añadido a `plan_fase_d.json` → `1 FAILED`, salida **1**. Copia en `/tmp`, original intacto. | CUMPLE |
| I-F1 | El camino de respuesta NO detecta idioma | `docker compose exec -T backend python -m pytest tests/test_language_store.py -q` | 20 passed | 20 passed | 0 | Llamada a `TranslationService._detect_language(original_title)` insertada en `_job_view` de `routers/match.py` → `6 failed, 14 passed`, salida **1**. Restaurado. | CUMPLE |
| I-F2 | El idioma viaja de la canónica al DTO | `… run --rm core-migrate python -m pytest jobhunt_core/tests/test_vacancy_language.py -q` | todas en verde | 18 passed | 0 | Campo `language` retirado de `VacancyDTO` en `api/schemas.py` → `2 failed, 16 passed`, salida **1**. Restaurado. | CUMPLE |
| I-F3 | La caché del recorrido se invalida entre procesos | `docker compose exec -T backend python -m pytest tests/test_feed_budget_y_degradacion.py -q` | 4 passed | 4 passed | 0 | Según contrato: eliminado `await publicar(pid)` de `services/matching/feedback.py` → **4 passed, salida 0. El check NO se puso rojo.** (Una aserción rota en el mismo fichero sí da `1 failed`, salida 1: el fichero puede fallar, pero no por esto.) Restaurado. | CHECK_ROTO |
| I-F4 | Las cadencias del beat tienen suelo de 60 s | `… run --rm core-migrate python -m pytest jobhunt_core/tests/test_config_bounds.py -q` | **49 passed** | **31 passed** | 0 | `ge=60` retirado de `CORE_SHADOW_PROJECT_EVERY_S` en `config.py` → `4 failed, 27 passed`, salida **1**. Restaurado. | NO_CUMPLE |

**Recuento**: 25 filas · CUMPLE 20 · NO_CUMPLE 3 (I-B1, I-D4, I-F4) · CHECK_ROTO 2 (I-D3, I-F3).
CHECK_ROTO = 8 %, por debajo del tercio que obliga a parar.

Filas anotadas como rojas en el contrato y reejecutadas: I-C1 (anotada con deriva
local el 2026-09-27) → hoy 0; I-D1 (anotada con el fallo de `useCvAnalysis`) → hoy
3 passed. Cambio de estado en ambas respecto a la anotación: ahora verdes.

## PARA_EL_CONTRATO

Lo que no se puede expresar como comando + valor esperado, o lo que el propio
contrato tiene mal escrito. Cada línea es una tarea sobre `INVARIANTES.md`, no
sobre el proyecto.

- **I-B1 · el «Esperado» fija recuentos que se mueven.** Tres pruebas que el
  contrato daba por saltadas (`3 skipped`) ahora corren y pasan (`2625 passed, 0
  skipped`). Qué tres son y por qué antes se saltaban no está en el contrato ni en
  este informe. El invariante necesita decidir si su esperado es «salida 0» o un
  recuento exacto que se actualiza con cada cambio de estado.
- **I-F4 · el «Esperado» es un error de transcripción.** 49 es la suma de
  `test_config_bounds.py` (31) y `test_vacancy_language.py` (18) ejecutados juntos
  al redactar el contrato. Solo, el fichero da 31. El invariante se cumple por
  código de salida y por control negativo; el número está mal.
- **I-D3 · el control negativo prescrito no rompe el check.** Quitar `replaced_by`
  al rotar deja los 9 casos verdes porque `rotar` también escribe `revoked_at`, y
  la detección de reutilización mira cualquiera de los dos. El CN tiene que romper
  ambas marcas, o el check tiene que cubrir cada una por separado.
- **I-F3 · el check apunta al vecino (mecánica 3.7 incumplida por el propio
  contrato).** `test_feed_budget_y_degradacion.py` prueba el presupuesto de tiempo,
  el recorte de descripciones y la degradación escolar. **No prueba la
  invalidación entre procesos.** Quitar la publicación por Redis deja los 4 casos
  verdes. La afirmación de I-F3 no tiene hoy ninguna prueba que la sostenga; hace
  falta una que instale dos procesos (o simule el segundo) y compruebe que el
  segundo deja de servir el recorrido viejo.
- **I-D4 · la base LOCAL conserva tres credenciales perpetuas de `portfolio`.** En
  producción se revocaron dos el 2026-09-25 (A19-28); en local no. El contrato no
  distingue entorno. Además, el CN prescrito («emitir una segunda») es inejecutable
  mientras haya más de una viva, porque la herramienta se niega: el CN sólo es
  válido partiendo de una única credencial.
- **I-A4 · el `cd frontend` es parte del comando, no un decorado.** Desde la raíz,
  `npx eslint` resolvió otra versión (10.11.0 en vez de 9.39.3) y no encontró
  `src`. El contrato debería decirlo explícitamente, como hace con el perfil de
  desarrollo del núcleo.
- **I-A3 · el CN es indirecto.** Comparar contra el ruff del host demuestra que la
  comparación distingue versiones, pero no reproduce el caso real (imagen con
  0.16.8). Reproducirlo exige reconstruir la imagen sin el pin: caro y con efectos.
  Se deja anotado como el único CN de la tabla que no rompe el objeto vigilado.
- **I-D2 · el CN rompe por `NameError`, no por semántica.** Renombrar el parámetro
  hace explotar el handler antes de llegar a la comprobación de `token`. El check se
  pone rojo, pero por un choque de nombres. Un CN mejor: añadir `token: str | None =
  Query(None)` y aceptarlo, sin quitar `ticket`.
- **Sin cambios respecto a la sesión anterior**: siguen pendientes las tres
  entradas ya listadas en el contrato (supervisor de contenedores parados, carga del
  NAS, reconciliación del registro de deuda). No se ejecutó nada sobre ellas.
