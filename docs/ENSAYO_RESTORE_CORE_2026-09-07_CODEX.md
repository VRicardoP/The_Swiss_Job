# Ensayo local de restore y migración — 2026-09-07

> **Fotografía anterior a la autorización de la segunda copia.** Esa autorización,
> ambos restores y el despliegue ya se completaron: [acta vigente](CIERRE_DESPLEGADO_2026-09-08_CODEX.md).

## Veredicto y alcance

**Restore y round-trip de esquema core verificados. Despliegue coordinado pendiente.**
Complementa `CIERRE_LOCAL_FIXES_2026-09-07_CODEX.md`; código probado: `4f5a366`.
No se modificaron bases, imágenes ni servicios en ejecución del NAS. No se
promovió CE, abrió holdout ni alteró un gate. ReactPortfolio sigue utilizable;
E/F y la campaña de calidad independiente no quedan cerradas por este ensayo.

## Copia autorizada y aislamiento

Se completó SOLO la copia `swissjobhunter_r5_rehearsal` desde `swissjob-postgres`
del NAS, usando `pg_dump -Fc -Z1`. Exit code 0; después se renombró `.partial`.

- Directorio LOCAL privado: `/tmp/swissjob-restore-20260907.q7GxPh/` (0700).
- Fichero: `core.dump`, 652.241.450 bytes, modo 0600.
- SHA-256: `2196c9fd360db92fad3ccde1b7874168d439040075b7d1df537b1e5a1193d396`.
- Contiene datos personales: no versionar, publicar ni transferir a terceros.
- PostgreSQL local: `swissjob-restore-20260907`, base `core_copy`, imagen
  `swissjob-postgres-core:pg16`, `--network none`, sin puertos publicados.
  Los contenedores de migración compartieron SOLO su namespace de red aislado.
- No se copiaron contraseñas de roles. Se crearon localmente `jobhunt_core` y
  `jobhunt_capture`, sin contraseña; la confianza local no está expuesta en red.

La exportación adicional de `swissjobhunter` fue rechazada por revisión
automática: exige autorización expresa de esa base con sus datos personales y
este destino. No se reintentó ni se sustituyó por otro transporte. No existe
backup de esa segunda base en este ensayo.

## Evidencia ejecutada

1. Restore con propietarios/ACL conservados, `--exit-on-error --single-transaction`:
   exit 0, sin errores ocultos.
2. Comparación exacta de objetos de `public` + `jobhunt` entre NAS y copia:
   **182 constraints, 145 índices, 34 triggers no internos y 70 relaciones**.
   Se cotejaron definición y validación de constraints; definición/valid/ready de
   índices; definición/enabled de triggers; propietario/ACL/tipo de relaciones.
   Comparación de los JSON canónicos: idénticos; constraints sin validar: **0**.
   Esto describe los objetos comparados, no sustituye un oráculo integral de datos.
3. Estado restaurado: core0041, BFF b3c7d1a95e42; siete exclusiones.
4. Upgrade core0041 → core0042 mediante la imagen nueva y el rol jobhunt_core:
   exit 0. Los tres perfiles existentes quedan con exclusions_version=0.
5. Canary HTTP ASGI con autenticación real y consumidor/perfil sintéticos,
   exclusivamente en la copia: alta, baja, ACK repetido sin reescritura,
   entrega vieja que no resucita una baja, versión contradictoria → 409,
   perfil inexistente → 404. El cliente antiguo sin version recibe **400**.
6. Limpieza del consumidor sintético. Hashes de contenido antes/después iguales:
   profiles (3), profile_exclusions (7), profile_recovery_state (19),
   profile_vacancy_state (32.961), match_evaluations (66.769).
   No se evaluaron modelos ni publicaron feeds. La generación sí avanza, como
   exige la invalidación; no se la retrocedió para aparentar igualdad.
7. Downgrade core0042 → core0041: exit 0; los objetos estructurales del esquema
   jobhunt vuelven a coincidir exactamente con el origen.
8. Ensayo ADICIONAL del DDL BFF en public de esta MISMA copia R5: upgrade correcto;
   seed de una fila, siete reglas, entrega pendiente. Downgrade rechazado con
   `pending exclusions: drain or explicitly reconcile before downgrade`.
   Tras el rechazo se conserva c4d8e2f60a17 y la fila pendiente intacta.
   **No se falsificó un ACK para forzar el downgrade.**

La copia R5 tiene CERO vínculos para esa fila en jobhunt_profile_map. No sustituye
por tanto el ensayo BFF→core sobre una copia de `swissjobhunter` actual. El canary
del punto 5 verifica la API core, no el circuito completo del BFF de producción.

Artefactos privados: `structure.sql`, `core.source.structure.json`,
`core.restored.structure.json`, `core.after_down.structure.json`, `core_canary.py`.
Al terminar se detiene el contenedor local; se conservan dump y datos del ensayo.
La copia termina en core0041 + BFF c4d8e2f60a17 con pendiente, deliberadamente.

## Imágenes y cautela de dependencias

Se construyó core `swissjob-core:4f5a366` desde `git archive 4f5a366`, sin incorporar
el cambio ajeno de `school_job_monitor_architecture.md`.

Se prepararon dos candidatos BFF locales, NO desplegados:

- `swissjob-backend:4f5a366`: build convencional reinstala dependencias transitivas;
  no autorizarlo solo por compartir el fichero requirements con las suites previas.
- `swissjob-backend:4f5a366-pinned`: reutiliza dependencias de la imagen LOCAL
  `swissjob-backend:prod` (ac5624c5…), sustituyendo código. Se usó para el ensayo DDL.
  **No es prueba de igualdad con producción NAS**, cuya imagen observada es
  8c82ff5c…. Su equivalencia y regresión en imagen siguen pendientes.

La etiqueta :prod local no identifica la imagen prod del NAS. No se retaggeó ni
desplegó ninguna de las dos. YAGNI favorece conservar dependencias verificadas,
pero debe comprobarse su identidad efectiva antes de declarar equivalencia.

## Siguiente paso bloqueante y secuencia

1. Autorización explícita para copiar `swissjobhunter` (cuentas, perfiles, CV y
   preferencias) del NAS al directorio LOCAL privado arriba indicado, solo ensayo.
2. Restore estricto de esa base y la misma comprobación estructural; ensayar con
   sus vínculos reales la cola versionada, entrega, recuperación de ACK y rollback.
3. Verificar dependencias e imágenes realmente destinadas al NAS. Las suites
   completas del código fueron 1073 core / 2288 BFF (+3 skip,4 xfail) / 7 frontend
   en la sesión previa: NO se presentan como reejecutadas en estas imágenes.
4. Coordinar migración y cambio de imágenes: antiguo BFF no envía version y el
   nuevo core lo rechaza. No permitir que un escritor antiguo siga editando entre
   el seed y el arranque del escritor que encola snapshots. Ensayar reversión de
   imágenes sin borrar pending ni restaurar una base core compartida completa.
5. Canary del feed SERVIDO y observabilidad de pending, generación y recuperación;
   confirmar en operación antes de declarar estas correcciones desplegadas.
6. Continuar E/F por capacidad sin apagar fuentes escolares ni otros escritores
   sin inventario/paridad. La retención legacy requiere decisión explícita.

No hay autorización nueva de retención, borrado de backups ni retirada legacy.
No hay garantía de ausencia absoluta de bugs ni GO de calidad implícito.
