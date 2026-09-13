# E.14 — corte documental de SwissJob y Portfolio

## Estado verificado

13-09-2026. **Corte documental desplegado en ambos BFF:** `documents=core_primary`,
historial conservado y escritores abiertos. Alcance: CV/cartas, sin cambiar
catálogo, matching, modelos, calidad, colegios ni productores. No hay API nueva.
Cron de backups sigue diferido por el propietario al cierre final.

**PUNTO 2 CERRADO.** Tras la autorización explícita del propietario para Groq,
se repitió la operación original con el CV real y ecf1c0a: generación conjunta,
lectura, ambos PDF, replay con los mismos IDs y baja exacta aprobados.
Los cuatro eventos nuevos llegaron a su inbox; cero journals pendientes,
históricos intactos y escritor único confirmados. No se enviaron candidaturas.

Versiones efectivas:

- Core API/worker/captura y BFF SwissJob: **d2a38e9**, esquemas
  **core0046 / b46e1230a901**.
- Portfolio: **ecf1c0a**, imagen `portfolio-backend:e14-ecf1c0a`,
  esquema **rr11s0042u18**.
- CLI administrativa: **0de9101**, imagen `swissjob-core:e14-document-cli`.

Se añadieron solo los scopes documentales a las dos credenciales efectivas,
un secreto privado de entrega, los dos destinos HTTP del worker y el routing
documental. No se publicaron credenciales ni se subieron cambios a remotos Git.

## Ensayo y preservación

Tres copias privadas nuevas, restauradas en contenedor local sin red ni puertos:
`pg_restore --exit-on-error --single-transaction`, sin errores ocultos.
Paridad estructural: Portfolio 27 constraints/50 índices/0 triggers;
BFF 174/146/13; core 208/161/39. Solo cinco equivalencias históricas de CHECK
recompiladas y demostradas; ninguna diferencia nueva admitida.

Ida/vuelta con imagen vigente: dos históricos SwissJob y un documento sintético
en el esquema restaurado real de Portfolio (su historia estaba vacía). Importación
idempotente, vuelta material exacta, altas/bajas posteriores respetadas y segunda
vuelta idempotente. Nunca se restauró una base sobre producción.

Corte vivo: drenaje con salida 0 de ambos BFF, sin SIGKILL; freeze efectivo y
cero journals pendientes. Dos históricos SwissJob y cero Portfolio importados,
sellos verificados y replay con 0 inserciones. Huella fuente SwissJob:
`43a732866823fa5ece08dda571ad374fc40dc97bea8065371f055bea0e49ab95`.

Tras altas/bajas de canarios, el core conserva exactamente los dos históricos.
Los mismos hashes materiales antes/después, comprobados por HTTP:

- `8e6bf43b77023f862a0c5342875d1684398a456e058f875c9784b6345459e6e6`
- `b03b71c1472b9e553614295be0dc64e587c91794db6f7caae5d8fb2b695bdb43`

Los almacenes locales siguen con 2/0 documentos, sin nuevas escrituras.
Cero journals pendientes; 6 eventos SwissJob y 6 Portfolio entregados y recibidos
(incluyen importación, altas y bajas). Lectura cruzada entre consumers: 404.

## Defectos encontrados y correcciones

### Sonda de freeze privada — 0de9101

La CLI consultaba `/health/deep` de Portfolio sin auth: 401.
`DOCUMENT_FREEZE_TOKEN` por entorno privado permite la sonda administrativa
existente, sin abrirla ni seguir redirecciones. Token corto del propietario
administrativo, sin cambiar roles ni revocar sesiones. Regresión HTTP roja con
d2a38e9 y verde con el fix; sonda real autenticada aprobada. El freeze sigue
rechazando mutaciones anónimas con 503 antes de auth.

CLI basada en d2a38e9 con un único COPY; no se montó código mutable sobre la API,
no se cambiaron migraciones ni imágenes de API/worker/captura.

### Generador Portfolio — 1f1f8ae y ecf1c0a

El canary real de carta devolvió dos respuestas vacías y un 500. Se añadió error
de dominio tras el único reintento y traducción a 502, antes de guardar cualquier
documento. Las cuatro regresiones fallan en 715c347; pasan con la corrección.

La primera ampliación a 8192 tokens permitió carta+PDF reales (18,81 s), pero el
canary conjunto descubrió que NO bastaba: CV vacío y reintento rechazado con 413,
cuota TPM 8000 frente a petición 10092. No se oculta como éxito del conjunto.

