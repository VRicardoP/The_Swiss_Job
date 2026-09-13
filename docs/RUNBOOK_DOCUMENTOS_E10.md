# Corte documental E.10 — procedimiento reversible

Este documento describe el procedimiento implementado; la evidencia del corte
13-09-2026 y su pendiente de canary real están en
[el acta E.14](DESPLIEGUE_E14_2026-09-13.md). No modifica catálogo/matching,
modelos ni el gate de calidad.

## Precondiciones

- Backup privado de ambas bases implicadas, restaurado con `--exit-on-error` y
  `--single-transaction`; comparar constraints, índices y triggers. Nunca ocultar
  stderr del restore. Ensayar también Portfolio con documentos no vacíos.
- Imagen core con `core0043`, BFFs compatibles, scopes `documents:read/write`.
- Ambos receptores documentales probados por HTTP real, concurrencia, pérdida de
  ACK y reinicio. Configurar un secreto de entrega sin publicarlo en argv/logs.
  SwissJob recibe en `/api/v1/integration/events`; Portfolio en
  `/api/v1/integration/inbox`. Comprobar destino/consumer, no inferirlo del nombre
  de la tabla de routing: SwissJob usa `swissjob` en routing y `swissjob-shadow`
  como consumidor core.
- Exportación y borrado del propietario verificados para el alcance del corte.
  Un borrado SQL NO acredita el borrado de backups; su cierre se prueba aparte.
- Drenar generaciones/entregas en vuelo y confirmar cero journals pendientes
  ANTES de congelar. Congelar todos los escritores documentales del BFF y
  comprobar la sonda del proceso efectivo. Si el proceso antiguo requirió SIGKILL,
  no llamarlo drenaje: revisar journals/transacciones y reconciliar antes de seguir.
- Conservar el routing local durante el snapshot y la importación. No apagar
  colegios ni fuentes para simplificar este corte.

## Herramienta administrativa

Viaja en la imagen core: `python -m jobhunt_core.document_cutover`. No monta
código mutable sobre la API y no activa políticas. Todas las credenciales van
en el entorno privado, nunca en argumentos:

- `SOURCE_DATABASE_URL`: conexión asyncpg a la base del BFF, schema `public`.
- `CORE_DATABASE_URL`: conexión core con su rol/configuración habitual.
- `DOCUMENT_FREEZE_URL`: `/health/documents` SwissJob o `/health/deep` Portfolio.
- `DOCUMENT_FREEZE_TOKEN`: token administrativo vigente para la sonda privada de
  Portfolio; solo entorno privado, nunca argv/logs. No abrir `/health/deep` ni
  sustituirla por una sonda sin estado de freeze. Un 401/403 aborta el corte.
  Renovar el token si caduca antes de la comprobación final de la transacción.
- Portfolio además: `SOURCE_DOCUMENT_OWNER_ID` y `SOURCE_CORE_PROFILE_ID`, copiados
  de la configuración efectiva del BFF, no adivinados desde una candidatura.

Directorio de artefactos 0700 y ficheros 0600, propiedad del usuario que ejecuta
la herramienta. Si se ejecuta en contenedor, usar ese UID/GID. El lote contiene
documentos personales: no subirlo a Git ni adjuntarlo a informes públicos.

`bindings.json` es un objeto `{id_local: uuid_perfil_core}`. SwissJob vuelve a
comprobarlo contra `jobhunt_profile_map` bajo lock; Portfolio exige coincidencia
exacta con su configuración de propietario. El core comprueba el consumer de
cada perfil. Una cuenta ajena o un vínculo cambiado abortan sin importación parcial.

```bash
python -m jobhunt_core.document_cutover snapshot \
  --origin swissjob --consumer swissjob-shadow \
  --bindings /privado/bindings.json --bundle /privado/lote.json
python -m jobhunt_core.document_cutover import \
  --bundle /privado/lote.json --report /privado/importacion.json
```

Portfolio usa `--origin portfolio --consumer portfolio`. No reutilizar un lote
entre consumidores. Los destinos de ficheros deben ser nuevos: nunca se sobrescribe
un sello ni un recibo. La salida estándar contiene solo estado/huellas, no CV.

El snapshot valida columnas y PK/FK incluso con tabla vacía. La importación:

1. Comprueba freeze, routing fresco local, vínculos y cero entregas pendientes.
2. Bloquea routing → propietarios → colecciones del origen; coteja el snapshot
   completo por contenido, no `max(updated_at)`.
3. Bloquea perfiles core en orden estable; conserva UUID, fecha, idioma, contenido,
   snapshots y metadata. Inserta documento/procedencia/evento en la misma transacción.
4. Relee y compara todos los campos. Colisión o divergencia revierte todo el lote.
5. Confirma core y escribe el recibo privado. El origen no recibe escrituras.

Si se pierde el ACK/fichero de recibo, repetir **el mismo lote sellado mientras el
routing siga local**, con un nuevo nombre de recibo. No crea otra generación ni
reemite eventos ya confirmados. Con routing core la importación se niega: no puede
resucitar bajas posteriores al corte.

## Confirmación y flip

Con escritores aún congelados: comprobar la biblioteca completa por ambos BFF,
PDF/descarga, hashes/fechas/snapshots, ownership y entrega real de eventos. Cambiar
solo la capacidad `documents` del perfil/consumer verificado mediante el mecanismo
transaccional de routing existente. Revalidar lector/escritor core sin fallback.
Solo después abrir escritores y ejecutar alta/listado/descarga/baja sintéticos
por HTTP con limpieza de esas identidades, sin enviar candidaturas ni correos reales.
Verificar CV y carta por separado y como pareja: una carta exitosa no demuestra
que quepa el presupuesto del CV. Portfolio traduce una respuesta inválida del
proveedor, tras un único reintento, a 502 sin guardar la pareja parcialmente.
Revisar cuota y consumo del proveedor; no resolverlo aumentando tokens sin medir.
No imprimir prompts, respuestas personales ni claves. Las pruebas adicionales con
CV real en un proveedor externo requieren autorización; una prueba sintética
debe declararse como tal, no como canary real del propietario.

## Reversión después de escrituras nuevas

Volver a congelar y drenar antes de revertir. El routing debe seguir core durante
la copia inversa; no hacerlo local primero, pues ocultaría documentos nuevos.

```bash
python -m jobhunt_core.document_cutover reverse \
  --bundle /privado/lote.json --import-report /privado/importacion.json \
  --report /privado/reversion.json
```

La reversión exige el recibo confirmado correspondiente al sello y a cada documento.
Lee la colección core VIGENTE bajo locks de perfil: incorpora altas posteriores y
respeta bajas, sin reponer el snapshot original a ciegas. Reconcilia solo los
propietarios vinculados en una transacción del BFF; una colisión con otro propietario
revierte todo. Compara la huella material antes de confirmar. Conserva el mismo
orden de locks entre bases que la ida; no necesita una transacción distribuida:
en cada dirección solo una base recibe escrituras.

Después del recibo verificado, cambiar routing a local, comprobar la biblioteca
y abrir el escritor local. El core queda como copia **inerte**, no se borra durante
la maniobra. Esto acredita reversibilidad funcional/material del propietario, no
igualdad byte a byte de toda la base compartida ni borrado GDPR. Retención/erase de
esas copias deben quedar inventariados; nunca ejecutar un restore global sobre
datos de otros consumidores para simular una reversión limpia.

No degradar core0043 mientras haya documentos o eventos documentales. Una imagen
vieja cuyo head no coincide tampoco constituye un rollback operativo válido.
