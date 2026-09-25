# Fase D — canary de lecturas EJECUTADO (2026-09-04, tarde)

## Estado del routing (verificado por fila Y por comportamiento)

| Capacidad | Alcance | Modo | Verificación |
|---|---|---|---|
| catalog | comodín (global) | core_read | búsqueda «teacher» servida (60 resultados); 0 fallbacks en logs |
| matching | P1 (b4619e85→0b69cfae) | core_read | top-3 del BFF == top-3 de la BD core BYTE A BYTE (81.56/73.98/73.32); 0 fallbacks |
| matching | P2 (49d283b9→680b2f12) | core_read | top del BFF == core menos 1 ítem SIN respaldo local (cota documentada del core_client, verificada: la oferta no existe en jobs legacy); 0 fallbacks |
| profiles/applications/saved_searches/documents/schools | — | local | escritor legacy (por diseño hasta E/flip de escritura) |

`jobhunt_profile_map`: 2 filas (vía external_ref del core, mecanismo legítimo).

## Hallazgo del canary (corregido en la ventana)

Primera página del feed core: ~43 s en FRÍO en el J1800 (calientes 0.3-1.4 s);
el timeout por defecto del cliente (5 s) disparaba el fallback. Corrección:
`CORE_HTTP_TIMEOUT_SECONDS=60` en `.env.prod` + recreación del backend.
Exactamente el tipo de defecto que `core_read`-con-fallback existe para cazar
sin impacto al usuario.

## Motor legacy por perfil

Gate D.1 activo por routing: con `core_read`, `legacy_owns()` = false ⇒ el
scheduler legacy de matching SALTA ambos perfiles y el endpoint /analyze
devuelve 409. Verificación operativa pendiente de la PRÓXIMA corrida diaria
(esperado `skipped_routing = 2` en su resumen) — ventana de observación antes
de `core_primary`.

## Pendiente para cerrar D (DoD)

1. Ventana de observación (≥1 ciclo diario de cosecha+matching) con ambos
   perfiles en `core_read` y 0 fallbacks sostenidos.
2. Flip a `core_primary` (catalog + matching, ambos perfiles) tras la ventana.
3. Verificación post-flip (0 respuestas locales, sin 503; digest/SSE/rutas).
4. Rollback ensayado del routing (set_routing a local + re-armado del motor).
5. Actualización documental + veredicto `SWISSJOB SOBRE CORE`.

## Adenda nocturna (04-09, ~23:55): release 099cf6d desplegada

- Imagen `swissjob-core:099cf6d` construida de árbol limpio (suite
  1045/1045) y desplegada en los 4 servicios core del NAS. Paridad por
  CONTENIDO: 177/177 ficheros de jobhunt_core con sha256 idéntico al árbol
  local (los tar de `docker save` difieren entre versiones de docker — el
  cotejo válido es el contenido); `/v1/health` y `/v1/ready` publican
  `release: 099cf6d, authoritative: true`. Incluye: revalidación atómica
  (1A/1B), watermark + `materialize_all` en beat (06:15), backend onnx y
  onnxruntime.
- **Incidencia**: el I/O de la transferencia/save sobre el J1800 tumbó un
  proceso de postgres (SIGPIPE 21:11; recuperación limpia 21:50, redo
  0.28 s, bases íntegras: jobs 29.879 / evals 65.353). REGLA NUEVA: jamás
  `docker save` en el NAS con la BD viva; transferir solo con `load` desde
  pipe y cotejar por contenido. El canary en core_read absorbió la ventana
  (fallback) — motivo por el que el deploy se hizo ANTES de core_primary.
- Post-deploy: canary sirviendo del core; CDC reanudado desde last_applied,
  slot r5 activo. Deuda F anotada: slot `jobhunt_shadow` viejo INACTIVO en
  prod retiene WAL — retirarlo con decisión en Fase F.

## DoD COMPLETO — veredicto: SWISSJOB SOBRE CORE (2026-09-05, 17:55 CEST)

| Criterio DoD | Evidencia |
|---|---|
| Todos los perfiles al core | catalog comodín + matching P1 y P2 en `core_primary`; servicios resueltos = CoreMatching/CoreCatalog PUROS (sin fallback), feeds 1.655/1.653 + catálogo 60 |
| Checkpoint de ventana | Cadena diaria 05-09 13:35: «etapa de matching OMITIDA — ningún perfil activo es legacy-owned» (forma FUERTE del gate D.1: la etapa ni se despacha; cosecha intacta: 787 fetched/486 embed/dedup ok) |
| Cero fallbacks en la ventana | 0 líneas cayó-a-local/CoreUnavailable en 20 h de logs previas al flip |
| Routing sin rollback_pending | tabla verificada tras el flip |
| Durables reconciliados | manifiesto 18+10+7 (04-09), huella estable |
| Rollback ensayado | 04-09, local⇄core_read en ambas direcciones, motores distinguibles |
| Un solo escritor por capacidad | matching/catalog: core; durables/documents/schools: legacy local hasta E (por diseño) |
| Incidencia de la ventana | ráfaga fría post-redeploy >60 s en core_primary ⇒ timeout a 120 s + CALENTAMIENTO como paso obligatorio del runbook de deploy (regla nueva) |

Deuda colateral anotada (independiente de D): SMTP del alerta-profesor con
534 app-password de Gmail (pre-existente); slot viejo `jobhunt_shadow`
inactivo retiene WAL (Fase F).
