"""Pydantic schemas for saved search endpoints."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from models.enums import NotifyFrequency


class SavedSearchFilters(BaseModel):
    """Contrato EXPLICITO de `filters` (G3/P2-8).

    Espejo del subconjunto de filtros de `CatalogSearchParams`
    (services/catalog/port.py) que ya declara `/api/v1/jobs/search`: mismos
    nombres y mismos tipos. Como `dict` libre se aceptaba `{"source": 123}`,
    `{"canton": ["ZH", "BE"]}` o `{"language": {"$ne": None}}`, y la tarea
    `run_saved_searches` — que consume `source`/`canton` como CSV y `language`
    como cadena — reventaba con AttributeError abortando el barrido de TODOS
    los usuarios, no solo del que guardo la busqueda rota.

    `extra="forbid"`: una clave desconocida falla en la ENTRADA (422 al
    usuario que la escribe) y no en el worker (fallo silencioso de todos).
    """

    model_config = ConfigDict(extra="forbid")

    q: str | None = None
    source: str | None = None
    canton: str | None = None
    language: str | None = None
    seniority: str | None = None
    contract_type: str | None = None
    salary_min: int | None = Field(None, ge=0)
    salary_max: int | None = Field(None, ge=0)
    remote_only: bool = False


class SavedSearchCreate(BaseModel):
    """Request body for POST /api/v1/searches."""

    name: str = Field(..., min_length=1, max_length=200)
    filters: SavedSearchFilters = Field(default_factory=SavedSearchFilters)
    # G3/P3-4 — CAMPO INERTE, aceptado solo por compatibilidad con la UI, que
    # lo sigue enviando y mostrando. La tarea no calcula ningún score: esta
    # búsqueda es un FILTRO sobre el corpus y se notifica cuando hay altas
    # nuevas (decisión de G1/P1-4, documentada en tasks/search_tasks.py). No
    # se aplica como umbral de resultados porque eso silenciaría a quien hoy
    # tiene guardado un 50 pensando que era un score. Retirarlo del contrato
    # exige tocar el frontend, fuera del alcance de esta auditoría.
    min_score: int = Field(0, ge=0, le=100)
    notify_frequency: NotifyFrequency = NotifyFrequency.daily
    notify_push: bool = True


class SavedSearchUpdate(BaseModel):
    """Request body for PUT /api/v1/searches/{id}."""

    name: str | None = Field(None, min_length=1, max_length=200)
    filters: SavedSearchFilters | None = None
    # G3/P3-4 — inerte, ver SavedSearchCreate.min_score.
    min_score: int | None = Field(None, ge=0, le=100)
    notify_frequency: NotifyFrequency | None = None
    notify_push: bool | None = None
    is_active: bool | None = None


class SavedSearchResponse(BaseModel):
    """Single saved search."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    name: str
    # LECTURA TOLERANTE (G3/P2-7 + P2-8): `dict` libre a proposito — las filas
    # ya guardadas con filtros arbitrarios deben seguir leyendose; la
    # validacion estricta vive en SavedSearchCreate/Update (la ENTRADA).
    filters: dict
    min_score: int
    notify_frequency: NotifyFrequency
    notify_push: bool
    is_active: bool
    last_run_at: datetime | None = None
    total_matches: int
    created_at: datetime

    @field_validator("filters", mode="before")
    @classmethod
    def _null_filters_as_empty(cls, v):
        """G3/P2-7: una fila con `'null'::jsonb` llega como None y tumbaba el
        listado ENTERO del usuario (500), sanas incluidas. Se lee como {}."""
        return {} if v is None else v


class SavedSearchListResponse(BaseModel):
    """Paginated saved searches list."""

    data: list[SavedSearchResponse]
    total: int
