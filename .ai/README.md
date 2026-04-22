# AI Workflow para VSCodium

Estructura local para trabajar con ChatGPT o agentes desde VSCodium con contexto minimo, prompts reproducibles y menor consumo de tokens.

## Objetivo

- Reducir contexto enviado en cada iteracion.
- Forzar prompts estructurados y comparables.
- Separar contexto estable del contexto temporal.
- Facilitar debugging, refactor, optimizacion e infraestructura.

## Estructura

```text
.ai/
  README.md
  commands/
    AUDIT.md
    AUDIT_PROD.md
    docsync.md
  context/
    ai-tooling.md
    project.md
    repo-map.md
  prompts/
    debug.md
    refactor.md
    optimize.md
    infra.md
    python-script.md
    review.md
    test-failure.md
    summary-refresh.md
    task-template.md
    update-docs.md
  checklists/
    doc-maintenance.md
    prompt-checklist.md
    session-reset.md
```

## Uso recomendado

1. Empieza con `.ai/context/project.md` si necesitas reintroducir el proyecto en una conversacion nueva.
2. Copia un prompt de `.ai/prompts/` segun el caso.
3. Pega solo el codigo, error o diff estrictamente necesario.
4. Trabaja por fases: analisis, plan, ejecucion parcial, verificacion.
5. Si la conversacion se ensucia, usa `.ai/prompts/summary-refresh.md` y reinicia chat.

## Orden recomendada

Para mantenimiento integral de documentacion y tooling de IA usa la orden definida en:

- `.ai/commands/docsync.md`

Nombre de la orden:

- `DOCSYNC`

Para auditoria profunda del proyecto usa la orden definida en:

- `.ai/commands/AUDIT.md`

Nombre de la orden:

- `AUDIT`

Invocacion canonica:

```text
Ejecuta AUDIT
```

Para auditoria agresiva orientada a produccion usa la orden definida en:

- `.ai/commands/AUDIT_PROD.md`

Nombre de la orden:

- `AUDIT_PROD`

Invocacion canonica:

```text
Ejecuta AUDIT_PROD
```

Esa orden no solo actualiza documentacion funcional. Tambien obliga a:

- eliminar contenido obsoleto
- compactar y reorganizar documentos que crecen demasiado
- revisar prompts, skills manuales, hooks y configuraciones de soporte
- alinear el framework de trabajo con el estado real del repo

## Regla operativa

No pegues archivos completos salvo que el problema dependa de interacciones amplias. Prioriza funciones, rutas, tests, logs y configuracion minima reproducible.
