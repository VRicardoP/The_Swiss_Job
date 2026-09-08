# E.3 — alta atómica de CV+carta, 2026-09-08

**Código local, aún sin activar el escritor core de documentos.**
Commits: SwissJob **bb3e578**, Portfolio **81fdf09**. No es el
cutover de E, ni el cierre de F, ni un GO de calidad. No se modificó el NAS;
el runtime local conserva d908ea2/core0042. Continúa
[E.2](CIERRE_LOCAL_E2_2026-09-08.md) y el
[contrato de documentos](FASE_E_DOCUMENTOS_2026-09-08.md).

## Bloqueo que resuelve

Portfolio confirma CV y carta en una única transacción local. Sustituirla por
dos POST HTTP independientes perdería esa propiedad: el segundo podría fallar
después de confirmar el primero. Compensar con DELETE tampoco basta si se pierde
la respuesta o falla la compensación.

Se añade **POST `/v1/profiles/{pid}/documents/batch`**, reutilizando la misma tabla,
autenticación, ownership, idempotencia, outbox y orden de locks que E.1. No se
añaden dependencias ni migraciones, ni se reescribe core0043.

## Contrato implementado

- Body `{items: [...]}`: uno o dos documentos terminados, como máximo uno por tipo
  (`cv`, `cover_letter`). Cada elemento usa DocumentCreateDTO, con sus límites de
  bytes, UTF-8, NUL, números finitos y tamaño efectivo JSONB. El POST individual
  llama ahora a la misma validación: no hay dos fronteras que diverjan.
- Un perfil/consumer por operación. `documents:write` e Idempotency-Key obligatorios.
  401 sin credencial, 403 sin scope, 404 ajeno/inexistente, 400 cuerpo/key inválidos.
- **Una sola transacción**: documentos + eventos individuales document.changed +
  recibo. Si falla una referencia de oferta o el segundo outbox, no queda ninguno.
- Locks como antes: perfil → recibo/documentos. Un reintento concurrente espera
  y recibe los mismos IDs. El lote no llama al LLM ni genera PDF bajo esos locks.
- 201 con `{items: [DocumentDTO, ...]}` en el orden recibido. El recibo persistido
  contiene exclusivamente `{ids: [...]}`; no duplica texto del CV.
- Mismo UUID de operación y mismo body → replay; mismo UUID/body distinto → 409;
  UUID nuevo/content idéntico → una generación nueva, legítima.
- Si se borró un miembro, el replay del lote devuelve **404 completo**: no resucita
  el documento ni devuelve una pareja parcial. No hay un endpoint de borrado de
  lote implícito; las bajas siguen siendo por documento.
- El erase del perfil incluye la nueva ruta de recibos, con UUID canónico y
  filtro de consumer, además de documentos y outbox.

## Adaptador Portfolio

`CoreDocuments.create_batch(user_id, application_id, documents, operation_id=UUID)`
usa un solo POST. Exige vínculo verificado de usuario/perfil y valida cardinalidad,
tipos, campos de cada recibo, IDs distintos, orden, owner, application_id, hash y
ausencia de cursor. Un recibo parcial/incompatible nunca se devuelve como éxito.
No hay fallback de escritura local ni dos llamadas al POST individual.

**No está cableado al router de generación.** Su integración exige conservar
durablemente el UUID de operación **y el body exacto ya generado** para reintentar
tras caída del BFF. Volver a invocar el LLM puede generar otro body y no es un retry.
La semántica del recibo sigue limitada a su retención existente (24 h); el corte
histórico necesita su manifiesto permanente, no este TTL como garantía eterna.

La versión mínima de rollback también debe incluir este contrato una vez usado:
tener el mismo head core0043 NO basta. Una imagen E.1 anterior no sabe atender
/batch ni purgar sus recibos al borrar un perfil. Antes del corte se exige una
imagen de reversión compatible con lote/erase, o reconciliar primero el estado
posterior; nunca volver a E.1 solo porque Alembic siga en core0043.

## Evidencia ejecutada

