# Cierre local de correcciones — 2026-09-07

## Estado vigente

**Correcciones implementadas y verificadas localmente. NO DESPLEGADAS.**
Este acta prevalece sobre el estado de bloqueo del editor descrito en
`ESTADO_FIXES_UNIFICACION_2026-09-07_CODEX.md`. Ese informe y sus parches se
conservan como historial, no como instrucciones para volver a aplicarlos.
Base de los cambios: `38c51ab`; el commit que contiene esta acta es el cierre local.

ReactPortfolio continúa utilizable para consultar ofertas y gestionar candidaturas.
Aplicar abre la oferta y registra el estado: el envío efectivo se completa en la
web de la empresa. No se enviaron candidaturas, modificaron preferencias reales,
promovieron modelos ni abrieron exámenes durante esta sesión.

## Correcciones terminadas en código

1. Cambiar exclusiones invalida recuperación y publicación mediante la generación
   existente; una declaración idéntica no reescribe reglas. Migración NUEVA
   `core0042`, sin modificar migraciones publicadas.
2. Entrega durable/versionada del BFF: edición + snapshot se confirman juntos;
   entrega fuera de la transacción; reintentos al arranque y cada 30 s. Una
   entrega vieja no resucita una baja y perder el ACK no exige otra edición.
   Nueva migración BFF `c4d8e2f60a17` sobre `b3c7d1a95e42`.
3. Fallar al guardar el diagnóstico después del commit no convierte el éxito
   local en 500. Una versión adelantada del core queda visible sin ACK falso.
4. Feed vacío válido registra intento y drena recuperación. Se mantiene la
   distinción: un modelo SIN corpus devuelve generación NULL y no registra
   intento; un corpus existente vaciado por filtros sí registra su generación.
5. Presupuesto CE: primer microlote de un documento, tandas pequeñas, deadline
   compartido y proceso terminable/recolectable; publicación cache-only.
   No se activó CE ni se alteró la receta/umbral del ranker.
6. UI de filtros: pending visible incluso tras la última baja; consulta cada
   cinco segundos mientras haya entrega pendiente. El handler de DELETE 204
   ya era correcto: la hipótesis contraria se refutó y NO se cambió ese código.
7. Jobicy sale únicamente del registro de fuentes todavía deshabilitadas;
   conserva su proveedor activo y su política de cosecha.
8. El test ANN controla realmente la entrada al fallback: `CORE_DEDUP_KNN=6`
   sobre cinco elegibles y conteo inyectado. No se cambia el detector ni se
   afirma que el ANN real siempre devuelve 0/5; se prueba la rama exacta.

YAGNI orientó el trabajo a reutilizar generación, locks y persistencia existentes,
sin nuevas dependencias. La separación del proceso CE responde a la necesidad
real de detener inferencia; cancelar un hilo no la cumple.

## Evidencia final, ejecutada en serie

| Comprobación | Resultado |
| --- | --- |
| Suite core completa final | **1073 passed**, 775,29 s |
| Suite BFF completa final | **2288 passed**, 3 skipped, 4 xfailed, 261,81 s |
| Frontend Vitest | **7 passed** |
| Build frontend | Correcto |
| ESLint de los archivos frontend modificados | Correcto |
| `git diff --check` | Correcto |
| Migración BFF con datos sintéticos | Upgrade/seed/baja vacía/guard de downgrade probados |

Las regresiones dirigidas incluyen publicación preparada antes de la exclusión,
rearme, vaciado completo, llegada desordenada, ACK perdido, reinicio de sesión,
fallo simultáneo de transporte y diagnóstico, versión adelantada, timeout y
cancelación de un proceso real. Los tests SQL del CE usan sus motores simulados;
los tests del proceso verifican aparte protocolo, kill y reaping. Esto NO es un
benchmark del modelo real en el J1800.

La primera pasada completa detectó una regresión del parche: registrar intento
sin corpus. Se corrigió en la fuente de ese retorno, conservando la aserción del
test. También detectó la fragilidad del fixture ANN. La nueva prueba de migración
necesitó corregir su propio savepoint: el DDL se ejecuta por la conexión, no por
la sesión ORM. Ninguno de esos rojos se silenció ni se declaró verde.

