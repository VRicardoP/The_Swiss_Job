# E.1 documentos — evidencia y pendientes, 2026-09-08

Código: **18b2a59**. Configuración local de broker: **bf7de9e**.
Contrato y siguiente secuencia: [Fase E documentos](FASE_E_DOCUMENTOS_2026-09-08.md).
**Implementación local verificada; NO desplegada. E/F no están cerradas.**

| Verificación | Resultado ejecutado |
| --- | --- |
| Bloque documentos con PostgreSQL real | 23 passed; 8,50 s |
| Suite core completa, después de los fixes | **1096 passed, 1 skipped**; 796,88 s |
| Único skip, separado con RELEASE_SHA=unknown | **1 passed**; 0,90 s |
| Upgrade core0042→core0043 y downgrade seguro | Incluido; base separada, no NAS |
| diff --check | Limpio |
| NAS, solo lectura | Core API/capture, BFF y frontend healthy; sin despliegue |

Pruebas sobre dependencias de `swissjob-core:d908ea2`, paquete/scripts montados
read-only por `/tmp/swissjob-e-documents-20260908.yml`. El plugin crea/elimina su
base desechable. No es una prueba de imagen E.1 final: antes del despliegue se exige
build del commit limpio, smoke de esa imagen, restore fiel y canary E.2–E.4.
No se ejecutaron dos suites simultáneamente ni se re-resolvieron dependencias.
BFF/frontend no cambiaron: su suite anterior NO se presenta como reejecutada aquí.

## Defectos detectados durante la implementación y corregidos

1. Primera prueba de alta API: 404 antes de implementar la ruta.
2. UUID equivalente con distinta grafía: dos documentos con la misma key y un
   recibo residual tras erase. **Dos tests fallaron**; ahora pasan construyendo
   las rutas de recibos con UUID canónicos, nunca con el path crudo.
3. INSERT confirmado entre guarda de vacío y DROP: podía perderse en downgrade.
   **Un test falló** con `observed=['committed']`; tras el lock previo obtiene
   `['blocked']`. El DDL de la reproducción se revierte dentro del test.
4. Las pruebas reales detectaron UUID enviado al agregado TEXT del outbox;
   la conversión se corrigió antes de la pasada final.

Dos pasadas preliminares se interrumpieron para cerrar los bordes 2/3 (192 y 165
tests pasados hasta la interrupción). NO se cuentan como suites completas. La
pasada final es la de 1096/1. Avisos restantes: deprecación FastAPI/Starlette y
caché pytest read-only. No hubo fallos en esa pasada; no es una garantía de cero
bugs ni un aprobado de la unificación completa.

## Incidencia local: broker recuperado, worker incompatible aislado

Redis `swissjob-redis-core`: exit 255 desde 03-09, restart=no. Worker con errores
DNS hacia redis-core; capture con staging pendiente >7 horas. La comprobación
siguiente FALLÓ antes del fix y PASÓ después, sin imprimir el entorno resuelto:

```sh
set -o pipefail
docker compose config --format json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]["redis-core"]; assert s.get("restart") == "unless-stopped"'
```

Antes de arrancar se compararon credenciales sin imprimirlas y red compartida;
el worker local no tenía destinos HTTP de entrega configurados. El preflight
detectó **worker b98833d/core0035 frente a BD core0041**: NO era seguro conectarlo.
Se detuvo ese worker (exit 0), se actualizó la política del Redis existente y se
arrancó reutilizando `swissjob_redis_core_data`. Redis confirmado
**running/healthy, restart=unless-stopped**. No FLUSH, borrado de volúmenes, cambio
de credenciales ni intervención sobre contenedores NAS.

**Drenaje local PENDIENTE.** No arrancar el worker antiguo sin alinear el entorno:
inventariar imagen/captura/base local, snapshot y restore verificado, elegir una
release coordinada (p. ej. d908ea2/core0042 ya probada, NO E.1 por accidente), migrar
solo la copia/base local prevista y recrear worker/capture compatibles. Verificar
broker, beat, staging drenado y ausencia de errores. Redis recuperado no equivale
a cerrar esta incidencia completa.

## Próximo tramo de unificación

Adaptadores de los dos BFF, recepción de `document.changed`, generación/PDF,
migración con identidades/fechas/hashes y rollback, luego canary y flip por
consumer. La costura actual de documentos incluye fallback de escrituras: no
activar un cliente core sin eliminar esa posibilidad de doble escritor.
Después colegios y retirada legacy, con sus criterios propios. Sin promoción de
modelos, cambio de métricas, nuevo holdout ni racha iniciada en esta entrega.