- Antes de implementar: **12 pruebas core fallaron** (ruta aún inexistente) y
  **13 pruebas Portfolio fallaron** (método aún inexistente).
- API/lifecycle/identity/upgrade/downgrade de documentos: **35 passed**, 12,05 s.
- Lote y carrera concurrente, después: **13 passed**, 4,78 s. Barreras: primera
  solicitud dentro del handler, segunda llega a ownership mientras la primera
  conserva la transacción; resultado = 2 documentos, 2 eventos y 1 recibo.
- Portfolio documentos/generación/lote: **42 passed**, 4,94 s.
- Clientes de ambos BFF contra HTTP y PostgreSQL reales: **2 passed**, 8,45 s.
  El cliente Portfolio añade lote/replay/listado/borrado parcial/replay rechazado;
  el flujo individual SwissJob sigue intacto. Se usa el arnés y comando de E.2.
- Suite core completa: **1109 passed, 1 skipped**, 842,80 s. El único skip es
  `test_release_desconocida_es_error_en_operacion`; ejecutado aparte con
  RELEASE_SHA=unknown: **1 passed**, 0,99 s. Avisos de deprecación y caché pytest
  read-only; no hubo fallos.
- Suite general Portfolio: **1889 passed, 1 skipped, 3 deselected**, 159,28 s.
  Solo se excluyen `test_upgrade_head_from_empty_database`,
  `test_roundtrip_base_to_head` y
  `test_el_esquema_de_alembic_no_deriva_de_los_modelos` (migraciones no modificadas).
  Formato black y diff --check limpios en los archivos propios.
- Todas las suites se ejecutaron en serie y con almacenamiento de pruebas,
  nunca contra datos NAS. La suite SwissJob BFF de E.2 no se reejecutó: su código
  no cambió; sí pasó su cliente contra la API real en el arnés compartido.

Las suites se ejecutaron sobre los árboles de trabajo, con los cambios ajenos
preexistentes preservados y excluidos de nuestros commits. Antes de desplegar se
exige build limpio y ensayo de esa imagen; estos resultados no son ese despliegue.

El código de la API, incluido erase, sí cambió: no se presenta la suite core
anterior como evidencia suficiente. Las pruebas específicas no certifican el
flujo de generación+PDF tras el cutover ni una imagen nueva desplegada.

## Siguiente paso y deuda que sigue abierta

1. Orquestación recuperable de generación: UUID de operación desde el solicitante,
   snapshot de insumos y persistencia del resultado antes de enviar el lote.
   Repetir el transporte no repite el LLM. No abrir transacciones de escritura
   durante llamadas de IA. Probar caída antes/después del commit y del ACK.
2. Caché SwissJob: no debe ser autoridad sobre existencia del documento. Hoy la
   lectura de caché devuelve el DTO sin comprobar almacenamiento, y DELETE no
   invalida la entrada. La solución debe cubrir también la carrera delete↔set,
   no solo añadir un DEL, y cambios de los insumos del CV/oferta. Testear primero
   generar→borrar→generar y la publicación tardía de una caché borrada.
3. Goldens de contenido JSON/PDF y descarga, idiomas, CV solo/carta sola/ambos;
   limpieza por retención y recepción idempotente de document.changed en los dos
   consumidores. Preservar el trabajo ajeno de integration_inbox en Portfolio.
4. Migración histórica explícita con UUID/fecha/ownership/metadata/hash y manifiesto
   durable; ensayo sobre restore fiel, ida/vuelta con documentos no vacíos y nuevas
   escrituras. Después build, scopes, freeze/drenaje específico y flip por consumer.
5. Las cuatro ofertas locales sin imagen previa siguen pendientes de reconciliación
   dirigida (claves y evidencia en E.2). No se fabricaron eventos/LSN ni se tocó legacy.
6. Colegios y retirada legacy mantienen sus propios entregables. Ninguno se cierra
   con este endpoint ni con un número de pruebas verdes.

YAGNI: se eligió la transacción del almacén existente; no una saga distribuida,
cola, motor PDF o sistema de reintentos nuevo. Se conservaron los archivos ajenos
sin incluirlos en este tramo.
