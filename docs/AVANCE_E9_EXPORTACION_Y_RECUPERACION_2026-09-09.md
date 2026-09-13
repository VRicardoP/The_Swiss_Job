# E.9 — portabilidad documental, freeze de propietario y recuperación

**Rectificación de alcance (2026-09-13):** este informe conserva la foto de E.9.
La propuesta de una API nueva de exportación integral fue descartada después por
el propietario; sus menciones como pendiente en este historial no son instrucciones
vigentes. Se mantienen las exportaciones existentes y la corrección de erase/copias.

## Estado real

Continuación LOCAL de E.8. No se ha desplegado ninguna imagen, cambiado routing,
migrado datos ni detenido un servicio del NAS. No cierra E/F ni certifica calidad.
El backend local usa recarga automática y el esquema e6fa04182c39 ya aplicado;
este delta no cambia esquema ni requiere una migración nueva.

## Corregido

1. **Exportación documental.** `/api/v1/profile/export` conserva cuenta/perfil y
   añade documentos de la autoridad activa, copias locales retenidas tras un
   futuro flip y operaciones de entrega (incluido el output preparado pendiente).
   Solo filas del propietario autenticado; no se exportan hashes de autenticación.
   Páginas core de 20, detección de IDs/cursores repetidos y fallo cerrado ante
   error/timeout: nunca se devuelve una exportación parcial como éxito.
   Límite síncrono: 2000 elementos por colección, 32 MiB de datos acumulados,
   60 s para el tramo HTTP. Excederlos devuelve 503, no trunca. El error obliga
   a usar un procedimiento de exportación de operador antes de superar esas cotas.
   Ninguna llamada HTTP mantiene una transacción local abierta.
2. **Freeze también en el borrado de cuenta.** `DOCUMENT_WRITES_FROZEN=true`
   bloquea `DELETE /profile/delete-all` antes de autenticación y se revalida
   dentro del endpoint. La exportación sigue disponible. El borrado adquiere
   primero el lock raíz del usuario, coherente con generación/entrega.
3. **Recibo de borrado veraz.** Confirma `erasure_scope=local_live_database`.
   Si existe vínculo core, devuelve `core_erasure=pending_confirmation`;
   sin vínculo, `not_linked` (no prueba ausencia de cualquier copia remota).
   `backup_erasure=not_confirmed` siempre: un DELETE SQL no sanea backups.
   Se cambia deliberadamente el mensaje previo de borrado permanente total;
   la regresión anterior se adapta a este contrato más preciso, no se elimina.
4. **Recibo de documento ya eliminado.** Si una operación estaba confirmada y
   su GET devuelve 404, la interfaz cierra la operación, elimina su estado de
   recuperación y permite otra acción explícita. No regenera automáticamente.
   401/403/503 no se confunden con borrado: conservan el error/reintento.

## Evidencia

- Tres reproducciones del propietario FALLARON antes del fix por sus aserciones:
  freeze devolvía 401 en vez de 503; exportación sin documentos; borrado vinculado
  sin señal de confirmación remota pendiente. Después, **24 pruebas dirigidas**
  de propietario/exportación/concurrencia/workflow/perfil pasaron.
- Primera suite completa: **2351 passed, 4 xfailed, 5 warnings**, 283,81 s.
  Los warnings son deprecación 422 y corrutinas de fixtures legacy; no se ocultan.
- Contrato HTTP/auth/PostgreSQL real: **5 passed**, 15,61 s. Nuevo caso SwissJob:
  21 documentos core + copia local + output preparado, con igualdad de IDs y
  contenido. Reutiliza servidor/credenciales y base desechable del arnés existente.
  Un warning de caché pytest sin permiso de escritura, no de lógica.
- Frontend: **14 pruebas pasadas**, lint y build correctos. PDF sigue diferido
  (~976 kB sin comprimir); no se añade dependencia. Los tests del nuevo helper
  fallaban inicialmente por helper ausente: NO se presentan como una mordida
  funcional del bug anterior. La comprobación del comportamiento final incluye
  navegador visible con frontera API simulada y datos sintéticos.
