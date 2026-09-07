# Reconfirmación de cierres — 2026-09-07

## Veredicto: REQUEST CHANGES

Revisión de `c8c15f9..38c51ab`, HEAD verificado `38c51ab`. El cierre de las
reproducciones anteriores es real, pero **A y B2 siguen parcialmente abiertos**.
Hay tres problemas concretos, con cinco reproducciones nuevas. No es necesario
rehacer todo el proyecto ni suspender el servicio baseline por el defecto de CE.

Alcance: código local, PostgreSQL de tests y documentación. No se han ejecutado
órdenes en el NAS, cambiado políticas, desplegado ni abierto ningún examen.
Las cifras de producción y paridad son evidencia **aportada por el autor**,
recogida en `ESTADO_Y_HOJA_DE_RUTA.md §25`; no una comprobación live de esta revisión.

## Estado de los contratos

| Contrato | Resultado de esta revisión |
|---|---|
| A: presupuesto | PARCIAL: evita la segunda tanda de la reproducción anterior, pero la primera todavía puede exceder el límite. |
| A: publicación cache-only | CERRADO para el interleaving previo: editar el CV tras materializar no dispara inferencia nueva al publicar. |
| B1: fotografía vacía | CERRADO: una evaluación explícita con cero candidatos retira los punteros anteriores. |
| B2: ciclo completo de exclusiones | PARCIAL: altas/bajas secuenciales llegan; faltan invalidación y garantías de entrega/orden. |
| C: restricción sellada | CERRADO para el hallazgo previo: restricciones distintas no pasan con el mismo sello; la CLI deriva la lista del sello. También se incluyen las reglas por perfil. |
| D: recuperación delegada | CERRADO para el hallazgo previo: el CE ya no mantiene la señal del proyector permanentemente encendida. |
| Rollback de `dismissed_at` | CERRADO para el defecto descrito: la fecha ISO se convierte a `datetime`; la prueba con marca y notas preexistentes pasa. |

Un canary con reglas ya cargadas acredita que el filtro funciona sobre esa
fotografía. No prueba altas/bajas posteriores, pérdida del transporte, reordenación
de peticiones ni publicación concurrente. «n/a, sin CE activo» tampoco certifica
el presupuesto del CE.

## P1-1 — Las exclusiones cambian sin invalidar recuperación ni publicación

**Dimensión:** integridad, diseño transaccional y concurrencia.

**Localización:** `jobhunt_core/matching.py:1192` (`declare_profile_exclusions`),
llamado desde `jobhunt_core/api/v1.py:568`.

El nuevo escritor hace DELETE/INSERT y commit, pero no modifica la revisión del
perfil, la generación del corpus ni el estado de recuperación. `core0041` tampoco
instala un trigger de invalidación. El `FOR UPDATE` del endpoint serializa las
escrituras, pero no hace que una evaluación preparada previamente detecte que sus
candidatos ya no son válidos.

**Dos reproducciones con PostgreSQL real:**

1. Crear perfil y dos ofertas `python`, evaluarlo mediante la costura del proyector
   y comprobar que ya no tiene trabajo pendiente. Declarar la exclusión `python`
   usando el mismo orden de locks/commit del endpoint. Resultado: el feed sigue
   sirviendo **dos ofertas excluidas** y `_RECOVERY_NEEDED_SQL` devuelve **[]**.
   Sin otro cambio que rearme la recuperación, repetirla no arregla el feed.
2. Preparar los candidatos; en otra sesión confirmar esa exclusión; continuar la
   fase 3. Resultado: **`status=ok`, `evaluated=2`, `moved_current=True`, feed=2**,
   en vez de descartar por deriva. No fue necesario activar un cross-encoder.

Sondas: `docs/recheck_core_20260907.py::test_exclusion_change_must_rearm_recovery`
y `::test_exclusion_change_during_preparation_must_fence_publication`.
Ambas fallan en HEAD por las aserciones del defecto, no por preparación del entorno.

**Corrección mínima propuesta:** hacer que un cambio efectivo de reglas invalide,
en la misma transacción, el token que observan tanto la recuperación como la fase 3.
La opción pequeña es reutilizar la generación existente, cubriendo todos los
escritores mediante una migración **nueva**, sin reescribir core0041. Revisar el orden
de locks antes de añadir triggers; no incrementar en una declaración idéntica si se
puede evitar. Una versión por perfil también vale si se incorpora a AMBAS fronteras,
pero borrar solo `profile_recovery_state` no cierra la publicación concurrente.

Probar alta, baja, vaciado, reintento idéntico y cambio durante preparación. Definir
explícitamente el retraso aceptado hasta el nuevo feed; si se promete exclusión
inmediata, proteger además el feed servido durante ese intervalo.

## P1-2 — El envío declarativo puede perder una baja o restaurarla fuera de orden

