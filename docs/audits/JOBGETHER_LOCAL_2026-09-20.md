# Jobgether — preparación nativa, no activación

- Provider, registro de normalización y fecha `createdAt` conectados al core.
- Identidad compatible con legacy: título/empresa/slug sin ObjectId inicial;
  URL pública original y objeto raw conservados. Dos URLs distintas bajo la
  misma identidad en un lote se excluyen como ambiguas, no por orden de llegada.
- Tres páginas son un presupuesto, no evidencia de cobertura: corte sin agotar
  la fuente = parcial. 403/429/503 iniciales no se convierten en vacío exitoso.
- Respuestas, tiempo y tamaño acotados; errores tardíos conservan el prefijo.
- Cinco regresiones nuevas fallaron antes del fix: identidad ambigua, contador
  de página sobredimensionado, slug Unicode, entero salarial grande y registro.
- Verificación dirigida: **22 passed**, incluidos tres providers a través de
  PostgreSQL, admisión 14→7 días y refresh idempotente (1,16 s).
- La descripción ausente permanece ausente: el sink representa `""` como NULL;
  no se fabrica descripción ni fecha para compensar la calidad del portal.

**Límite operativo:** la comprobación pública previa devolvió HTTP 403. No se
ha reintentado eludiendo el bloqueo ni se ha activado/desplegado esta fuente.
No existe evidencia de paridad viva completa para Jobgether. El ensayo local
con respuestas simuladas no autoriza retirar todavía su productor anterior.
La suite completa anterior (1.462) no incluye este delta; no sumarla al resultado
dirigido como si fuera una nueva ejecución integral.
