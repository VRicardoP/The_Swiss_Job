# ORDER: DOCSYNC

Usa esta orden cuando quieras pedir una actualizacion integral de documentacion y del framework local de trabajo con IA.

## Alias recomendado

`DOCSYNC`

## Intencion

Esta orden significa:

"Actualiza la documentacion relevante del proyecto segun el estado real del repo. Elimina contenido obsoleto, duplicado o de baja utilidad. Reorganiza y compacta los documentos que hayan crecido demasiado. Despues revisa y optimiza todos los prompts, skills manuales, comandos, hooks, checklists, contexto estable y herramientas locales relacionadas con ChatGPT o agentes en este framework para que sigan siendo correctos, eficientes y faciles de mantener."

## Alcance minimo

- `docs/` si aplica
- `README` relevantes si existen
- `.ai/`
- `.claude/`
- `.vscode/`
- cualquier otra capa local de prompts, hooks, workflows o tooling de agentes que exista en el repo

## Reglas de ejecucion

1. No limitarse a anadir texto.
2. Borrar contenido que ya no aplique.
3. Reducir redundancia entre documentos.
4. Compactar instrucciones largas cuando se puedan convertir en plantillas o checklists.
5. Mantener sincronizados documentos funcionales y tooling de IA.
6. Si aparece una herramienta local nueva, incorporarla a la estructura de mantenimiento.

## Flujo esperado

1. Auditar el estado actual del repo y de la documentacion.
2. Detectar huecos, duplicados y contenido obsoleto.
3. Proponer la estructura minima adecuada.
4. Aplicar cambios.
5. Resumir:
   - que se actualizo
   - que se elimino
   - que se simplifico
   - que se optimizo en prompts, skills, hooks o configuraciones

## Prompt listo para pegar

```text
ORDER:
DOCSYNC

CONTEXT:
Audit the repository and perform a full documentation maintenance pass.

TASK:
Update the relevant project documentation and the local AI workflow layer. Remove obsolete content, consolidate duplicated guidance, and optimize documents that have grown inefficient. Also review and optimize all prompts, manual skills, commands, hooks, permissions, and supporting tooling created for efficient ChatGPT usage in this framework.

CONSTRAINTS:
- prefer fewer, clearer files
- delete stale content instead of preserving it by default
- keep the system optimized for token efficiency and reuse
- align every document with the current repo state

OUTPUT:
- updated files
- removed obsolete content
- optimizations applied
- residual gaps if any
```

## Nota

Si en el futuro se crea un sistema real de slash commands, hooks automatizados o skills externos dentro del repo, esta orden debe actualizarse para apuntar a esas rutas concretas.
