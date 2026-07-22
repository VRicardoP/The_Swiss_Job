# jobhunt-core — imagen esbelta (API + worker + migrate; sin Chromium ni ML en A-01).
# Contexto de build: la RAÍZ del repo (ver docker-compose*.yml y .dockerignore raíz,
# que limita el contexto al paquete jobhunt_core).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY jobhunt_core/requirements.txt /app/jobhunt_core/requirements.txt
RUN pip install --no-cache-dir -r /app/jobhunt_core/requirements.txt

# Usuario sin privilegios (revisión externa A-01 #7): nada corre como root.
RUN addgroup --system core && adduser --system --ingroup core core
COPY --chown=core:core jobhunt_core /app/jobhunt_core
USER core

# Comando por defecto: la API. worker/migrate pasan su propio command en compose.
CMD ["uvicorn", "jobhunt_core.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
