# Punto 4 — conservar filtros antes del traspaso de avisos

## Continuación 21-09: circuito de ejecución, todavía SIN corte productivo

Implementación local en verificación:

- `core0050`: autoridad de ejecución explícita por búsqueda + observaciones
  durables; upgrade desde core0049 conserva búsquedas y downgrade se niega
  a borrar estado de ejecución existente, incluso deshabilitado.
- Ejecutor: lock perfil→búsqueda; consulta, observaciones, contadores y outbox
  en una transacción. Reintentos/doble ejecución no duplican avisos. Un commit
  tardío de cosecha no se pierde por adelantar `last_run_at`. Las identidades
  observadas se propagan por cadenas de merge sin volver a notificar.
- Planificador y tarea manual apagados por defecto. Solo ejecutan búsquedas
  transferidas, activas, con dueño activo y destino HTTP real. Una búsqueda
  fallida revierte sus efectos y cede turno sin adelantar su marca de consumo.
- API: alta opt-in del dialecto `swissjob-v1`, lectura individual con ETag,
  ejecución manual autenticada y validación de filtros de búsquedas transferidas.
  Las búsquedas de Portfolio NO se interpretan con este dialecto.
- BFF: adaptación de CRUD/manual al contrato existente, routing fresco por
  capacidad `saved_searches`, sin fallback local en core_primary/rollback_pending.
  Freeze específico de mutaciones y ejecutores legacy; lecturas vivas.
- Inbox: recibo + Notification en la misma transacción; replay idempotente,
  payload validado, SSE solo tras commit y recuperación por historial si falla.

Evidencia dirigida: 41 pruebas del ejecutor/consulta/API previa; 18 de tarea,
migración y ejecución (solapadas); 14 de API incluidas alta opt-in y ejecución
manual; 22 del inbox BFF. Las diez regresiones iniciales del inbox fallaban
antes del fix (ACK sin aviso y mensajes inconsistentes aceptados). La cadena de
merge también se reprodujo roja antes de su corrección. No se enviaron avisos reales.
La suite completa core termina con **1.596 passed**, 2 warnings, **894,22 s**
(`/tmp/point4-search-execution-core-full.log`), commit core `fc7dc70`.
BFF: **70 pruebas dirigidas**; suite completa **2.497 passed, 4 xfailed,
5 warnings**, **381,20 s** (`/tmp/point4-search-bff-full.log`), siempre después
de la suite core. Los warnings corresponden a dos deprecaciones HTTP422 y
tres corrutinas de cosecha simuladas en los tests G4; no al nuevo ejecutor.
Estas cifras verifican código, NO el corte: migración operativa y despliegue
siguen pendientes. La preparación del migrador se verifica separadamente.

Censo READ ONLY en la copia aislada NAS (21-09): diez búsquedas activas, dos
dueños, todas ejecutadas alguna vez, cero nombres duplicados por dueño. Las diez
copias core corresponden unívocamente por perfil/nombre en ESTE snapshot, pero
**0/10 UUID coinciden**; difieren las diez fechas de creación, `last_run_at` y
`total_matches`, y los filtros de una. No exportados valores ni datos personales.
Antes del flip: preservar IDs públicos, reconciliar cada valor autoritativo,
sembrar identidades ya avisadas y demostrar recuperación POST-corte. La ausencia
actual de nombres duplicados NO convierte el nombre en una clave general.

## Checkpoint anterior: filtros y backfill ensayado

Estado: **preparación local probada y ensayo revertido en copia NAS**. No es
el corte de búsquedas/avisos ni de productores. Producción conserva `346fd36`;
ningún scope nativo se ha activado y no se han enviado avisos de prueba.

## Defecto y cambio

El payload de `legacy:*` ya transporta cantón, idioma, seniority, contrato y
salarios CHF, pero su normalizador descartaba esos campos de la canónica.
Una búsqueda ejecutada directamente contra ella perdería filtros reales.

- `normalize.py` conserva esos seis campos opcionales, con validación de tipos.
  No infiere un salario numérico desde texto ni convierte bool/string en importe.
- `legacy_shadow.py` los selecciona del payload que ya existe. No importa código
  del BFF, no modifica migraciones y no cambia `TEXT_FIELDS` ni el embedding.
- `saved_search_query.py` implementa la consulta de filtros SwissJob sobre ofertas
  presentables: FTS simple sobre título/descripción/empresa, SIN coincidencias
  solo por tags; remoto, fuente primaria, cantón, idioma, seniority, contrato y
  solape salarial. Reutiliza el GIN existente como prefiltro y parámetros SQL.
- Tipos o claves desconocidos fallan cerrados. No se interpretan los filtros de
  Portfolio como si fueran los de SwissJob: sus reglas de texto son distintas.

