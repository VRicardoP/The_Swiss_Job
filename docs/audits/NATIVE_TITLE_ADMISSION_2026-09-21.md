# Punto 4 — preservar la admisión de títulos al transferir productores

Estado: corrección local; **sin activar fuentes nativas ni retirar productores**.
La suite completa iniciada el 20-09 se interrumpió por orden del propietario:
no cuenta como validación. Reanudación autorizada el 21-09; suite completa:
**1.535 passed**, dos avisos, 933,14 s. Código funcional inmóvil durante la suite;
ejecución en serie, base desechable. Registro:
`/tmp/point4-native-title-resumed-full.log`.

## Defecto demostrado y corrección

Sobre el mismo cuerpo público de Remotive, el adaptador y el sink conservaban
identidades y contenido, pero admitían cinco altas que el pipeline SwissJob
excluía por título. No basta la paridad del adaptador para autorizar el corte.

`legacy_title_filter: true` es una política explícita por scope. Se valida antes
de descargar, no se envía al portal, participa en el fingerprint semántico y se
revalida bajo el lock del scope antes de persistir. Ausencia/false mantiene el
comportamiento previo. Se reutilizan los extractores de identidad y la consulta
batcheada de ofertas conocidas: sólo se rechazan ALTAS; se conservan refrescos
por id nativo o URL exacta de la misma fuente. No se modifica el raw.

Las 72 reglas se portan sin importar el backend retirado. Huella de JSON ordenado:
`0ee1f83fd569d622490ad53bb7de71526194f922efcfbd8ad0812acd06e8a883`.
Se contrastó esa huella contra el código ejecutado en **ambos workers NAS**,
no sólo contra el árbol local. No es una nueva política global de relevancia.

## Evidencia

- Antes del fix: ocho casos rojos en `/tmp/point4-native-title-red.log`.
  Incluyen la fuga del parámetro interno y la falta de validación; la divergencia
  real de altas consta además en `/tmp/point4-remotive-title-parity-result.json`.
- Después: **37 passed**, combinación de títulos, ventana, NAV y exclusión de
  scopes; `/tmp/point4-native-title-expanded.log`. Incluye cambio de política
  durante fetch, omisión/false, valores inválidos y huella congelada.
- Copia NAS aislada `core_copy`, mismo cuerpo de 17 filas:
  **6 admitidas, 6 históricas reutilizadas**, canónica completa, segunda pasada
  idempotente, rollback íntegro. Ocho títulos excluidos en el lote completo
  (no confundirlos con las cinco ALTAS recientes de la divergencia inicial).
  Primera aserción del instrumento confundía ambos contadores; se corrigió
  sin cambiar código productivo ni datos y se exigió conservación exacta de
  las seis históricas. Registro: `/tmp/point4-remotive-title-copy-proof.log`.
- Cuerpo público SHA256:
  `0cd7acba69236d6c16354a39512d05cb1522e234c4b28d4bcd17413027a49061`.
  No se redescargó para favorecer el resultado ni se modificó producción.

## Condiciones de despliegue y corte

Completar suite, construir desde commit limpio, verificar imagen/configuración
y drenar worker antes del reemplazo. Declarar filtro y ventana al habilitar
cada fuente cuyo productor anterior aplicaba esas reglas. Los scrapers no heredan
automáticamente la política de los providers: comprobar su flujo real.

R5 sigue teniendo un worker antiguo sin guard de retirada. La preparación de
su reemplazo debe conservar también la ausencia de Jobicy en ese productor;
la imagen pública nueva lo registra. No habilitar una segunda cosecha por el
mero cambio de imagen. La comparación de esquema encontró sólo la tabla de
entrega de perfiles como diferencia entre R5 y público; la candidata de worker
`d89b6ee` es anterior a esa migración. Esto no equivale aún a aceptación desplegada.
