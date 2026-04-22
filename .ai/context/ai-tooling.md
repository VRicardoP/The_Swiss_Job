# AI Tooling Context

## Goal

Mantener un framework local de trabajo con ChatGPT dentro de VSCodium que sea:

- barato en tokens
- consistente
- reproducible
- facil de mantener a largo plazo

## Current local assets

- `.ai/`: prompts, contexto y checklists manuales.
- `.claude/settings.json`
- `.claude/settings.local.json`
- `.vscode/settings.json`

## Maintenance principle

Cuando se actualice documentacion del proyecto, tambien hay que revisar si la capa de trabajo con IA sigue siendo correcta.

Eso incluye:

- prompts reutilizables
- comandos manuales
- contexto estable del proyecto
- checklists operativas
- permisos o configuraciones de herramientas locales

## Optimization rules

- eliminar prompts redundantes o solapados
- compactar instrucciones largas en bloques reutilizables
- mover contexto estable a archivos de contexto, no repetirlo en cada prompt
- eliminar referencias a rutas, servicios o flujos que ya no existan
- mantener nombres de archivos y ordenes faciles de recordar
