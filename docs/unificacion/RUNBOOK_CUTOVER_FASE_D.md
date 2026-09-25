# RUNBOOK — Cutover Fase D (SwissJob → jobhunt_core)

> **✅ EJECUTADO Y CERRADO el 2026-09-05** (veredicto `SWISSJOB SOBRE CORE`,
> ESTADO §23; confirmado en operación el 06-09 con un ciclo diario completo y
> 0 errores de core en 24 h, §24). Evidencia: `FASE_D_MIGRACION_2026-09-04/`
> (migración + CANARY.md + adenda de release). Este runbook queda como
> procedimiento de referencia y para el rollback; los pasos ya no están
> pendientes. Dos correcciones aprendidas en la ejecución, incorporadas:
> **(1)** tras cada redeploy del core hay que CALENTAR el feed antes de
> servir en `core_primary` (la primera página fría supera el timeout);
> **(2)** `CORE_HTTP_TIMEOUT_SECONDS=120` en el BFF.

> Creado 2026-09-04 (cierre total, Fase 4). **Deriva del runbook del piloto
> (`RUNBOOK_CUTOVER_PILOTO.md`) y REUTILIZA su maquinaria ya probada en
> producción** (routing A.SEAM, GATE C, import/rollback `import_portfolio*`,
> kill-switch por capacidad). Aquí solo se documenta lo ESPECÍFICO de D; para
> cada paso idéntico se referencia el del piloto — no se duplican comandos.
> Datos autoritativos por tabla: `MANIFIESTO_DATOS_SWISSJOB.md` (recontar al
> corte, no copiar).

## La diferencia esencial con el piloto (léela primero)

**El harvest legacy y el CDC SIGUEN VIVOS durante y después de D.** Los 25+
providers/scrapers viven solo en el legacy y el corpus fluye al core por la
sombra (paridad verificada). Lo que D apaga es POR CAPACIDAD Y POR PERFIL:
matching, alertas y notificaciones del motor legacy para cada perfil migrado
(gate anti-doble-motor D.1/D.2, ya desplegado e inerte con `jobhunt_routing`
vacía). NO hay quiesce global de schedulers como en el piloto: el scheduler
de cosecha NO se toca; los de matching/alertas se desactivan por perfil.

Consecuencias operativas:
- El freeze breve de durables aplica igual (35 filas: 18 feedback + 10
  búsquedas + 7 filtros; recontar al corte).
- El «un solo escritor» se verifica POR CAPACIDAD: tras migrar un perfil, su
  matching/notificaciones legacy = OFF y el core es el único escritor de su
  feed; catálogo puede ir a `core_read` global desde el principio.
- Rollback por capacidad: volver un perfil a `local` re-arma SU matching
  legacy (los scores legacy siguen archivados; el motor recomputa).

## Capacidades del routing SwissJob (services/routing.py)

`catalog` · `matching` · `profiles` · `applications` · `documents` ·
`schools`. En D solo se flippean `catalog` y `matching` (+ push de
`profiles` al core vía PUT); `applications`/`saved_searches` migran datos
pero su escritura sigue local hasta que el /v1 exponga escritura (mismo
patrón que el piloto E.3); `documents`/`schools` = LOCAL hasta Fase E.

## Identidades

- Consumer core: `swissjob` (enrollment propio; NO reutilizar la credencial
  del portfolio). Scopes: `vacancies:read profiles:read profiles:write
  matches:read`.
- Perfiles core YA existentes (sombra): P1 = `0b69cfae-41e3-4dd2-971b-1ded69426060`,
  P2 = `680b2f12-9e45-478e-ad60-6762f4b8df28`. El vínculo user→profile se
  puebla en `jobhunt_profile_map` en el canary POR PERFIL (mecanismo
  legítimo, no IDs a mano en tablas de negocio).

## Preflight (paso A del piloto, adaptado)

1. `CORE_CONSUMER_KEY` del BFF SwissJob: **HOY VACÍA** — emitir credencial
   por enrollment con los scopes de arriba ANTES de nada. Verificar scope
   con el PUT no-mutante del piloto (404=ok, 403=falta scope).