- Dos recorridos Playwright correctos: recibo borrado → operación liberada sin
  tercer POST; y recarga/mismo UUID → biblioteca → PDF → móvil → delete 204.
  Cero `pageerror`. No es prueba contra NAS ni certificación visual exhaustiva.

Pruebas nuevas en `backend/tests/test_document_owner_contract.py`,
`backend/tests/test_document_export_core.py`,
`frontend/src/config/documentRecovery.test.js`; contrato real ampliado en
`scripts/test_document_adapter_contract.py` con cliente SwissJob aislado.

## Qué NO cierra este delta

- La respuesta declara `export_scope=account, profile, documents and document
  deliveries`: NO inventaría todas las candidaturas, búsquedas, feedback,
  notificaciones, eventos o backups. La exportación integral sigue pendiente.
- La colección core se lee por páginas, no mediante snapshot distribuido.
  Es portabilidad de usuario, no backup transaccional para cutover. El corte
  requiere freeze/drenaje y manifiesto independiente.
- `pending_confirmation` no es un workflow nuevo de erase: falta su seguimiento
  durable/confirmación remota, junto con retención y recuperación de backups.
- Inbox SwissJob, soporte verificado `document.changed` en ambos destinos,
  migrador histórico UUID/fechas/hash, rollback después de nuevas escrituras y
  canary/flip documental permanecen pendientes.
- Colegios, productores sustitutos, retirada legacy y carga representativa NAS
  no se certifican con esta suite. La corrección EOF WordPress cefb70a sigue local.

## Comprobación operativa sin mutaciones

`docker ps` por SSH confirmó Portfolio e7-2eb5f31 en marcha y API/capture core R5,
backend/frontend SwissJob con health correcto cuando disponen de esa sonda.
Workers en ejecución no equivale a prueba funcional de todas sus tareas.
No se imprimieron entornos ni credenciales. No se modificó el NAS.

## Siguiente secuencia

1. Receptor durable de eventos documentales y pruebas de replay/ACK perdido en
   ambos BFF; no confundir insertar outbox con consumirlo.
2. Completar export/erase integral y seguimiento de confirmación, contemplando
   journal, retención y backups. Una confirmación parcial no autoriza el flip.
3. Migrador histórico explícito + copia fiel + rollback con escrituras nuevas;
   preservar UUID, fecha, idioma, contexto y hashes sin restaurar el corpus global.
4. Release limpia, freeze/drenaje, canary y único escritor por perfil/capacidad.
5. Colegios y productores uno a uno, luego rendimiento/recuperación representativos
   y retirada legacy. El NO-GO de calidad exige examen independiente nuevo.

YAGNI: se reutilizan el puerto documental, cursores, validación Pydantic y cliente
HTTP existentes; sin cola nueva, dependencias o importaciones BFF→jobhunt_core.

## Verificación final

Segunda pasada completa, con el contrato final de borrado y formato aplicado:
**2351 passed, 4 xfailed, 5 warnings**, 283,29 s. Mismos avisos conocidos.
Ruff de los archivos Python afectados correcto; lint/build frontend correctos.
Repetición final del arnés HTTP/auth/PG: **5 passed**, 16,06 s, mismo warning
de caché pytest no escribible. Montar TODO `scripts/` en `/app/scripts:ro`:
el arnés ahora usa `swiss_document_export_contract_client.py` como subproceso.
Todas las suites se ejecutaron en serie, sin compartir simultáneamente la BD.

Runtime local: health healthy, writes enabled, OpenAPI con los nuevos campos de
exportación/borrado confirmado. Previsualización temporal 4178 detenida; servidor
ajeno 8080 intacto. No se eliminó ningún archivo de usuario ni se tocó el cambio
ajeno en `school_job_monitor_architecture.md`. Core/Portfolio no cambiaron código.
