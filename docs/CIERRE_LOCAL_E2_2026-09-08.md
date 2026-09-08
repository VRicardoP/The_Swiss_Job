# E.2 — adaptadores de documentos y recuperación local, 2026-09-08

**Tramo local implementado; no es un cutover ni el cierre de E/F. NAS intacto.**
Commits: SwissJob **e1df10f** (runtime) y **65b7481** (adaptador/ensayo);
Portfolio **178c7ab** (adaptador y regresiones).

Continúa [E.1](CIERRE_LOCAL_E1_2026-09-08.md) y su
[contrato/secuencia](FASE_E_DOCUMENTOS_2026-09-08.md).

## 1. Runtime local recuperado, sin desplegar E.1 por accidente

La API/worker/capture locales ejecutaban b98833d/core0035 frente a una BD core0041.
Se detuvo la captura local para el respaldo; el worker ya estaba detenido. El
broker conserva su volumen. No se cambiaron políticas, consumidores ni destinos.

Respaldo completo pg_dump -Fc, privado; copia restaurada con
`pg_restore --exit-on-error --single-transaction`, salida 0. Artefactos retenidos:

- `/tmp/swissjob-local-align-20260908.Ner4sM/core-local.dump` (directorio 0700,
  archivo 0600), SHA256
  `747f4d68f5ad8e4aa22320d2b9c22ef280ecd9b021d739041bd5e985e5ddcd7c`.
- Copia local `jobhunt_align_20260908`, en `swissjob-postgres`. Contiene datos
  privados del respaldo: no publicar ni usar como fixture de una suite.

Paridad previa: **185 constraints, 146 índices, 34 triggers, 0 sin validar**.
El hash de las definiciones NO fue idéntico: cuatro CHECK normalizaron casts de
arrays varchar→text al restaurarse (change_log.op, judgments.source,
routing.mode, dedup_pairs.verdict). Se recrearon las expresiones originales en
tablas TEMP LIKE y se comparó pg_get_constraintdef: **4/4 equivalentes**; transacción
revertida. No se ocultó el desacuerdo del hash ni se suprimieron errores del restore.

Sobre esa copia se ejecutó core0041→core0042→core0041→core0042 con la imagen
`swissjob-core:d908ea2`; después se aplicó core0042 a la base local. **core0043 no
se aplicó al runtime local**: solo a bases desechables de pruebas.

`docker-compose.core-local.yml` fija la misma imagen en API/worker/capture/migrate
y publica la API exclusivamente en loopback. Usar junto al compose base, con
`--no-build --pull never`; NO ejecutar `--build` con ese override ni arrancar el
runtime desde el compose base solo, que apunta a otra etiqueta mutable.

Comprobación final: API ready/authoritative=true, release d908ea2, Alembic core0042;
API y capture healthy, worker responde pong. El primer project drenó **3.000
cambios en 6 lotes**, 2.749 upserts y 247 cierres, en 10,625 s. Staging pendiente
posterior: **0**. Esto cierra la incompatibilidad de runtime, no certifica por sí
solo la completitud histórica del corpus.

### Incidencia local identificada: cuatro ofertas sin imagen previa

El drenaje emitió cuatro alertas por UPDATE con TOAST omitido y sin contenido
previo completo. El proyector aplicó su contrato conservador (solo last_seen),
sin inventar una revisión. Consulta de solo lectura posterior:

| Clave legacy | Descripción (caracteres) | Listings core legacy:arbeitnow |
| --- | ---: | ---: |
| c2af7abb3404eb7e98c4fae2e6ee787b | 5480 | 0 |
| 5776bdb24d7f7fa9a1a41838b26c700e | 2187 | 0 |
| 7dc642fc8dc9092706b50b3d898de2b1 | 2749 | 0 |
| 2cc2ac502bc792d98b46950806d4ff3b | 4669 | 0 |

Las cuatro siguen activas y no marcadas duplicate_of en legacy; hay dos cambios
stageados por clave, ninguno pendiente. **Deuda abierta, no reparada en este tramo.**
No se alteró legacy para provocar CDC, no se inventaron LSN ni se reinició el slot.
Solución siguiente: reconciliación dirigida de esas claves desde snapshot completo,
ensayada primero en copia, usando el sink existente y una frontera verificable
frente al CDC. Comprobar después contenido/hash/estado y segundo pase idempotente.
No sustituir esto por un bootstrap global o un UPDATE artificial de last_seen.

## 2. Adaptadores HTTP E.2, todavía sin conectar al routing vivo

SwissJob `backend/services/documents/core_client.py` reutiliza el vínculo por
usuario y el contrato /v1. `CoreDocuments()` sin BD sigue Unsupported y sin red;
el resolver conserva esa forma hasta el corte. El adaptador vinculado valida
ownership, referencia de oferta, contenido/hash/metadata, tipos y fechas; recorre
la paginación con límite total de tiempo y falla sin devolver un listado parcial.
DELETE hace GET→ETag→If-Match; 404 no se confunde con 403/errores.

La costura canary ahora **solo permite escrituras locales**, incluso si se le
inyecta un primario real: create/delete no prueban el core y luego otro escritor.
Port/docstrings describen la API existente y su vinculación aún no habilitada,
en vez de afirmar que /v1 carece de documentos.

Portfolio `services/documents_core.py` exige un vínculo explícito verificado
`(profile_id, owner_user_id)`; el CORE_PROFILE_ID global no autoriza a todos los
usuarios. Conserva application_id, user_id entero, idioma/modelo/tiempo y contenido
JSON exacto para la descarga PDF posterior. Reutiliza el cliente y presupuesto HTTP
existentes. No cambia config/main/routers ni el trabajo ajeno de integration_inbox.

