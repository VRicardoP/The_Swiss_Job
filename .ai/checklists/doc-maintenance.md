# Doc Maintenance Checklist

Usa esta lista cuando la tarea sea actualizar documentacion del proyecto.

## Documentacion funcional

- Verificar que el contenido refleje el comportamiento actual del repo.
- Eliminar secciones obsoletas, duplicadas o contradictorias.
- Compactar documentos demasiado largos.
- Reordenar para que primero aparezca lo operativo y despues el detalle.
- Sustituir texto narrativo por listas o bloques reutilizables cuando reduzca tokens.

## Framework de IA local

- Revisar `.ai/context/` y eliminar contexto desactualizado.
- Revisar `.ai/prompts/` y fusionar prompts que hagan casi lo mismo.
- Revisar `.ai/commands/` y asegurar que las ordenes siguen siendo correctas.
- Revisar `.ai/checklists/` para evitar pasos redundantes.
- Revisar `.claude/settings.json` y `.claude/settings.local.json` si hay permisos o flujos que ya no correspondan.
- Revisar `.vscode/settings.json` solo si afecta al flujo operativo.

## Criterio de calidad

Una actualizacion no esta completa si solo anade contenido. Debe dejar el sistema mas claro, mas corto y mas exacto que antes.
