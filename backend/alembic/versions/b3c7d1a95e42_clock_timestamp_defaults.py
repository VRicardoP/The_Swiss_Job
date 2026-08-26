"""G3/P1-1: clock_timestamp() como default de las columnas de marca temporal

Revision ID: b3c7d1a95e42
Revises: a1f2e3d4c5b6
Create Date: 2026-08-26

En PostgreSQL `now()` es `transaction_timestamp()`: se congela al ABRIR la
transacción. `jobs.first_seen_at`, `jobs.last_seen_at` y
`match_results.created_at` los pone el server_default, y las transacciones que
los escriben (cosecha por fuente, `run_matching` con el rerank LLM dentro) duran
minutos: las filas nacían fechadas al inicio de la transacción, por debajo de la
marca de agua que las tareas de aviso ya habían guardado, y no se notificaban
jamás.

`clock_timestamp()` es el reloj real en el momento de la evaluación, así que el
desfase se reduce a «INSERT→commit». Es solo `ALTER COLUMN SET DEFAULT`: no
reescribe la tabla, no toca las filas existentes y no bloquea más que un
ACCESS EXCLUSIVE instantáneo sobre el catálogo.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b3c7d1a95e42"
down_revision = "a1f2e3d4c5b6"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("jobs", "first_seen_at"),
    ("jobs", "last_seen_at"),
    ("match_results", "created_at"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT clock_timestamp()"
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT now()")
