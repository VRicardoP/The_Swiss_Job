# Punto 4 — perfiles: ensayo aislado del NAS

Fecha: 20-09-2026. **No es un despliegue ni cierra el punto 4**.

## Entorno y límites

Copia privada ya restaurada estrictamente y contrastada estructuralmente en el
ensayo del 19-09: `swissjob-f-rehearsal-20260919`, bases `source_copy` y
`core_copy`. Sin puertos publicados y sin red externa. Los procesos de ensayo
comparten sólo su namespace de red; la aplicación productiva no se conecta a
esas bases. Datos personales y credenciales permanecen en el NAS. Caducidad del
ensayo original: 21-09-2026 20:00 UTC; eliminar también el contenedor/PGDATA al
terminar, no sólo los ficheros temporales.

Se reutiliza el runtime de `swissjob-core:e15-1b8d910`, montando el código candidato
en sólo lectura. Esto verifica compatibilidad del runtime, **no** sustituye un
build limpio ni el canary de la imagen final. Credenciales sintéticas emitidas
únicamente en `core_copy`, con caducidad de una hora y revocadas tras el ensayo.

## Migración y reversión protegida

SQL generado por Alembic desde los ficheros reales, aplicado con
`psql -X -v ON_ERROR_STOP=1`, sin ocultar errores:

- Core: `core0048 → core0049`, confirmado en una transacción.
- Fuente: `b46e1230a901 → c57f2341b012`, captura atómica de los dos perfiles.
- Tras las entregas, ambos downgrades son rechazados expresamente: no se borra
  la autoridad ni la cola. Salida 3 esperada; el esquema conserva su revisión.
  La recuperación compatible es hacia delante, conservando versiones y datos.

Digest del SQL de upgrade core:
`07dd1ba9b33e04900855263bd582154baede942af455bd33360ff9090630d149`.
Digest del SQL de upgrade fuente:
`c94b6661403f1405bb71489be9712744d18d2ed64a23671cd52d4a105058ea6d`.

## Entrega y recuperación del contenido

`scripts/rehearse_profile_delivery.py` impone las direcciones de las copias y
ejercita los handlers HTTP reales mediante ASGI. No simula el core ni sale a la
red pública. Para ambos perfiles se comprobaron:

1. Seed, entrega inicial y repetición de la misma versión.
2. Edición del escritor, incremento transaccional y rechazo de sobrescritura
   atrasada. Misma versión con contenido/actividad distintos: 409.
3. Vaciado: cero punteros de evaluación; feedback y guardados idénticos.
4. Recuperación del contenido original mediante una versión nueva, sin rebobinar
   contadores ni reactivar CDC como autoridad.
5. La revisión recuperada no conserva intentos que supriman su reevaluación.

Resultado de la segunda ejecución: `verified_on_isolated_copy`, profiles=2,
retry_ordering_conflict=true, empty_withdraws_feed=true,
feedback_bookmarks_preserved=true, forward_recovery=true, recovery_rearmed=true.
`live_deployment_verified=false` explícito. No equivale a comprobar el transporte
de red del BFF ni su ACK; éstos tienen pruebas locales y requieren canary vivo.

El primer ensayo validó contenido, pero no rearme. La revisión posterior encontró
el caso A→vacío→A: una revisión histórica podía mantener su intento antiguo y
dejar el feed vacío. La regresión reprodujo `pid not in pending`; se corrigió
invalidando intentos cuando cambia la revisión o la actividad. La prueba verifica
también que la reevaluación vuelve a servir ofertas. No se debilitó el test.

Después del primer ensayo, las tablas fuente volvieron a su hash canónico previo:
`user_profiles`: `7d736a896618980431a63bfb73788196db7ddaa21d156b57892314f5ed7b820b`;
`users`: `42df310a07e958e597b34f945d0603635ac88d10b0dc30847feb4af7ce1a9fcd`.
Son hashes de filas JSONB ordenadas, no de los bytes físicos de PostgreSQL.

## Verificación local y recuperación de feed completadas

- BFF, commit `a93dcbc`: **2459 passed, 3 skipped, 4 xfailed**, 316,93 s.
  Log SHA256 `4a548037ba4855cf485f00161ee03d77411941cc0a472407f465957ac7a4412f`.
- Core antes del último borde A→vacío→A: **1498 passed**, 856,03 s.
- Borde de retorno: **rojo demostrado**, luego **16 dirigidas verdes**, 10,25 s.
  Logs rojo/verde SHA256:
  `53224864a7e4e01f5e6757a3550b006102dc2d5da3715985a5727bbe3ab38ea6` /
  `e805928298fa117c02f80b7ca857c5c7272aded2e4095326f1d7e027e4451c39`.
- Repetición completa del core, commit `bac5e78`: **1499 passed**, 856,37 s.
  Log SHA256 `aa73d89654230e701401b12c9198b8f260cc1a80e60d7ee093ad640f1d3e29b6`.
- Recuperación completa en copia: **dos perfiles, 20 ofertas servidas cada uno,
  cero punteros a revisión obsoleta**. Reutilizó dos vectores por texto; no hizo
  inferencia nueva. 1.160,388 s con PostgreSQL y proceso limitados a 0,5 CPU,
  copia fría y ambas políticas activas evaluadas. Es una comprobación funcional,
  no una medición de latencia de producción ni una ejecución del holdout.
- Sonda separada del encoder registrado: **vector real válido de 384
  dimensiones**, sin red y con caché de modelo readonly. 228,208 s en frío,
  CPU limitada a 0,25 y RAM a 1.200 MiB. Acredita funcionamiento del encoder,
  no un presupuesto de producción ni el rendimiento del cross-encoder.

Producción sigue en E.15/core0047, salvo el checkpoint previo del worker público
`d89b6ee`. `CORE_PROFILE_SYNC_ENABLED` no se ha activado allí.

## Imágenes construidas, aún no activadas

Commit limpio `bac5e78e5f989f9ffa8c827e1497b2aa327d1428`, sin cambios de
dependencias frente a las bases E.15 exactas. Build local al NAS, sin red ni push.

- Core `swissjob-core:point4-bac5e78`:
  `sha256:d821671f836e7cd0e71a6eb0334924d373120594f2052e84429f7f758953925b`.
  Usuario `core`; marcador horneado y etiqueta coinciden con el commit.
  Hash del endpoint ejecutable:
  `344fa12a6fc4be46792744064d50876b52af632c0391c7967530e81ad216273c`.
- BFF `swissjob-backend:point4-bac5e78`:
  `sha256:a6b36ae595d2eb7a03c17199182726a48c37abf23f29b1cd82a0b24e78baed48`.
  Usuario `app`; etiqueta de revisión coincidente.

## Transporte entre las imágenes finales

API temporal de la imagen nueva en loopback del namespace aislado, sin publicar
puertos. Readiness HTTP **200 / ready**. Cliente ejecutado desde la imagen BFF
nueva, contra `source_copy`, sin lifespan/schedulers/worker de correo.
Credencial temporal emitida sólo en `core_copy`, sin imprimir el secreto:
**2 perfiles confirmados por HTTP, segunda pasada = 0 pendientes**.
La credencial se revocó al terminar. La API temporal se detuvo y retiró; la copia
PostgreSQL se conserva para los siguientes ensayos hasta su caducidad. No se
ha cambiado la configuración productiva ni habilitado la entrega allí.
Estos pasos completan el ensayo de transporte; queda el canary del entorno vivo.