La consulta **todavía no está conectada al planificador ni a un endpoint nuevo**.
No adelanta marcas temporales ni escribe contadores/avisos. Es el componente de
selección que falta para sustituir la lectura de `public.jobs`, no un segundo
motor ya habilitado. El enriquecimiento de los productores nativos todavía debe
conservar los mismos campos antes de su corte; este cambio no lo presupone.

## Evidencia local

- Antes del cambio: 7 fallos por pérdida de metadatos, 16 controles verdes, en
  `test_search_filter_metadata.py`. Después pasan los 23.
- Consulta, validación y replay: **40 pruebas nuevas dirigidas pasan** en total.
  Incluyen PostgreSQL real, exclusión de tag-only/archivadas/encarnaciones
  terminadas, todos los filtros, parámetros hostiles, salario cero, cambio de
  metadatos, reaparición de contenido previo y replay del MISMO raw antiguo.
- El replay crea otra revisión canónica sin duplicar la revisión raw; conserva
  exactamente texto/hash de embedding y es idempotente en la segunda pasada.
- Normalización y canónica preexistentes, junto con los primeros tests nuevos:
  **50 passed** (no sumar esta cifra a 40: hay pruebas solapadas).
- Suite completa con perfil de desarrollo: **1.575 passed**, 2 warnings,
  **978,20 s**; log local `/tmp/point4-search-metadata-full-dev.log`.
  El intento inicial sin `docker-compose.dev.yml` no llegó a recoger dos tests
  por faltar montajes de scripts; no cuenta como validación. No se relajó ningún
  test. Los warnings son Starlette/httpx y el caché de pytest sin permiso de escritura.

Comando reproducible de la suite completa, siempre en serie:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm \
  core-migrate python -m pytest jobhunt_core/tests -q
```

## Ensayo sobre datos reales, aislado y revertido

Copia NAS `swissjob-f-rehearsal-20260919`, base `core_copy`, sin puertos externos.
El código nuevo se monta exclusivamente en un contenedor temporal con CPU/memoria
acotadas; no se reconstruye/recrea ningún servicio productivo. Datos privados no
exportados; solo salen agregados. Caducidad de la copia: 21-09 a las **20:00 UTC**.

Censo de solo lectura de `legacy:workingnomads`, 99 ofertas primarias activas:

| Campo | Presente en raw | Presente en canónica anterior |
|---|---:|---:|
| Idioma | 99 | 0 |
| Seniority | 40 | 0 |
| Tipo de contrato | 20 | 0 |
| Cantón / salario mínimo / salario máximo | 0 cada uno | 0 cada uno |

Ensayo `point4_search_metadata_rehearsal.py`, dentro del directorio privado de
la copia: **99 reparadas**, texto/hash de embedding idénticos, **99 resultados
verificados** con la consulta por idioma/fuente, segunda pasada idempotente y
rollback con restauración exacta de los 99 punteros canónicos iniciales.

El censo global previo agotó el timeout de 45 s (transacción read-only, sin
cambios). Se acotó el ensayo a la fuente del primer traspaso. Estos números NO
representan el corpus completo ni acreditan aún su backfill productivo.

## Continuación necesaria, sin perder comportamiento

1. Portar/validar el enriquecimiento nativo con los mismos datos de entrada, y
   ensayar el backfill de canónicas antes de trasladar lectores con esos filtros.
2. Reconciliar identidades y estado de las 10 búsquedas activas SwissJob. La
   importación histórica genera otros UUID y utiliza nombre: no asumir que el
   nombre es único ni que los valores importados siguen vigentes. Conservar IDs
   visibles, preferencias, marcas, totales e historial de notificaciones.
3. Ejecutar búsquedas en core con contrato explícito por dialecto, propietario
   revalidado y un solo escritor. Deduplicación durable, contadores y evento de
   outbox en la misma transacción; no marcar entregado antes de persistir el aviso.
   Probar crash, reintento, dos ejecutores, edición/borrado concurrentes y commits
   tardíos de cosecha. Una marca temporal adelantada no acredita completitud.
4. Consumir el evento en el inbox BFF de manera idempotente, creando aviso y recibo
   juntos. Conservar el historial anterior. Probar replay y payload discrepante,
   propietario ajeno/borrado, ejecución manual, programación y SSE post-commit.
   No usar correo real para estas pruebas.
5. Solo con ese circuito y recuperación POST-corte demostrados: congelar/drenar el
   ejecutor viejo, migrar, verificar, activar un único ejecutor nuevo y comprobar
   lecturas servidas. Después retomar el corte WorkingNomads preparado, no antes.

Portfolio tiene otro dialecto y su runner local se desactiva cuando la autoridad
de búsquedas es core; no basta con que exista CRUD en `/v1`. Su digest también
depende aún de cachés locales. Ambos pendientes siguen dentro del punto 4.

**Punto 4 abierto.** Punto 5, cron de backups y entrega final no se han iniciado
en este avance. No se cambia la política canónica ni se abre un holdout.
