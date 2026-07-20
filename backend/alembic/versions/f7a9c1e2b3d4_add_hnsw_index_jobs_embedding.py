"""add HNSW index on jobs.embedding for pgvector ANN search

Revision ID: f7a9c1e2b3d4
Revises: c9f2a7b41e30
Create Date: 2026-07-20

Índice ANN (HNSW) sobre jobs.embedding. El Stage 1 del matching ordena hoy TODO
el catálogo activo por cosine_distance sin índice (full scan). Con HNSW la
búsqueda de vecinos es sublineal. vector_cosine_ops coincide con el operador
cosine_distance que usa el matcher, requisito para que el índice se aplique.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f7a9c1e2b3d4"
down_revision = "c9f2a7b41e30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Índice PARCIAL sobre is_active: el Stage 1 filtra is_active=true, así que el
    # índice solo necesita cubrir esas filas (más pequeño y sin infra-devolver por
    # el post-filtro del ANN). IF NOT EXISTS: idempotente.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_embedding_hnsw "
        "ON jobs USING hnsw (embedding vector_cosine_ops) "
        "WHERE is_active"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_hnsw")
