# Punto 4 — conservar filtros antes del traspaso de avisos

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
