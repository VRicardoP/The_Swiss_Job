# ACTA de decisiones — 2026-08-26

> El propietario delegó expresamente: «Las decisiones debes tomarlas tú
> atendiendo a que sean las más óptimas y eficientes para el proyecto.»
> Base: DECISIONES_PENDIENTES_2026-08-26.md (recomendaciones adoptadas con
> los matices indicados). Reversibles salvo indicación.

## D1 — Auth del catálogo en Fase D: FLIP GLOBAL POR COMODÍN
El canary por perfil queda ratificado SOLO para capacidades con identidad
(matching, notificaciones, perfiles, candidaturas). El catálogo es corpus
global sin ownership: su flip es una fila comodín (core_read con
FallbackCatalog primero), mecanismo ya probado en producción en el portfolio.

## D2 — Criterio del GATE: RE-RATIFICADO
- dedup_precision 1.000 sigue VINCULANTE sin cambios.
- dedup_recall: umbral vinculante pasa de 0.90 a **0.40** = el techo
  DEMOSTRADO con los datos almacenados (ANALISIS_TRACK_R_FASE3; las señales
  restantes no existen en los pares históricos del examen congelado; esperar
  apply_url no puede reverdecerlo porque los pares históricos carecen del dato).
- La vía ÚNICA para re-subir el listón: promoción del estrato positivo a
  examen mediante RE-ETIQUETADO CIEGO INDEPENDIENTE (agente de contexto
  limpio sin acceso a las etiquetas existentes, o el propietario) + acta +
  cap de concentración por clúster. Hasta entonces, el recall del estrato se
  publica como fila informativa (cohorte, ya implementado).
- Matiz de prudencia: el cambio de umbral entra con SU PROPIA acta en el
  código (comentario con referencia) y NO altera la mecánica de 7 ciclos.

## D3 — Cotas /v1 (canton/language/seniority/contract_type/salary/sort):
VIGENTES pero NO permanentes. Fase D solo exige core_read (FallbackCatalog
cubre los filtros locales, cero regresión). El modelado estructurado en
content (patrón R.6: fuera de content_hash, backfill 24k) queda como
PREREQUISITO explícito de core_primary del catálogo, post-gate.

## D4 — Legion Health: RATIFICADO duplicate
IPOS-06: listas de estados solapadas = misma oferta; la licencia por estado
afecta a elegibilidad, no a identidad. Al promocionar el estrato a examen se
CAPA la concentración del clúster (≤ ~15% de los positivos del examen).

## D5 — Búsquedas guardadas de SwissJob: CAPACIDAD LOCAL DEL BFF (sin costura)
Su motor (search_tasks: ejecutar búsquedas + last_run_at + notificar) es
escritor local, y las notificaciones son responsabilidad del BFF (plan §9)
— igual que documentos. Un flip del CRUD rompería las alertas en silencio.
Se declara capacidad BFF-local; re-evaluable solo si algún día el core
absorbe la ejecución de búsquedas (decisión de contrato nueva).

## D6 — Los 19 ambiguos del estrato dev: EXCLUIDOS DEFINITIVAMENTE
Para un estrato POSITIVO la pureza manda: un ambiguo etiquetado duplicate
contaminaría el numerador del recall informativo. Se ratifica su exclusión
(el loader ya los excluye) — no se etiquetan ni duplicate ni distinct. Si el
re-etiquetado ciego de la promoción los redescubre y decide, esa decisión
prevalecerá.
