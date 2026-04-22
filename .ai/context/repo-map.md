# Repo Map

## Root

- `backend/`: API, servicios, scrapers, tareas, tests.
- `frontend/`: UI Vite/React.
- `docker/`: soporte de contenedores.
- `docs/`: documentacion tecnica.
- `docker-compose.yml`: entorno local.
- `.env.example`: variables base.

## Backend hotspots

- `backend/main.py`: arranque de la aplicacion.
- `backend/routers/`: comportamiento HTTP.
- `backend/services/`: reglas de negocio y coordinacion.
- `backend/providers/`: fuentes externas y adaptadores.
- `backend/scrapers/`: scraping especializado.
- `backend/tasks/`: trabajos async o programados.
- `backend/tests/`: cobertura regresiva.

## Frontend hotspots

- `frontend/src/App.jsx`: composicion principal.
- `frontend/src/main.jsx`: bootstrap.
- `frontend/src/index.css`: estilos globales.

## Prompting note

Cuando el problema afecte varias capas, resume las dependencias entre capas antes de pegar codigo.
