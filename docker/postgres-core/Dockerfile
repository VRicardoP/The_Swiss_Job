# Postgres del proyecto + plugin wal2json (B-01, CONTRATOS_FASE_B.md §2).
# La imagen pgvector NO trae el plugin de logical decoding; test_decoding se
# descarta (parsing de texto frágil con Text/JSONB). El wal_level=logical va
# por `command:` en compose, no aquí: la config no es de la imagen.
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-wal2json \
    && rm -rf /var/lib/apt/lists/*
