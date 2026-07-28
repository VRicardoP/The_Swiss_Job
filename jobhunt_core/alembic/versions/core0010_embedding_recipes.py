"""Versiona la receta de preprocesado que forma parte del espacio vectorial.

Revision ID: core0010
Revises: core0009
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from jobhunt_core.config import settings

revision: str = "core0010"
down_revision: Union[str, None] = "core0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

S = settings.CORE_DB_SCHEMA


def upgrade() -> None:
    op.add_column(
        "embedding_models",
        sa.Column(
            "recipe_version",
            sa.String(40),
            nullable=False,
            server_default="legacy_v1",
        ),
        schema=S,
    )
    op.drop_constraint(
        "uq_embmodel_name_version", "embedding_models", schema=S, type_="unique"
    )
    op.create_unique_constraint(
        "uq_embmodel_name_version_recipe",
        "embedding_models",
        ["name", "version", "recipe_version"],
        schema=S,
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_embmodel_name_version_recipe",
        "embedding_models",
        schema=S,
        type_="unique",
    )
    # Si existen dos recetas para los mismos pesos, restaurar la unicidad
    # antigua falla de forma segura en vez de borrar una de ellas.
    op.create_unique_constraint(
        "uq_embmodel_name_version",
        "embedding_models",
        ["name", "version"],
        schema=S,
    )
    op.drop_column("embedding_models", "recipe_version", schema=S)