**Dimensión:** integridad distribuida, gestión de errores y concurrencia.

**Localización:** `backend/services/exclusions_sync.py:49`, `:68`, `:75`;
`backend/routers/analytics.py:241`; receptor `jobhunt_core/api/v1.py:568`.

Enviar el conjunto completo hace el reemplazo declarativo, pero no garantiza
entrega ni orden. La mutación local se confirma ANTES de la llamada HTTP. No hay
versión del conjunto, precondición ni registro durable pendiente de entrega.
Los routers ignoran los estados `core_inaccesible`/`rechazado` del helper. La búsqueda
de todos sus llamadores confirma que solo se reenvía ante otra creación, aprobación
o baja del usuario: el comentario «el siguiente empujón reconcilia» no garantiza
que ese empujón llegue a existir.

**Reproducciones:**

1. A lee `{Director}` y su petición se retrasa antes de llegar al core. B confirma
   la baja local, envía `{}` y recibe éxito. A llega después. Ambos envíos devuelven
   `ok`, pero el core termina otra vez con `{Director}` y el BFF sin la regla.
   `docs/recheck_bff_20260907.py::test_delayed_snapshot_must_not_resurrect_deleted_filter`
   falla mostrando exactamente ese conjunto. Esta prueba usa el helper productivo,
   barreras deterministas y un doble de transporte que implementa el reemplazo del
   receptor; no simula una carrera real en el NAS.
2. Petición HTTP real al BFF de tests: DELETE de una regla de un usuario autenticado,
   con transporte al core que lanza `ConnectionError`. Se confirma `is_active=False`
   en PostgreSQL, se intenta un único envío de `{}`, y el endpoint responde **204**.
   No queda una entrega persistida para recuperarse tras el fallo.
   `docs/recheck_endpoint_20260907.py::test_delete_reports_success_despite_failed_projection`
   falla por ese 204. La prueba del helper que observa `core_inaccesible` pasa y
   sirve como control del fallo de transporte inyectado.

**Corrección mínima completa:** conservar una sola autoridad de las reglas, con
una revisión monotónica y entrega durable por perfil. Confirmar reglas + revisión
+ estado pendiente en la misma transacción local; reintentar con un mecanismo
persistente existente si lo hay. El core debe aplicar solo revisiones no obsoletas,
bajo su lock, y reconocer un reintento idéntico sin revertir una revisión posterior.
No mantener una transacción de BD abierta durante la red como sustituto de esa
garantía. Exponer pendiente/fallo al usuario y a la vigilancia.

Cambiar simplemente el 204 por 503 NO resuelve la pérdida del mensaje. Un contrato
asíncrono puede admitir éxito local, pero debe declarar ese significado y demostrar
persistencia/reentrega: en ese diseño se adaptará la última aserción del test HTTP
para exigir ese estado observable, no un código HTTP concreto.

Pruebas de cierre: caída antes de enviar, caída después de que el core confirme pero
antes del ACK, reinicio del BFF, A→B con llegada B→A, reintento idéntico y recuperación
SIN nueva edición del usuario. Después, comprobar alta y baja sobre el feed servido,
no únicamente sobre `profile_exclusions`.

## P1-3 — La primera tanda sigue sin coste admisible conocido

**Dimensión:** presupuesto operativo y eficiencia; bloquea activar CE, no el baseline.

**Localización:** `jobhunt_core/matching.py:1531`, `:1575–1597`.

`coste_doc` arranca a cero en CADA invocación. Mientras no haya una observación,
la guarda solo exige el margen de cierre y permite ejecutar 64 documentos completos
mediante `to_thread`. La estimación se obtiene después de consumir ese trabajo.
Comprobar al final que ya no cabe otra preparación devuelve `backlog` demasiado tarde.

**Reproducción de control de flujo con reloj simulado, NO benchmark del NAS:**

- presupuesto 1.200 s, preparación 135 s, primera tanda 64 documentos;
- coste inyectado 28 s/documento (no existe una cota que lo rechace);
- la tanda se admite porque `coste_doc == 0`;
- final a **1.927 s**, `scored=64`, `status=backlog`.

El tiempo ya supera los 1.200 s y el límite blando de 1.800 s. El bucle original de
la regresión oficial a 16 s/documento ahora pasa: eso verifica la segunda tanda,
no la seguridad de una primera tanda más lenta.

Sonda: `docs/recheck_core_20260907.py::test_first_cold_batch_must_respect_budget`.

**Corrección mínima propuesta:** primer micro-lote pequeño con cota conservadora
de admisión, comprobaciones entre micro-lotes y presupuesto de extremo a extremo
(preparación, inferencia, persistencia/cierre y publicación cache-only). No lanzar
64 documentos a ciegas para averiguar cuánto cuestan. La cancelación de un await de
`to_thread` no detiene su hilo: no presentarla como una interrupción efectiva de CPU.
Si el contrato exige un máximo estricto también ante bloqueo del backend, hace
falta una frontera de ejecución que se pueda detener y recoger realmente.

