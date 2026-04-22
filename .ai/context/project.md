# Project Context

## Scope

SwissJob es un proyecto con backend Python y frontend Vite/React, con orquestacion local mediante Docker Compose.

## Backend

- Stack probable: Python + FastAPI.
- Codigo principal en `backend/`.
- Capas visibles:
  - `routers/` para endpoints.
  - `services/` para logica de negocio.
  - `providers/` y `scrapers/` para integraciones y extraccion.
  - `models/`, `schemas/`, `tasks/`, `utils/`.
- Tests en `backend/tests/`.
- Dependencias en `backend/requirements.txt`.

## Frontend

- App en `frontend/`.
- Stack visible: Vite + React.
- Entrada en `frontend/src/`.

## Infra local

- `docker-compose.yml` en la raiz.
- Recursos Docker en `docker/`.
- Variables de ejemplo en `.env.example`.

## Prompting Rules

- Enviar solo codigo y logs relevantes al problema actual.
- Pedir primero analisis o plan cuando la tarea no sea trivial.
- Pedir cambios parciales antes de pedir refactors amplios.
- Exigir siempre formato de salida concreto.

## Default Constraints

- Mantener API publica salvo que se indique lo contrario.
- No introducir dependencias nuevas sin justificarlo.
- Si se toca backend, considerar tests existentes.
- Si se toca scraping o providers, cuidar timeouts, retries y normalizacion.
