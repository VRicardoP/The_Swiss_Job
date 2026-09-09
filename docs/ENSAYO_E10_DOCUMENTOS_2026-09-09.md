# E.10 — ensayo documental y cierre de privacidad de recibos

Fecha: 2026-09-09. Trabajo en curso; este acta no certifica los cinco trabajos ni
un flip. Producción no se modificó durante las verificaciones descritas aquí.

## Copias autorizadas y restauración

Tres dumps privados, con owners/ACL, restaurados en bases nuevas de un contenedor
local **sin red ni puertos**. `pg_restore --exit-on-error --single-transaction`:
ningún error suprimido. Evidencia privada: `/tmp/unification-e10.t4tnEzYK` (0700).
No contiene un compromiso de retención permanente: sigue pendiente el ciclo de
vida de backups y la certificación de borrado de copias.

| Origen | SHA-256 del dump |
| --- | --- |
| Portfolio/proyecto | `1936180fd619ecc4b4492d78b7920ad0a06857db8d3e2eb653edb564b3cbca98` |
| SwissJob BFF | `91243c977860317b30c9bc812a6f9fdca5a2289230bc21413cf4ea665911de51` |
| Core R5 vivo | `09885ade07b1b1bdb45816fc1c2c74879fa6ec08f49037631022df542d1d40e6` |

La base NAS llamada `swissjobhunter_r5_rehearsal` es **el core vivo**, no una base
desechable. Solo se hizo dump y consultas de lectura en ella.

Paridad: Portfolio 27 constraints/50 índices/0 triggers; BFF 170/141/13; core
183/145/35 (incluye los esquemas public/jobhunt presentes en cada copia). Owners,
ACL y relaciones también cotejados. Core: igualdad exacta. Portfolio/BFF: una y
cuatro diferencias de representación de CHECK (`varchar[]::text[]` frente a
casts por elemento); el SQL ORIGINAL se recompiló en tablas temporales y produjo
exactamente el CHECK restaurado. Ninguna otra diferencia admitida.

Upgrade ensayado: core0042→core0043; BFF c4d8e2f60a17→d5e9f3071b28→
e6fa04182c39→f70b15293d40. Sin reescribir migraciones publicadas.

## Migración y reversión

Herramienta distribuida dentro de la imagen: `python -m jobhunt_core.document_cutover`.
Procedimiento y precondiciones: [runbook](RUNBOOK_DOCUMENTOS_E10.md).

- SwissJob: dos documentos históricos reales; UUID, fecha, idioma, contenido y
  snapshots conservados. Huella histórica
  `43a732866823fa5ece08dda571ad374fc40dc97bea8065371f055bea0e49ab95`.
- Portfolio: historia real vacía; además, una fila sintética no vacía sobre la
  copia de su esquema real, para ejercitar enum, snapshot, tipos y FK. No se
  presenta esa fila como dato del usuario.
- Snapshot sellado antes de importar; importación repetida sin nuevas filas ni
  eventos; vuelta histórica con identidad material exacta.
- Tras el flip **en las copias**, baja y alta de documento; la vuelta reproduce
  la colección vigente, no resucita la baja ni pierde el alta. Segunda vuelta
  idempotente. Copias core retenidas e inertes, sin borrar corpus compartido.
- El ensayo CLI usa una sonda de freeze controlada, sin afirmar que ensaya el
  drenaje de procesos NAS. El recorrido HTTP separado usa los routers reales.

El resumen final del ensayo encontró la protección de UID del directorio privado
(proceso root frente a directorio del usuario); el ensayo había terminado. Sus
resultados se reconstruyeron desde sellos y recibos ya persistidos, sin repetir
escrituras. Se conservaron las restricciones de permisos, no se relajaron.

## Inbox y recibos

SwissJob: inbox autenticado y durable de metadata, sin CV/payload personal,
validación de binding y propietario bajo lock. Portfolio: validación equivalente
de eventos documentales conservando su inbox existente. Recepción por HTTP real:
concurrencia, pérdida de ACK, conflicto de event_id y reinicio sin duplicar recibos.
Rutas: SwissJob `/api/v1/integration/events`; Portfolio `/api/v1/integration/inbox`.

Se reprodujo un P1 preexistente: tras erase, repetir PUT del perfil devolvía el CV
desde idempotency_records (200), incluso con UUID mayúsculo/compacto. Corrección
común: propietario vivo bloqueado **antes** del recibo y de cualquier replay;
recibos nuevos con subject explícito; erase elimina recibos del propietario y
formatos históricos identificables sin afectar a otro perfil. Orden único:
perfil→recibo→hijos. Espera de raíz acotada con el timeout existente.

Los DELETE antiguos ya completados y sin metadata de propietario conservan solo
`{status:204, body:null}`. Su replay vacío mantiene compatibilidad hasta purga;
no se atribuye a un propietario por adivinación. Los nuevos DELETE sí quedan
vinculados y se borran con el perfil. Esto no certifica erase de backups.

## Verificación y pendientes operativos

- Antes del fix: las tres reproducciones de CV fallaron con `(1 recibo, 200)`.
- Después: once casos de CV/durables/compatibilidad/aislamiento pasaron; añadido
  además interleaving de replay concurrente con erase observado con
  `pg_blocking_pids` (el replay espera y acaba 404, no devuelve el CV).
- CLI contra PostgreSQL desechable: 2 pasados; HTTP inbox real: 2 pasados;
  adaptadores HTTP: 5 pasados; BFF completo: 2361 pasados/4 xfailed.
- Portfolio: corregida clasificación del inbox autenticado en inventario público;
  185 pruebas dirigidas pasadas. No se eliminó la cota: hay pruebas autenticadas
  de tamaño/desconexión y anónima de respuesta sin esperar el cuerpo.
- La primera combinación de las pruebas nuevas dejaba vacantes sintéticas y
  afectaba a paginación posterior. Fixture corregida con limpieza por URL propia,
  sin cambiar el ranking ni relajar la aserción del test afectado.
- Core completo antes del último borde de UUID: 1129 pasados/1 omitido, 871,08 s.
  Después, 59 pruebas de privacidad/API pasadas, 31,27 s; cubren también llaves
  y URN en recibos legados, no solo las tres primeras formas.
- Portfolio final: 1975 pasados/1 omitido, 220,50 s; commit 715c347, con el fix
  WordPress cefb70a incluido en el árbol de la imagen. Dependencias iguales al NAS.
- El preflight detectó otro borde antes de activar HTTP: matching emite
  aggregate_id como hash de 64 caracteres, no UUID. El sobre genérico ahora
  admite el identificador opaco varchar(100) del core; documentos conservan la
  comprobación UUID exacta. Regresión: rechazo UUID reproducido; 11 pruebas del
  inbox pasadas después de corregirlo.
- Release/canary y las suites finales sobre el commit se registrarán tras
  ejecutarse. No se confunde una imagen construida con un despliegue confirmado.

Los cinco contratos siguen separados: documentos; colegios; productores/retirada;
export/erase/backups; rendimiento representativo. No apagar ningún productor o
colegio para simular cierre. Calidad continúa sin examen independiente válido;
este trabajo no autoriza promoción de un ranker ni cambia umbrales.
