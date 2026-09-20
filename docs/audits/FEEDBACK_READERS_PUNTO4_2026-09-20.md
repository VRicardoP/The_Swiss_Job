# Punto 4 — consumidores del feedback antes del cambio de escritor

## Defecto reproducido y corrección

El analizador de patrones retenido leía exclusivamente `match_results` local.
Al activar `CORE_FEEDBACK_ENABLED`, las decisiones nuevas del usuario sólo se
guardarían en core: el contador local devolvería cero y el análisis borraría las
sugerencias pendientes sin observar esos rechazos. Se reprodujo antes del fix:
las cuatro pruebas BFF nuevas fallaron; las dos pruebas iniciales core devolvían
404 porque faltaba el contrato de lectura.

La lectura nueva es parte de esa función existente, no una nueva función de UI:

- `GET /v1/profiles/{pid}/feedback-context`, con `matches:read` y ownership.
- Una consulta para el conjunto: identidad, título, empresa, tags y decisión
  efectiva. Incluye historial sin marca (denominador) y ofertas archivadas.
- Observación escolar sin corpus: identidad escolar; vinculada: una única
  identidad de vacante. Reutiliza la resolución temporal de feedback existente,
  incluidas las deselecciones posteriores a un rechazo.
- Más de 50.000 elementos da 503; jamás devuelve una muestra truncada como completa.
- El BFF valida todo el payload y unicidad/forma de identidades. Flag OFF conserva
  lector local; flag ON no tiene fallback local. El análisis obtiene una sola
  foto antes de tocar propuestas; core ausente o respuesta rota no las elimina.

Verificación dirigida: core contexto+feedback 14 passed; contexto+colegios+orden
temporal 10 passed; BFF contexto+analytics+patrones 23 passed. Pruebas con fuentes
externas simuladas y BD local aislada; no prueban un despliegue.

## Otros consumidores retenidos inspeccionados

- Digest y watchlist legacy filtran perfiles por `legacy_owned_sql(matching)`;
  no deben volver a emitir para perfiles con matching core.
- Estado de watchlist escolar omite overlay local con el flag core activo.
- `CoreMatching` y guardados ya seleccionan el escritor activo.
- Generación documental consulta `MatchResult` sólo para skills del matching,
  no para decidir feedback; soporta vacante UUID sin Job local. No cambia aquí.

## Condición operativa

Feedback productivo sigue LOCAL. No liberar el corte hasta suite completa en
serie, imagen limpia, ensayo HTTP de este lector sobre la copia privada y el
plan/apply/readback bajo freeze. La recuperación POST-corte conserva la autoridad
core y las marcas nuevas; `revert` del migrador es sólo PRE-activación.

Ensayo adicional reproducible (sin llamadas de red fuera de la copia):
`scripts/rehearse_feedback_context_prepare.py` crea credencial temporal de lectura
en core_copy; `scripts/rehearse_feedback_context_bff.py` comprueba contador, análisis
y preservación ante caída del core; cerrar revocando la credencial. Exigen los
artefactos privados del ensayo de recuperación anterior, nunca sus copias en git.

Punto 4 permanece abierto: fuentes, scheduler, búsquedas/avisos, extracción escolar
y drenaje final de CDC no se certifican mediante esta corrección.

## Ensayo de volumen antes de desplegar (05:40)

La primera imagen `5631a0e` NO superó el ensayo HTTP sobre la copia: timeout
del BFF. La suite pequeña no representaba las 12.023 filas de historial.
EXPLAIN ANALYZE midió 31.595,6 ms y 12.023 subplanes correlacionados. Las
estadísticas de las cuatro tablas relevantes estaban autoanalizadas.

Comparación consecutiva en la misma copia, misma salida completa:
original 30,796 s; decisión por lote 14,225 s; repetición por lote 10,113 s.
La equivalencia se comprobó sobre todos los campos/filas, no sólo el conteo.
No se muestreó ni eliminó historia para acelerar.

Se extrae una única relación de decisiones compartida por consultas puntuales
y por lote; mismos desempates, deselecciones y vínculos escolares tardíos.
El análisis manual completo tiene presupuesto propio: SQL 40 s, HTTP 45 s,
conexión 5 s. No cambian los timeouts del feed ni de las escrituras. Al agotarlo
se falla explícitamente sin borrar propuestas. No es una medición p95 productiva.

Antes de esa optimización: suite BFF 2.466 passed, 4 xfailed; core 1.505 passed.
Después: 22 pruebas dirigidas core y 14 BFF pasan. Suites finales y ensayo HTTP
de la imagen optimizada pendientes al escribir este checkpoint.
