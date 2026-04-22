# ORDER: AUDIT

Usa esta orden cuando quieras pedir una auditoria profunda de cualquier proyecto.

## Alias recomendado

`AUDIT`

## Invocacion canonica

```text
Ejecuta AUDIT
```

## Intencion

Esta orden significa:

"Analiza el proyecto en profundidad usando el repositorio, la documentacion existente, la configuracion, los tests y cualquier skill o workflow disponible del agente. Busca deuda tecnica, errores de logica, bugs, vulnerabilidades, problemas de rendimiento, riesgos de fiabilidad, oportunidades de refactor, tests faltantes y cualquier otro punto relevante para detectar y corregir problemas reales."

## Alcance minimo

- codigo fuente
- configuracion
- tests
- documentacion existente
- workflows o tooling del agente disponibles en el entorno

## Reglas de ejecucion

1. Empezar por la documentacion y contrastarla con la implementacion real.
2. Priorizar hallazgos concretos frente a recomendaciones genericas.
3. Ordenar findings por severidad.
4. Distinguir entre errores confirmados, riesgos probables y huecos de informacion.
5. Incluir impacto y fix recomendado.

## Flujo esperado

1. Revisar documentacion y estructura del repo.
2. Identificar areas de mayor riesgo.
3. Auditar implementacion, configuracion y tests.
4. Devolver hallazgos accionables y priorizados.

## Prompt listo para pegar

```text
ORDER:
AUDIT

CONTEXT:
Analyze this project in depth using the repository, existing documentation, tests, configuration, and any relevant agent skills.

TASK:
Find technical debt, logic errors, bugs, vulnerabilities, performance issues, refactor opportunities, reliability risks, and missing tests. Compare documentation against the actual implementation.

CONSTRAINTS:
- return only concrete findings
- order findings by severity
- include file references, impact, and recommended fixes

OUTPUT:
- findings
- assumptions or missing information
- top fixes
```

## Variantes relacionadas

- Usa `.ai/prompts/project-audit.md` para auditoria profunda general.
- Usa `.ai/prompts/project-audit-production.md` para una auditoria agresiva orientada a produccion.
