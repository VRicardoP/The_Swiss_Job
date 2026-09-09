# E.10 — despliegue verificado y límites del cierre (2026-09-09)

## Resultado

Se han desplegado las implementaciones E.8/E.9/E.10, el inbox y la corrección
de EOF WordPress. **No es el cierre de los cinco trabajos ni un GO de calidad.**
El routing de documentos sigue local; no se ha ejecutado la migración documental
sobre producción ni se ha parado ningún productor para aparentar la unificación.

| Servicio | Release desplegada | Esquema / comprobación |
|---|---|---|
| Portfolio backend | `715c347` | `rr11s0042u18`; canary autenticado |
| Core API, worker, capture R5 | `613f1d5` | `core0043`; ready autoritativo; worker pong |
| SwissJob BFF | `613f1d5` | `f70b15293d40`; healthy |
| SwissJob frontend | `4dcfa0e` (bundle `21c07c6`) | healthy; navegador real y PDF |

Identidades Docker NAS (config SHA, no confundir con manifest-list de buildx):

- Portfolio: `ebd77cf1ae27e901c31d5c3845c1676e9b94446f749c72a510fb24a04f8c0fb4`.
- Core: `810a27704b3021b9f4f415562cd6fd28d8b076085ce4a7f46127a9bc09268626`.
- BFF: `154d00dd0bbc326f782cba33720888a00e43e5a6840c32fe253dbf0e7f9eae4b`.
- Frontend final: `fce756a0fd2363b7671dcbc240150b2df087093c6cd000e917ac43def19258f6`.

## Verificación realizada

Suites ejecutadas **en serie**, sin concurrencia sobre la BD de tests:

| Verificación | Resultado |
|---|---|
| Core, imagen exacta `613f1d5` | 1136 passed, 1 skipped; 866,58 s |
| BFF, imagen exacta `613f1d5` | 2359 passed, 3 skipped, 4 xfailed; 286,44 s |
| Portfolio `715c347` | 1975 passed, 1 skipped; 220,50 s |
| Contratos HTTP/auth/PG y CLI documental | 9 passed; 51,29 s |
| Frontend final | 16 passed; 8 ficheros |
| Aislamiento proxy real Docker/Nginx | 30/30 producción y 30/30 ensayo |

Las tres pruebas BFF gated por credencial se omitieron con CORE_CONSUMER_KEY vacío;
no se presentan como E2E ejecutado. Los contratos HTTP/PG de la tabla son pruebas
separadas. No se ha certificado ausencia absoluta de regresiones.

El ensayo sobre copias privadas, restauradas con owners/ACL y `--exit-on-error`,
está en [ENSAYO_E10_DOCUMENTOS_2026-09-09.md](ENSAYO_E10_DOCUMENTOS_2026-09-09.md).
Incluye dos documentos históricos SwissJob, fixture Portfolio separada (histórico
real vacío), import idempotente, reverse con altas/bajas posteriores y comprobación
de contenido. **No confundir ese ensayo con un corte documental en el NAS.**

Canary Portfolio: GET/POST/core directo devuelven los mismos 50 IDs (SHA
`9463f913dcdec3c9a71d4f9f26dee28e3ee9de0a45609f1f7f93c749d9b0c8b5`),
progreso 100/persisted_feed, cero operaciones documentales pendientes;
schedulers armed y escrituras open. Las sondas privadas requieren autenticación.

Navegador real sobre `:4000`, repetido después del último cambio: login visible,
biblioteca con los dos documentos históricos, PDF válido de 560024 bytes,
390×844 sin desbordamiento y cero errores JavaScript. No se generó ni borró nada.

## Defecto descubierto durante el canary y corregido

El alias DNS `backend` era anunciado por producción y `swissjob-backend-r5` en
la red compartida. Nginx repartía peticiones entre ambos: se reprodujo un 404
intermitente de la biblioteca. La sonda nginx-health no detectaba ese defecto.

`4dcfa0e` usa la plantilla nativa de Nginx: destino por defecto
`swissjob-backend`; el manifiesto R5 declara `swissjob-backend-r5`. Se actualizó
también el manifiesto operativo R5, sin recrear sus servicios legacy.
La prueba `scripts/test_frontend_proxy_isolation.py` construye una red interna
sin datos/puertos publicados y ambos aliases en conflicto: la imagen anterior
`21c07c6` FALLA con respuestas production/rehearsal mezcladas; la corregida PASA.
La regresión de configuración también se verificó roja antes del fix.

