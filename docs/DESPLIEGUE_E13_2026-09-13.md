# E.13 — borrado coordinado desplegado; retención pendiente de autorización

## Estado vigente

13-09-2026. Prevalece sobre el estado previo de `BORRADO_COORDINADO_E13.md`.
Core API/worker/captura: **d2a38e9 / core0046**. BFF público:
**d2a38e9 / b46e1230a901**. Copia CDC: esquema BFF **b46e1230a901**;
su backend y productores conservan la imagen anterior. Únicamente el nuevo
`swissjob-erasure-cdc` ejecuta `python -m services.profile_erasure --watch`.
No arranca otro escritor de exclusiones, servidor HTTP ni cosechador.

API/captura/BFF healthy; worker y reconciliador arrancados, cero reinicios en la
comprobación. Portfolio conserva su imagen y su cuenta. No se ha cambiado routing,
política de ranking, holdout ni racha. No es GO de calidad ni cierre de los demás trabajos.

Imágenes verificadas al cargar en NAS:

- Core: `69899961d514dc76a86e8ff45c66d63a1c5bd3f55cc05b320c51dce157802cd4`.
- BFF: `1c66ad5055b2ae33e6f3b438fc2d95f3442f6a009e0f85926e8384ba44664eb4`.
- Archivo de transporte: SHA256 `b61b818958686d2d0eebec4c95316039637af1e9de0429f0e115fdcf92aca3c6`.

## Pruebas realizadas, sin borrar cuentas reales

- Imágenes exactas: **31 tests core** y **10 BFF**, en serie. Las suites completas
  previas (1160 core / 2384 BFF) siguen siendo la evidencia del commit; no se dice
  que se hayan vuelto a ejecutar completas durante el despliegue.
- Herramienta de retención y script diario: **9 tests** locales sin red ni NAS,
  incluidos fallo de dump y archivo inválido. `bash -n` de ambos scripts correcto.
- Cuenta sintética en BFF vivo, origen CDC y core: DELETE HTTP **200**;
  token anterior **401**; solicitud durable confirmada; recibo core y acks
  **swissjob-live + swissjob-cdc**. Perfil sintético ausente y cero payloads
  personales del canary sin redactar en captura.
- Re-alta core de esa identidad: rechazada por la valla.
- Simulación de restore en CDC: reconciliador detenido; se inserta SOLO la fila
  sintética antigua y se comprueba que existe; barrera one-shot elimina **1 cuenta**,
  aunque ya tenía confirmación anterior; reconciliador continuo reanudado.
- Restore local aislado previo (`e10_core_copy`, migrado a core0046): recibo real
  sellado del NAS, identidad sintética restaurada, ensayo con rollback, aplicación
  y segunda aplicación idempotente. Otros perfiles idénticos; número de vacantes
  idéntico. No se ha restaurado una base encima de producción.
- Huellas de identidades antes/después idénticas: dos cuentas en público y CDC;
  tres perfiles core. El inventario mínimo vigente se conserva privadamente en
  NAS y ordenador, separado de dumps antiguos.

Una sonda adicional de lectura de matching excedió 30 s en dos intentos; no se
ocultan. Comparación puntual y en serie de la misma consulta CoreMatching.results
sobre el mismo core: imagen anterior **21,436 s**, nueva **9,970 s**, ambas con
20 elementos y total 1533. No reproduce una regresión en esa consulta, pero no
prueba por sí sola la causa de los timeouts. Último canary HTTP completo: ambos
perfiles **200**, 20 ofertas; **10,939 s / 11,445 s**, totales **1533 / 1524**.
Muestra limitada, no certificación de p95 ni de arranque bajo toda carga. El
reconciliador dedicado usa aproximadamente 60 MiB y 0 % CPU entre pasadas.

## Backups: lo comprobado y lo NO autorizado todavía

Se han creado copias privadas actuales de las tres bases y comprobado su lectura
mediante `pg_restore --list`; eso no se presenta como restore completo de cada
archivo nuevo. Inventario inicial: **16 archivos**, con fechas originales,
tamaños, SHA256 y raíz privada. Preview: **7 vencidos**, **0 últimos backups
bloqueados por falta de sustituto**. `retention_met=false` significa aquí que
el preview NO ha retirado todavía los vencidos, no que el sustituto esté corrupto.

Ocho archivos antiguos que estaban fuera de custodia privada se trasladaron
a `e13-erasure/legacy-*` y quedaron con permisos 0600 bajo directorio 0700.
Sus nombres originales, fechas y destinos están en el inventario privado.
**Ningún backup ha sido eliminado**: la revisión automática de permisos rechazó
`--apply`, pidiendo autorización explícita para esos siete archivos. La solicitud
está presentada al propietario; no se ha intentado eludir el rechazo.

Preparados, NO instalados como tarea programada:

- `scripts/nas_backup_daily.sh`: tres dumps, validación de archivo, registro,
  inventario mínimo de supresiones, retención y marca de éxito. Los parciales
  fallidos se registran como temporales; un fallo no publica éxito ni purga copias.
- `scripts/nas_install_backup_cron.sh`: requiere administrador autenticado;
  mantiene los demás trabajos y programa 02:20 (hora del NAS), ejecución como
  Ricardo y aviso QNAP si falla. Necesita comprobar primera ejecución y alarma.

La sesión SSH actual no dispone de `sudo -n`. No se modifica el cron administrativo
sin autenticar ni se utiliza un contenedor privilegiado para evitarlo. El instalador
está disponible en el directorio privado NAS `e13-erasure`.

También permanece pendiente la retirada autorizada de las copias temporales
locales inventariadas y las bases desechables del contenedor privado de restore;
no se confunde registro de backups NAS con inventario/caducidad global cumplidos.

## Recuperación y límites

No ejecutar downgrade de core0046 tras confirmar recibos: sería quitar la
protección anti-resurrección. Conservar esquema, solicitudes, recibos y acks y
corregir hacia delante. Las imágenes previas se conservan para diagnóstico, no
como autorización de volver a un proyector que ignore las supresiones.

El punto 1 NO está cerrado mientras falten retirada autorizada, programación,
su comprobación y el cierre de copias temporales. La cuenta propietaria de
Portfolio no se elimina y no es una tarea pendiente.

Evidencia operativa privada: `unification-e10.XXkEjw88/e13-erasure` en el NAS.
No incorporar a Git sus env, inspecciones Docker, inventarios de sujetos ni dumps.