Antes de activar CE, medir en el J1800 primer lote frío, contención, pico, cambio de
CV y reinicio; demostrar salida con backlog y margen ANTES del soft limit, no subir
el límite para que pase. No afirmo que el NAS haya sufrido la duración simulada.

## Verificación ejecutada (siempre en serie)

- Cinco regresiones anteriores + importador: **12 passed**, 7,64 s.
- Cross-encoder + dev_eval + matching: **66 passed**, 46,25 s.
- Tests existentes de sincronización BFF: **3 passed**.
- Tests existentes del router analytics: **7 passed**.
- Sondas nuevas: **5 fallos esperados que reproducen los defectos** y un control
  que pasa (el helper devuelve `core_inaccesible`).
- Total de pruebas EXISTENTES reejecutadas: **88 passed**.
- No se reejecutó la suite completa: **1060/1060 ×2 es la declaración del autor**,
  no un resultado independiente de esta revisión. No se reabren como regresión
  nueva los tres tests temporales de heartbeat ya reconocidos.
- `git diff --check`: sin errores. No se cambió código funcional ni migraciones.
  Se preservó `school_job_monitor_architecture.md`, modificado por el usuario.

### Comandos reproducibles desde la raíz

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate \
  python -m pytest jobhunt_core/tests/test_review_evidence_20260907.py \
  jobhunt_core/tests/test_integration_import_swissjob.py -q --tb=short

docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate \
  python -m pytest jobhunt_core/tests/test_integration_cross_encoder.py \
  jobhunt_core/tests/test_integration_dev_eval.py \
  jobhunt_core/tests/test_integration_matching.py -q --tb=short

docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
  -v /home/lothar/Public/SwissJob/docs/recheck_core_20260907.py:/app/jobhunt_core/tests/test_external_recheck_core_20260907.py:ro \
  core-migrate python -m pytest jobhunt_core/tests/test_external_recheck_core_20260907.py -q --tb=short

docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps \
  -v /home/lothar/Public/SwissJob/docs/recheck_bff_20260907.py:/app/tests/test_external_recheck_bff_20260907.py:ro \
  --entrypoint python backend -m pytest tests/test_exclusions_sync.py \
  tests/test_external_recheck_bff_20260907.py -q --tb=short

docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps \
  -v /home/lothar/Public/SwissJob/docs/recheck_endpoint_20260907.py:/app/tests/test_external_recheck_endpoint_20260907.py:ro \
  --entrypoint python backend -m pytest tests/test_external_recheck_endpoint_20260907.py \
  tests/test_analytics_router.py -q --tb=short
```

Los montajes anidados de Docker dejan marcadores vacíos en los directorios de tests.
Los tres creados durante esta revisión se retiraron tras comprobar que medían cero
bytes. Las reproducciones completas permanecen en `docs/`, fuera de la suite normal.

## Prioridades para cerrar sin otra cadena de parches parciales

1. **Primero el contrato completo de exclusiones:** edición → entrega durable y
   ordenada → configuración vigente → invalidación → publicación → feed servido.
   Es lo que afecta a la baseline ya operativa. Resolver P1-1 y P1-2 como una unidad
   verificable, no dar por cerrado uno solo porque el PUT funciona en secuencia.
2. **Después el presupuesto CE**, manteniéndolo inactivo mientras falta su prueba
   de admisión fría. No requiere detener la unificación que sirve baseline.
3. Usar estas reproducciones como punto de partida y añadir los escenarios de
   reinicio/orden/ACK descritos. Probar que fallan en `38c51ab` por la causa esperada,
   y que pasan tras el fix sin suprimir guardas ni sustituir los interleavings.
4. Revisar conjuntamente escritores, tokens de invalidación, lectores, locks y
   reintentos antes de codificar; migraciones nuevas, sin modificar las publicadas.
   La guía YAGNI orienta la propuesta a reutilizar la generación y los mecanismos
   persistentes existentes, sin introducir otra plataforma de mensajería.
5. Reejecutar suites en serie y ensayo sobre copia con restore estricto y paridad
   estructural. Canary de alta/baja con comprobación eventual del feed, incluida
   caída breve del receptor. Separar corregido, ensayado, desplegado y confirmado.
6. Actualizar `ESTADO §25` y deuda: A/B2 parciales hasta estas pruebas; C/D/B1 y
   rollback conservan sus cierres verificados. E/F siguen como hitos separados.

Esto reduce el riesgo de regresión; ninguna técnica permite prometer cero errores
futuros. La salida no consiste en rebajar el criterio de aceptación ni en repetir
el examen consumido. El GO de calidad sigue necesitando un holdout independiente
nuevo; el número anterior no se usa como evidencia válida a favor ni en contra.
