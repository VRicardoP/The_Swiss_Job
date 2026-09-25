# Manifiesto de datos — Fase D (SwissJob → core) · por tabla

> El entregable documental que D.1 exigía. Censo REAL del NAS: 2026-08-23. Decisión por tabla:
> **MIGRAR** (mover al core con verificación) · **RECOMPUTAR** (el core lo deriva solo) ·
> **CONSERVAR** (se queda en el BFF) · **ARCHIVAR** (histórico local, fuera del camino).
> Regla heredada de PF.3: el estado de usuario NO se pierde ni se duplica.

## El hallazgo que redimensiona Fase D (igual que pasó con el piloto)

**`job_applications = 0`.** Como en el Portfolio, el usuario real no ha usado candidaturas.
La migración de durables de D es MÍNIMA: 18 feedbacks + 10 búsquedas + 7 filtros. El riesgo del
flip vive en el routing de lecturas por perfil y el apagado de schedulers — no en los datos.

## Decisión por tabla

| Tabla | Filas (real) | Decisión | Detalle |
|---|---|---|---|
| `users` | 2 | **CONSERVAR** | Auth/identidad = el BFF. El vínculo al core ya existe: `jobhunt_profile_map` (A.SEAM, hoy vacía — se puebla en el canary por perfil) |
| `user_profiles` | 2 | **CONSERVAR + push** | El CV se sirve LOCAL; push al core vía `PUT /v1/profiles/{pid}` (patrón C-3, exige credencial con `profiles:write`). Los 2 perfiles YA existen en el core (sombra) — el push solo alinea la revisión vigente |
| `match_results` | 2.398 | **RECOMPUTAR** | El core ya evalúa en continuo (`match_evaluations`, miles). NO migrar scores viejos: apples-to-oranges (pipeline distinto). El histórico legacy queda ARCHIVADO local para consulta |
| `match_results.feedback` | 18 | **MIGRAR** | → `profile_vacancy_state`/`events` (thumbs_up/applied/dismissed → estado estable ADR-03). Es EL estado de usuario que PF.3 protege. Mapeo job_hash→vacancy vía `external_id` (mismo mecanismo que el oráculo; los 18 son mapeables — son ofertas con las que interactuó) |
| `job_applications` | **0** | — | Nada que migrar. La maquinaria §4 (ledger/procedencia/rollback) queda probada y disponible si aparecieran antes del flip |
| `generated_documents` | 2 | **CONSERVAR** | LOCAL hasta Fase E (los documentos pasan al core con WeasyPrint en E, no en D) |
| `saved_searches` | 10 | **MIGRAR** | El core tiene tabla propia (core0011). Verificación por tupla canónica (patrón C-4) |
| `job_filters` | 7 | **MIGRAR** | Con `saved_searches` — misma vertical de preferencias de búsqueda |
| `pattern_suggestions` | 0 | — | Vacía. El mecanismo evoluciona a TRACK P post-migración |
| `notifications` | 1.061 (1.060 sin leer) | **ARCHIVAR** ⚠ decisión propietario | Recomendación: NO migrar. 1.060 sin leer ≈ ruido de watchlist (A2-3, bug conocido); `notification_outbox` del core arranca limpio. El plan pedía "decidir migrar/archivar, NO recomputar" — propuesta: archivo local consultable + corte limpio |
| `source_compliance` | 21 | **CONSERVAR** (por ahora) | El harvest sigue siendo LEGACY tras el flip de D (ver "Arquitectura post-D") — compliance sigue operativa donde opera el harvest. Migra en F con el harvest |
| `source_cursors` | 15 | **RECOMPUTAR** (en F) | Cursor por scope del core (`source_scope_state`) se reconstruye al portar cada fuente. NO migrar estado de crawl |
| `source_health` | 34 | **CONSERVAR** | Observabilidad del harvest legacy — vive donde vive el harvest |
| `jobs` (corpus) | ~23k | **YA EN EL CORE** | Vía CDC/sombra desde el 2026-08-06, con paridad verificada (`perdida=0`) |

## Arquitectura post-D — la aclaración que el runbook D necesita

Tras el flip de D, **el legacy NO se apaga entero**: queda como **cosechador + CDC**
(los 25+ providers/scrapers viven SOLO ahí; el core tiene 1 provider portado). Lo que se apaga en
D son los schedulers de **matching/alertas por perfil** (gate anti-doble-motor D.1/D.2, ya en
producción e inerte con `jobhunt_routing` vacía). La cosecha y su compliance/salud siguen legacy
hasta que F porte las fuentes. Consecuencia para el runbook D: el "apagado de schedulers" es POR
CAPACIDAD (matching/notificaciones por perfil), no por proceso.

## Verificación (patrón C-4/§4, ya probado)

- Feedback: conteo 18/18 mapeados + spot-check de los 3 estados (up/down/applied) en
  `profile_vacancy_state`.
- Búsquedas/filtros: tupla canónica por (nombre, filtros normalizados) — checksums cross-BD.
- Sin CDC nuevo: el corpus ya fluye por la sombra; los durables son 35 filas — freeze breve del
  BFF (mono-propietario, mismo argumento que el piloto §0).