Ambos POST exigen **UUID estable de operación aportado por el solicitante**:
reintento ambiguo usa la misma key, nueva generación usa otra aunque el contenido
coincida. No se genera una key por contenido o por intento de transporte. El
frontend/orquestación todavía deben transportar y conservar ese UUID; el adaptador
no soluciona la atomicidad CV+carta ni garantiza replay pasado el TTL de 24 h del core.

## 3. Verificaciones y límites de la evidencia

- SwissJob, regresión previa: 14 fallos antes de implementar adaptador/canary.
- Dos regresiones adicionales fallaron antes de corregir: version=True y recibo
  con contexto de oferta incorrecto. Ahora ambas fallan cerrado correctamente.
- Bloque SwissJob documentos/contrato: **52 passed**, 13,05 s.
- Suite BFF SwissJob completa: **2308 passed, 4 xfailed**, 275,27 s. Avisos de
  deprecación y corrutinas no awaited en mocks de cosecha preexistentes; no se
  presentan como arreglados por este cambio de documentos.
- Portfolio, antes de implementar: import del módulo inexistente falla.
- Bloque Portfolio documentos/generación: **29 passed**, 4,62 s.
- Ensayo de adaptadores con API real: **2 passed**, 5,98 s. PostgreSQL/auth reales,
  servidor HTTP loopback efímero, clientes en procesos separados; sin importar
  jobhunt_core en ningún BFF. POST/replay/generación nueva de contenido idéntico,
  21 documentos con cursor real, DELETE/ETag y tenant ajeno. Los primeros intentos
  fallaron por configuración/dependencias del arnés, no se cuentan como verdes.
- Suite general Portfolio: **1876 passed, 1 skipped, 3 deselected**, 162,61 s.
  Solo se excluyeron los tres ensayos reales de `test_migrations.py`; no se modificó
  su Alembic ni se certifica aquí la migración inbox ajena. Tras aplicar el formato
  exigido por el hook a los dos archivos propios: bloque **29 passed**, 4,88 s.
- Última repetición del arnés con guarda de BD desechable: **2 passed**, 6,84 s.
  Advertencia inocua de caché pytest read-only; el proceso y su limpieza terminaron.

Ensayo reproducible: `scripts/test_document_adapter_contract.py`. Ejecutarlo en
core-migrate montando core actual, ese script, `/bff` y `/portfolio` read-only,
con `python -m pytest -p jobhunt_core.tests.conftest
scripts/test_document_adapter_contract.py`. **El plugin es obligatorio**: crea y
elimina su propia base; una guarda impide sembrar sin ese aislamiento. Las
dependencias BFF faltantes pueden suplementarse desde el venv ya instalado con
DOCUMENT_TEST_EXTRA_PACKAGES (sin instalaciones ni alterar imágenes). El script
no carga los .env privados ni usa credenciales reales. Comando ejecutado desde SwissJob:

```sh
docker compose -f docker-compose.yml -f docker-compose.core-local.yml run --rm --no-deps \
  -v /home/lothar/Public/SwissJob/jobhunt_core:/app/jobhunt_core:ro \
  -v /home/lothar/Public/SwissJob/scripts/test_document_adapter_contract.py:/app/scripts/test_document_adapter_contract.py:ro \
  -v /home/lothar/Public/SwissJob/backend:/bff:ro \
  -v /home/lothar/Public/ReactPortfolio/backend:/portfolio:ro \
  -e DOCUMENT_TEST_EXTRA_PACKAGES=/portfolio/venv/lib/python3.12/site-packages \
  core-migrate python -m pytest -p jobhunt_core.tests.conftest \
  scripts/test_document_adapter_contract.py -q --tb=short --show-capture=no
```

No equivale a probar una
imagen E.2 final ni a ensayar el router de generación/PDF después del flip.

No cambió código core ni Alembic en E.2. La suite core completa 1096/1 es evidencia
**anterior de E.1**, no una pasada repetida aquí. Todas las suites se ejecutaron en
serie. Se preservaron los cambios ajenos de ambos repositorios.

## 4. Siguiente tramo bloqueante, sin un nuevo GO ficticio

1. Preparar identidad de operación y recuperabilidad CV+carta, descarga JSON/PDF
   con goldens, invalidación de caché y limpieza/retención. Generación fuera de locks.
2. Completar/ensayar inbox document.changed en ambos consumers (duplicados, ACK
   perdido, baja). Coordinar el trabajo ajeno de Portfolio, no sobrescribirlo.
3. Migrador explícito de documentos históricos que preserve UUID, created_at,
   ownership, metadata y SHA256, con manifiesto durable y rollback de escrituras
   posteriores. Freeze/drenaje específico de todos sus escritores, no doble escritura.
4. Restore fiel + fixtures Portfolio NO vacías; ida/vuelta y reanudación. Luego
   build limpio con core0043, scopes, canary del flujo completo BFF y flip por consumer.
5. Colegios como corte separado; F solo después de verificar sustituto de cada
   fuente/tarea legacy. No parar fuentes escolares aún exclusivas del legacy.

La consulta/aplicación de ofertas existente no se cambia por este tramo. E/F y el
GO de calidad son estados distintos: no se tocaron métricas, holdout ni modelos;
no se inició una racha. YAGNI: helpers/patrones existentes, sin dependencias nuevas,
sin motor PDF nuevo ni infraestructura de almacenamiento especulativa.