2. Red compose común BFF↔core-api (getent hosts core-api).
3. Backup del NAS + **restore PROBADO** sobre copia con RPO/RTO medidos
   (T-PRE-FLIP del piloto aplica igual; si ya se midió para el piloto y
   nada cambió de infraestructura, referenciar esa evidencia con fecha).
4. Recuento REAL de durables al día del corte (no las cifras del 23-08).
5. Estado de schedulers de matching/alertas legacy por perfil (inventario
   de qué se apagará).
6. `jobhunt_routing` y `jobhunt_profile_map`: vacías o con el estado
   esperado; ninguna fila `rollback_pending`.

## Migración de durables (paso C del piloto, manifiesto propio)

- `match_results.feedback` (18): job_hash→vacancy vía `external_id` (mismo
  mecanismo que el oráculo; verificar 18/18 mapeables ANTES de migrar) →
  `profile_vacancy_state`/eventos (thumbs_up/applied/dismissed).
- `saved_searches` (10) + `job_filters` (7) → tuplas canónicas core.
- `match_results` (scores): NO migrar — recomputar (el core ya evalúa).
  Histórico legacy queda ARCHIVADO local.
- `notifications` (1.061): **decisión de propietario pendiente** —
  recomendación del manifiesto: ARCHIVAR local + corte limpio. NO ejecutar
  sin esa decisión.
- `job_applications` = 0 al último censo; si aparecieran, maquinaria §4.
- Idempotencia/rollback: MISMA disciplina del piloto (manifiesto de
  procedencia exacta por PKs, borrado FK-safe probado en ensayo, guards
  LIFO/CASCADE). Fuente sintética: `swissjob-import` (no `portfolio-import`).

## Verificación (paso D del piloto)

Manifiesto de resultados esperados ANTES de migrar (por tabla y clave
transformada) + checksums de columnas materiales; comparar destino contra el
manifiesto, jamás conteos brutos. Feedback: 18/18 + spot-check de los 3
estados.

## Canary y flip (paso E del piloto, por perfil)

Escalera monotónica: `catalog` global a `core_read` → comparación
legacy/core del feed del PRIMER perfil (paridad de contenido, no de scores:
motores distintos — comparar conjunto de vacantes vivas y orden razonable)
→ `matching` de ese perfil a `core_read` → observación (ventana del runbook)
→ apagar SU matching/alertas legacy → `core_primary` de ese perfil →
segundo perfil, ídem → durables a `core_primary` SOLO tras confirmar un
único escritor. GATE C (readiness) antes de cada `core_primary`, con los
hechos del core leídos del core real. Retorno inmediato posible en cada
escalón (set_routing a `local`; el motor legacy de ese perfil se re-arma).

## Post-flip (paso F del piloto)

- Feed de ambos perfiles servido por el core (0 fallbacks en core_read; 0
  503 en core_primary).
- Matching/alertas legacy de los perfiles migrados: OFF verificado (ningún
  `match_results` nuevo post-corte para esos perfiles).
- Harvest legacy + CDC: SIGUEN vivos y sanos (lag del slot, dedup).
- Rutas interactivas, digest, SSE y botón de análisis: contra el core, sin
  leer resultados legacy congelados ni disparar doble evaluación.

## DoD de Fase D

Todos los perfiles destinados al core; routing sin `rollback_pending`;
durables reconciliados con manifiesto; outbox sana; rollback ensayado; cero
motor duplicado por perfil. Veredicto: **SWISSJOB SOBRE CORE**.

## Relación con el freeze de la racha (orden ratificado 2026-09-04)

D se completa y ESTABILIZA antes del freeze de código+receta+modelo+
política+cohortes. La cohorte de la racha = TODOS los perfiles de ambos
consumers ya migrados. Cualquier despliegue posterior que afecte modelo,
receta, cohortes, corpus o scoring REINICIA la racha.
