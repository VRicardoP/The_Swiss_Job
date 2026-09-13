# E.12 — catálogo optimizado y referencias documentales, desplegados

## Estado verificado

13-09-2026. Core API/worker/captura **44fe6b8 / core0045**; BFF SwissJob
**be65fb5 / a91c06e3df72**. API y captura healthy; BFF healthy sin reinicios;
worker responde pong. Nginx recargado tras recrear el BFF. Portfolio y frontend
no cambian de imagen. No se publican repositorios ni se añade una API de exportación.

No es cierre de los cinco trabajos ni GO de calidad. Se conserva la autoridad
documental local y todos los productores. No hay promoción, holdout ni racha.

## Código y pruebas

- c1924d8 (ya desplegado): una petición de catálogo por página, con total/offset
  exactos; ya no descargar el corpus completo para mostrar veinte ofertas.
- be65fb5: índice parcial remoto y compatibilidad con UUID canónico en generación,
  almacenamiento, importación y vuelta documental. Referencia ampliada a 36,
  sin truncar ni crear otra copia de ofertas en jobs.
- 66010ad: tres tests de upgrade comparan el head empaquetado, no core0043 fijo.
- 44fe6b8: búsqueda literal LIKE escapada + dos índices GIN pg_trgm existentes
  en la plataforma. Conserva %, _, barra, Unicode y minúsculas en PostgreSQL.
  Sin nuevos paquetes, nuevos filtros ni incremento de timeouts.

Regresión de indexabilidad roja sobre be65fb5; equivalencia literal verde en
ambas versiones. Dirigidas: **86 passed**, incluidos los tres upgrades corregidos.
Suite final core en imagen inmutable: **1143 passed, 1 skipped**, 936,12 s.
BFF en imagen exacta be65fb5: **2374 passed, 3 skipped, 4 xfailed**, 331,59 s.
Suites en serie; skips y avisos preexistentes no se cuentan como verificaciones.

## El canary encontró una condición operativa adicional

Primera comprobación después del despliegue: navegador correcto, pero remoto
todavía excedía 10 s. General p95 2,37 s. Ese canary **falló** y no se oculta.

EXPLAIN de la sentencia productiva completa: Seq Scan de 124.900 revisiones,
8.646,77 ms, 237.760 buffers. El índice existía y era válido, pero relallvisible
era cero; no había vacuum/analyze registrados en las estadísticas consultadas.
La copia de ensayo no representaba ese estado de mantenimiento del NAS.

Se ejecutó **VACUUM (ANALYZE) exclusivamente de jobhunt.offer_revisions**, sin
FULL, sin borrar datos lógicos, sin cambiar costes globales ni desactivar scans.
Límites: lock_timeout 5 s y statement_timeout 300 s. Resultado posterior:
Index Only Scan, Heap Fetches=0, **158,64 ms**, 1.715 buffers; mismos 13.639
resultados remotos. No hubo que cambiar de nuevo el algoritmo ni la migración.

Lección obligatoria de operación: después de restaurar/crear índices, verificar
el plan real y el mantenimiento de la tabla, no solo indisvalid o el rendimiento
de otra máquina. Los índices parciales no garantizan por sí solos el plan elegido.
Conservar autovacuum y revisar sus estadísticas si vuelve la regresión; no añadir
un cron ni cambiar parámetros globales sin medición que lo justifique.

## Canary final NAS por URL pública, en serie

33.933 ofertas; timeout sin modificar, 10 s. Presupuesto general predeclarado
p95 ≤5 s, veinte lecturas. El navegador terminó antes de esta repetición final.

| Recorrido | n | p50 ms | p95 ms | Máximo ms |
|---|---:|---:|---:|---:|
| General | 20 | 823,60 | 1672,76 | 1724,05 |
| Segunda página | 3 | 772,58 | 775,30 | 775,30 |
| Remoto | 3 | 410,34 | 716,86 | 716,86 |
| Fuente | 3 | 1124,75 | 1229,73 | 1229,73 |
| Texto python | 3 | 229,75 | 281,55 | 281,55 |
| Sin resultados | 1 | 81,57 | 81,57 | 81,57 |
| Offset fuera del catálogo | 1 | 402,79 | 402,79 | 402,79 |

También se verifican total, límite, offset, IDs únicos, has_more y contenido
compatible con cada filtro. Navegador visible: búsqueda, detalle, href al portal
(sin abrirlo ni enviar candidatura), móvil 390×844 y cero pageerror.
Son 34 lecturas acotadas, no certificación de toda carga futura, filtros no
incluidos, generación LLM/PDF o backlog del proyecto completo.

## Integridad y recuperación

Huellas pre/post idénticas (solo hashes, sin perfiles ni secretos): políticas
77b4f8ba262dca8c50799e17c5306abd; modelos f49c2b0e61cb04ceeae8a6426820dbfc;
scopes 75a912536d51e0212d1a21348bfca49e (cero habilitados);
routing BFF 5b68b49fea1f04adcc157bb3d061b3e1.

Copias previas privadas NAS completadas: core 679 MiB, BFF 310 MiB. Directorio
0700 y ficheros 0600, sin subirlos a Git. Estas copias nuevas no se presentan
como restauradas: el ensayo de esquema/recuperación usa la copia fiel existente.

Recuperación ensayada sin downgrade: imágenes con funcionalidad c1924d8 y
migraciones actuales, identificadas separadamente:

- core c1924d8-schema0045: ready autoritativo con core0045 en la copia.
- BFF c1924d8-schema-a91c06e3df72: arranca con a91 y el ORM anterior lee una
  referencia UUID nueva sin truncarla, probado dentro de una transacción revertida.

La recuperación vuelve a rechazar nuevas generaciones con UUID (422); conserva
su lectura. No prometer compatibilidad funcional completa con esa generación.
No ejecutar downgrade ni restore global después de nuevas escrituras.

Artefactos privados: unification-e10.XXkEjw88/e12-documents en el NAS.
Scripts verificados por contenido y SHA antes de ejecutar: deploy 96d3c61b…,
recuperación f7d00464…; paquete 51e11274…. El script de recuperación no se ha
ejecutado en producción. Imágenes activas (config SHA de Docker NAS):

- core: dce97efbb601b47e68ccd4755bbebeb261f12459d3ad48bc53d9262d740aa53d.
- BFF: 12220a41396bc44f9ce532530bd5a8ffa9cccbb9b625d8e329f61a91164256c6.

## Siguiente cierre real

El borrado público elimina en swissjobhunter; CDC escucha
swissjobhunter_r5_rehearsal. No asumir propagación entre ambas. Antes del corte
documental: seguimiento durable del borrado, confirmación remota, protección
contra replay/resurrección, copias y retención; después scopes/entrega,
freeze/drenaje, importación, canary y flip de documentos.

Colegios y sustitución de productores siguen como verticales separadas; no
apagar legacy sin paridad. La API de exportación integral descartada NO forma
parte de esta secuencia. La calidad requiere otro examen independiente válido.