Corrección final ecf1c0a: fallback Groq con razonamiento **medio** y 4096 tokens
de salida; conserva Gemini, prompts de fidelidad y forma, timeout y un único
reintento. No se cambian cuenta, modelo, plan de pago ni chatbot.
El límite incluye razonamiento y texto, según la
[referencia de Groq](https://console.groq.com/docs/api-reference).
Las dos regresiones de presupuesto fallan contra 1f1f8ae en archivo de HEAD
aislado; pasan con ecf1c0a. Pruebas reales del proveedor con entradas sintéticas
largas: CV 2,79 s / carta 1,57 s, JSON completo y finish=stop.
Esto no acredita por sí solo la pareja HTTP con el CV real.

Imagen final construida desde archivos comiteados, dos COPY sobre la base vigente.
Mismo entorno (comparado como mapa, no por orden), volúmenes y operación original.

## Evidencia y rendimiento acotado

- Contratos HTTP/PG/CLI: **9 passed**.
- Core d2a38e9: **1161 passed, 1 skipped**, 941,88 s.
- Core con CLI corregida: **1162 passed, 1 skipped**, 880,72 s.
- Portfolio: **1979 passed, 1 skipped**, 219,28 s (primer fix) y
  **1979 passed, 1 skipped**, 219,08 s (presupuesto final).
- Dirigidos Portfolio: **42 passed**, antes de cada suite.
- Navegador NAS real: biblioteca, PDF histórico de 560.024 bytes, móvil
  390×844 y cero errores JavaScript.
- SwissJob: carta nueva 6,87 s y CV nuevo 15,12 s; lectura y repetición del mismo
  operation_id sin duplicación. Portfolio: carta real, lectura/replay y PDF
  de 10.489 bytes con la primera corrección.
- Imagen final ecf1c0a, proveedor real y datos exclusivamente sintéticos:
  CV+PDF de 10.296 bytes en 26,81 s y carta+PDF de 8.574 bytes en 7,41 s
  (incluye render frío; sin reemplazar parámetros del código desplegado).
- Los tres canarios se borraron por IDs exactos, DELETE 204 seguido de GET 404;
  también se retiraron sus archivos temporales de contenido.
- Muestra de 20 GET por BFF, todos 200: SwissJob p95 683,3 ms / media 376,4 ms;
  Portfolio p95 887,2 ms / media 392,5 ms. Historial de solo dos documentos y
  NAS compartido: no es una certificación de carga ni del rendimiento global.
- Última verificación: API/captura/BFF SwissJob healthy; worker/Portfolio running,
  cero reinicios. Portfolio tiene sonda HTTP 200, no healthcheck Docker propio.

Incidencias de invocación registradas: primera colección con imagen antigua;
otra sin mounts administrativos; ambas repetidas correctamente. Buildx QNAP
rechazó la invocación, se usó builder clásico. El primer despliegue del generador
completó pero la comprobación de entorno falló por orden de variables; comparación
por nombre/valor confirmó cero diferencias. No se contabilizan como pruebas verdes.

## Recuperación, custodia y continuación exacta

Seguir [RUNBOOK_DOCUMENTOS_E10.md](RUNBOOK_DOCUMENTOS_E10.md): freeze/drenaje,
reverse de la colección core vigente con recibo, verificación, routing local y
solo entonces abrir escritor. Nunca restore global ni downgrade anti-resurrección.

NAS privado: `unification-e10.XXkEjw88/e14-documents` (0700, archivos sensibles
0600). Configuración persistente Portfolio: `portfolio.configured.yml`, con
versión de freeze separada. Los configs no son dumps caducables.
El contenedor anterior `portfolio_backend_before_e14` permanece detenido,
restart=no; no reiniciarlo como rollback improvisado.

Sellos SwissJob/Portfolio registrados como rollback por siete días desde su
creación, sin extender fechas. Registro NAS: 18 entradas, ningún vencido pendiente.
Las tres copias locales y el clúster restaurado completo se retiraron tras comprobar
cero sesiones y bind/red exactos. También se retiraron los sellos de ensayo locales,
el PDF temporal y la copia aislada usada para la mordida. Backups NAS vigentes y
sellos operativos se conservan. Las 28 copias privadas de configuración/diagnóstico
utilizadas durante la comprobación quedan registradas localmente por 48 horas,
con fechas originales, en `/tmp/unification-e14.OxUv3DAU/retention.json`.
No se instaló cron: la retención sigue operándose manualmente.

## Confirmación final tras autorización — punto 2 cerrado

Misma operación original, CV real, Groq configurado y versión ecf1c0a, sin
sustituir entradas ni parámetros. Recorrido completo: **28,16 s**.

- Generación conjunta y lecturas: **HTTP 200**.
- PDF CV: **15.619 bytes**; carta: **10.287 bytes**, ambos con firma PDF válida.
- Replay: los dos IDs idénticos; no se duplicaron documentos.
- Bajas de canarios: **204**, seguidas de lectura **404**:
  `1dbabab0-7942-46d6-9da8-be03292b3fa8` y
  `68f9df55-0b12-4ab6-aade-2ba005529dc4`.
- Sus cuatro eventos (alta/baja por documento) entregados y presentes en el
  inbox de Portfolio: seis recibos Portfolio en total y seis SwissJob.
  La pasada adicional del dispatcher reclamó cero porque ya estaban entregados;
  la evidencia es el cotejo outbox↔inbox por event_id, no ese contador cero.
- Core conserva solo los dos históricos; las bibliotecas devuelven los hashes
  originales y Portfolio queda vacío como antes. Locales 2/0 sin nuevas filas,
  journals pendientes 0/0 y routing exclusivamente core_primary.
- Lectura cruzada entre consumers: 404; servicios sanos/running y cero reinicios.
- Archivo temporal con el contenido de esta pareja retirado tras validar
  deleted=true e IDs exactos. Los recibos de auditoría permanecen.

Esta continuación no modifica código funcional ni imágenes: valida la versión
que ya pasó las suites indicadas, sin presentarlas como reejecutadas.
La autorización pendiente quedó resuelta; no queda trabajo abierto del punto 2.
Cron diferido, colegios/productores y calidad mantienen sus alcances separados.
