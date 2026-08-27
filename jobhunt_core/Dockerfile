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

# Identidad de la release, horneada en la imagen (auditoría externa 2026-08-27
# P1-3): `__version__` es una constante y no distingue despliegues. Se pasa al
# construir — `RELEASE_SHA=$(git rev-parse --short HEAD) docker compose build ...` —
# y NO se inyecta por `environment:` en runtime, para que no pueda desligarse del
# código que lleva la imagen. La API la publica en /v1/health y /v1/ready.
ARG RELEASE_SHA=unknown
ENV RELEASE_SHA=${RELEASE_SHA}

# Comando por defecto: la API. worker/migrate pasan su propio command en compose.
CMD ["uvicorn", "jobhunt_core.api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# Caché HF escribible: el usuario core tiene HOME=/nonexistent y la descarga
# del modelo fallaba por permisos (decisión delegada 2026-07-28).
ENV HF_HOME=/tmp/hf-cache
