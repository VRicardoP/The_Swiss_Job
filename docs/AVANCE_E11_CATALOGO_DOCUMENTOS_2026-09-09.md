# E.11 — correcciones de catálogo y documentos, sin ampliación funcional

Estado inicial a 2026-09-09; recuperación y continuación el 2026-09-13: trabajo en curso. Este documento no certifica la finalización
de los cinco trabajos ni un GO de calidad. Prevalece sobre la propuesta de nueva
API de exportación integral de E.10: **el propietario la ha descartado**. No es
un permiso pendiente ni una precondición que deba añadirse al proyecto.

## Catálogo ya desplegado: c1924d8 / core0043

El BFF descargaba el catálogo completo para devolver 20 ofertas, total y offset.
Con 32.102 vacantes y un máximo de 100 páginas de 100, la búsqueda general no
solo era lenta: podía fallar sin terminar. Se conserva el contrato existente,
con una petición por página. El core cuenta y selecciona IDs en una sentencia;
hidrata solo la página. Keyset sin offset conserva su representación anterior.

Pruebas rojas en imagen anterior: total ausente/offset ignorado, mezcla de cursor
y offset, recorrido repetido del BFF. Pruebas completas en imágenes c1924d8, en serie:

- Core: 1139 passed, 1 skipped, 864,66 s.
- BFF: 2367 passed, 3 skipped, 4 xfailed, 317,29 s.
- Las omisiones no son pruebas ejecutadas; hay avisos de corutinas en tests legacy.

NAS: core API/worker/capture y BFF c1924d8. Sin migraciones, cambios de routing,
políticas, modelos o productores. Se recargó Nginx tras recrear el BFF para renovar
su resolución DNS. Rollback de imágenes conservado, sin restaurar bases compartidas.

Medición por URL pública :4000, 32.102 ofertas, presupuesto declarado antes:

| Recorrido | n | p50 | p95 | máximo |
|---|---:|---:|---:|---:|
| General, 20 ofertas | 20 | 720,69 ms | 1535,52 ms | 1712,68 ms |
| Segunda página | 3 | 1282,95 ms | 2466,96 ms | 2466,96 ms |

General cumple p95 ≤5 s. **Remoto excedió el timeout de 10 s:** el canary completo
no pasó; no ocultar ese fallo con el resultado de la primera fila. Navegador real:
búsqueda, detalle, enlace al portal, texto python y móvil 390×844 correctos, cero
errores JavaScript. No se envió ninguna candidatura ni se generó contenido real.

## Correcciones posteriores, aún en verificación de release be65fb5

1. Filtro remoto: el plan original descomprime content de 121.829 revisiones,
   incluidas las históricas. NAS: 6.859,67 ms y 227.935 buffers para ese SELECT.
   Se descartó una alternativa LATERAL más lenta (12.819 ms; sin JIT 4.539 ms).
   En copia fiel privada, índice parcial de 1.504 kB: mismo resultado, 1.344,78 ms
   y 226.583 buffers → 15,89 ms y 719 buffers. Migración nueva core0044; literal
   booleano validado para mantener la elegibilidad del índice en planes genéricos.
   **Estas cifras de índice son locales, no rendimiento NAS ya desplegado.**
2. Documentos: 5 de las 20 ofertas iniciales carecen de Job local activo y usan
   UUID core. El enlace externo funciona, pero el generador devolvía 422 porque
   exigía 32 caracteres. f2d3423 acepta UUID canónico y reutiliza el catálogo para
   obtener el contexto sin copiar ofertas a jobs. a91c06e3df72 amplía la referencia
   documental a 36. Downgrade falla cerrado si truncaría referencias guardadas.
3. El importador tenía el mismo límite de 32: 8fe1c9c preserva UUID en snapshot,
   importación, replay y vuelta; regresión roja en el padre. Vuelta contra tablas
   SQL reales incluye una referencia UUID nueva después del corte.

Dirigidas: 44 BFF y 53 core aprobadas. Imágenes be65fb5 construidas desde git archive.
Resultado recuperado el 13-09: BFF **2374 passed, 3 skipped, 4 xfailed**, 331,59 s.
Core: 1138 passed, 1 skipped y 3 fallos por expectativas estáticas core0043 tras
ampliar el esquema. 66010ad corrige únicamente esos tests: comparan el head del
paquete mediante el helper existente. Reejecución dirigida: **86 passed**, incluyendo
esos tres recorridos de migración y la búsqueda literal con el nuevo core0045.
La suite completa de 44fe6b8 y su despliegue siguen pendientes en esta foto.
No activar nueva generación con UUID antes de la migración BFF. No degradar su
columna si contiene referencias core; una imagen vieja no es reversión funcional
completa de esa generación. Migraciones anteriores intactas.

## Continuación de búsqueda textual (13-09, 44fe6b8)

Los filtros de texto también excedían el presupuesto en NAS: python 27,89 s y
consulta sin resultado 42,16 s en el tramo BFF→core. Se sustituye position por
LIKE con escape literal, conservando minúsculas en PostgreSQL y semántica de
%, _, barra y Unicode. No se aumentan timeouts ni se añade un motor de búsqueda.
core0045 añade dos índices GIN usando pg_trgm ya instalado. En copia: 573,39 ms
→45,07 ms, 439.628→1.433 buffers, mismos resultados. Son cifras locales, no NAS.
La prueba de indexabilidad falla en be65fb5; equivalencia literal pasa en ambos.

La recuperación operativa conserva el esquema ampliado: imágenes funcionales
c1924d8 + migraciones actuales, identificadas como variantes distintas, no como
la release c1924d8 original. Readiness core0045 y lectura UUID del ORM anterior
ensayados sobre la copia privada. No ejecutar downgrade ni restauración global.
La generación antigua vuelve a rechazar UUID nuevos; la biblioteca existente
permanece legible. Esta limitación explícita no acredita el corte documental.

## Alcance de trabajo que se conserva

- Documentos: terminar verificación/despliegue compatible, confirmación de erase,
  scopes/entrega y corte histórico reversible. No añadir una API de exportación nueva.
- Colegios: autoridad, preferencias/contacto y estados conservados por consumer.
- Productores/F: portar con paridad antes de retirar, nunca apagar para aparentar cierre.
- Privacidad y backups: corregir el borrado existente y confirmar copias/retención;
  no declarar eliminación remota ni de backups por un DELETE local.
- Rendimiento: acabar filtros y carga representativa; no extrapolar biblioteca
  de dos documentos ni medir una colección vacía como prueba integral.

La topología efectiva importa: captura core lee swissjobhunter_r5_rehearsal,
mientras el BFF público escribe swissjobhunter. Borrar un usuario en la segunda
no es un evento CDC en la primera. El workflow de confirmación remota sigue
abierto; no simular su cierre con un 202 de transporte.

No se ha abierto holdout, cambiado umbrales/promovido ranker ni iniciado racha.
