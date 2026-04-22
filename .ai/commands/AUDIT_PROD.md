# ORDER: AUDIT_PROD

Usa esta orden cuando quieras pedir una auditoria agresiva orientada a produccion o pre-release.

## Alias recomendado

`AUDIT_PROD`

## Invocacion canonica

```text
Ejecuta AUDIT_PROD
```

## Intencion

Esta orden significa:

"Analiza el proyecto como si fuera a desplegarse en produccion o a exponerse a usuarios reales. Usa el repositorio, la documentacion existente, la configuracion, los tests, la infraestructura y cualquier skill o workflow disponible del agente. Busca bloqueadores de release, vulnerabilidades, fallos de logica, riesgos de fiabilidad, problemas de integridad de datos, debilidades de despliegue, cuellos de botella, tests faltantes y cualquier otra carencia que aumente el riesgo operativo."

## Alcance minimo

- codigo fuente
- configuracion
- tests
- documentacion existente
- infraestructura y despliegue
- workflows o tooling del agente disponibles en el entorno

## Reglas de ejecucion

1. Empezar por la documentacion y verificarla contra la implementacion real.
2. Priorizar rutas criticas y riesgos operativos.
3. Ordenar findings por severidad y radio de impacto.
4. Separar errores confirmados, riesgos probables y safeguards ausentes.
5. Incluir evidencia, impacto en produccion y fix recomendado.

## Flujo esperado

1. Revisar documentacion, arquitectura y configuracion de despliegue.
2. Identificar caminos criticos del sistema.
3. Auditar codigo, tests, config e infraestructura con criterio de produccion.
4. Devolver hallazgos accionables y priorizados.

## Prompt listo para pegar

```text
ORDER:
AUDIT_PROD

CONTEXT:
Audit this project as a production-ready system using the repository, existing documentation, tests, configuration, infrastructure files, and any relevant agent skills.

TASK:
Find release blockers, vulnerabilities, logic flaws, reliability risks, data integrity issues, performance bottlenecks, deployment weaknesses, and missing safeguards. Compare documentation against the actual implementation.

CONSTRAINTS:
- return only concrete findings
- order findings by severity
- include evidence, impact, and recommended fixes

OUTPUT:
- findings
- release blockers
- top fixes before production
- risks needing manual verification
```

## Variante relacionada

- Usa `.ai/prompts/project-audit-production.md` como prompt base para esta orden.