Persisten warnings ya observados: deprecaciones FastAPI/httpx, escritura de caché
pytest sin permisos y corutinas de dobles de Celery en tests legacy. No se
ocultaron. Los skipped/xfail no cuentan como comprobaciones aprobadas.

Comandos de las pasadas completas:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm core-migrate \
  python -m pytest jobhunt_core/tests -q --tb=short
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --no-deps \
  --entrypoint python backend -m pytest tests/ -q --tb=short
```

Frontend, desde `frontend/`: `./node_modules/.bin/vitest run` y `npm run build`.

## Qué no se ha hecho y qué autorización falta

No se ha modificado el NAS. Su core sigue en la release observada `38c51ab`.
Se verificaron servicios y tamaños de las bases, no se exportó su contenido:

- `swissjobhunter`: 762 MB observados.
- `swissjobhunter_r5_rehearsal`: 2061 MB observados.

La exportación completa para el ensayo fue rechazada por requerir autorización
explícita del payload y destino: contiene cuentas, perfiles, CV y preferencias.
Destino solicitado: `/tmp/swissjob-restore-20260907.q7GxPh/`, directorio LOCAL
privado con permisos 0700. Está vacío; no hay un backup parcial que pueda darse
por válido. Se pidió permiso y queda pendiente de respuesta. No se intentó
sortear el rechazo con otro transporte o destino.

El anterior bloqueo del editor se resolvió mediante parches quirúrgicos de Git:
contextos comprobados antes y contenido verificado después. No se sustituyeron
archivos completos para eludir la revisión automática.

## Secuencia siguiente

1. Autorizar la copia de AMBAS bases al destino privado indicado, solo para
   backup/ensayo. Mantenerlas fuera del repositorio y de cualquier tercero.
2. Restore estricto en destino desechable, con `--exit-on-error`, propietarios y
   permisos correctos; comparar definiciones de constraints/índices/triggers y
   ausencia de constraints sin validar. No basta con contar objetos.
3. Ensayar `core0042` y la migración BFF sobre esas copias, reconciliar snapshots
   y versiones, repetir entrega y verificar rollback sin perder cambios ajenos.
   Ensayar compatibilidad de imágenes: el receptor ahora exige `version` y no
   basta con bajar Alembic. No restaurar una base core compartida en producción
   como atajo de rollback, pues borraría escrituras de otros consumers.
4. Construir desde el commit limpio verificado, sin incluir los cambios ajenos.
   Durante el seed no puede seguir escribiendo un BFF antiguo que no encole
   snapshots. Coordinar migración e imágenes y conservar ruta de recuperación.
5. Canary controlado de alta/baja con comprobación del feed SERVIDO, no solo de
   filas de reglas; observar pending, generación y recuperación. Confirmar que
   producción funciona antes de ampliar o apagar escritores.
6. Continuar E (documentos/colegios) por capacidad con contratos, goldens,
   migración y rollback. Después F: paridad de fuentes, drenaje, backup final y
   retención ratificada. No apagar el global de Portfolio sin inventariar las
   fuentes escolares que también gobierna.

## Deuda y límites de cierre

- Ensayo real y despliegue de estas correcciones: pendientes, no certificados por
  las suites locales.
- E/F: siguen siendo trabajo de unificación; no son un problema del umbral del
  ranker. La retención del legacy aún requiere decisión del propietario.
- CE: benchmark real frío/pico/cambio de CV/reinicio antes de activarlo.
- Calidad: requiere holdout independiente NUEVO; el número invalidado
  `0.5856/0.7438` no es certificación. No se cambian umbrales para obtener verde.
- Una racha requerida de siete días exige 168 h reales elegibles. No sustituye
  los entregables de E/F ni puede certificarse con pruebas aceleradas.

No hay garantía de ausencia absoluta de bugs. Sí hay cierre de las reproducciones
incluidas y suites verdes sobre el código actual. El despliegue continúa sujeto
al ensayo, canary, observabilidad y rollback probado.

Se preservaron los cambios del usuario en `school_job_monitor_architecture.md`
y los cambios ajenos de ReactPortfolio. No se borraron datos, volúmenes ni
artefactos históricos y no queda ninguna suite ejecutándose al cerrar el acta.