`21c07c6` había corregido previamente un `\\n` literal visible en el generador,
con regresión de render primero roja. No se alteraron dependencias ni el ranker.

## Rendimiento: medido, todavía NO cerrado

Tras corregir el proxy, 20 peticiones reales por ruta y contenido conservado:

| Ruta | p50 | p95 | Máximo |
|---|---:|---:|---:|
| Biblioteca documental (2 documentos) | 53,27 ms | 137,04 ms | 205,40 ms |
| Journal de operaciones (vacío) | 81,40 ms | 135,34 ms | 175,42 ms |

**Defecto abierto reproducido:** GET `/api/v1/jobs/search?limit=20` excede el
timeout de 30 s desde `:4000`, también después del fix de proxy. No se aumenta
el timeout para convertirlo en un éxito. El routing real es catalog core_primary;
`services/catalog/core_client.py` recorre el feed filtrado completo para total y
offset. Es una hipótesis concreta de coste, no una causa perfilada todavía.
Siguiente paso: medir páginas/SQL y tiempo BFF→core; eliminar el recorrido completo
con un contrato de paginación/recuento coherente, conservar filtros/identidad,
regresión de presupuesto y comparación sobre corpus poblado. No volver a declarar
rendimiento cerrado con la colección de documentos o de journal vacía.

## Copias y reversibilidad del despliegue

Artefactos privados NAS: `/share/CACHEDEV1_DATA/Public/unification-e10.XXkEjw88/`.
No están en Git; contienen copias, configuración privada y scripts de despliegue.
Backups inmediatamente anteriores al relevo Swiss/core:

- Core: `791306457079e0e32bb54701e87fc789688819a241e093d2435f805415441c95`.
- BFF: `1476041e9be837afaa8b9f183caabd8294509160bf3f9f46263ec6e35996345e`.

Portfolio se detuvo con gracia y exit 0; se conserva
`portfolio_backend_pre_715c347` y la copia previa. Frontend conserva
`rollback-before-4dcfa0e`. La imagen frontend final viajó con SHA de archivo
`7b2f38dd3a1dcaab512a04a00266fab9165c7509fcb5f35cc4f95c8c61406e73`.

No prometer rollback del core/BFF simplemente re-etiquetando una imagen anterior:
sus revisiones Alembic son anteriores. Hace falta código compatible o downgrade
ensayado con sus guardas de datos vacíos; con datos nuevos se conserva y reconcilia
el estado. **No restaurar globalmente el corpus vivo ni borrar backups útiles.**

## Cinco trabajos: estado y siguiente condición comprobable

1. **Documentos:** código compatible desplegado; ida/vuelta comprobada en copia.
   Falta export/erase integral confirmado, configuración/scopes de delivery,
   import histórico vivo y canary/flip por propietario. Core tiene 0 documentos.
2. **Colegios:** no migrados. Preservar monitores, contacto, borradores y acciones
   por consumer; oferta escolar vinculada al corpus único, no un segundo corpus.
3. **Productores/F:** 0 scopes nativos habilitados; fuentes legacy siguen vivas.
   Portar con paridad de identidad y medir cobertura antes de detener productores.
4. **Privacidad:** exportación actual es parcial; no hay workflow completo
   erase→acks de consumidores→backups. No llamar confirmación GDPR a DELETE SQL.
5. **Rendimiento/recuperación:** medición anterior y timeout de catálogo pendientes
   de resolución; falta carga de escrituras/LLM/backlog/reinicio representativa.

### Bloqueo de autorización, no un cierre técnico

La revisión automática de permisos rechazó crear `jobhunt_core/api/v1_privacy.py`:
el endpoint nuevo expondría por red CV, candidaturas, estado/documentos personales
y datos derivados; solicitó autorización explícita para interfaz, destinatario y
alcance. **El archivo no fue creado; no se intentó eludir la denegación.**

Pedir autorización específica para una API privada, autenticada, limitada al
propietario y al BFF de su consumer, con scope dedicado y descarga al navegador
o equipo privado del propietario; sin publicación ni entrega a terceros. Esto
no sustituye los trabajos técnicos anteriores ni convierte en pendiente el
permiso ya concedido para copias privadas, correcciones y despliegues.

El NO-GO de calidad sigue por ausencia de examen independiente válido. Ningún
resultado de esta sesión cambia umbrales, promueve política o abre racha.
