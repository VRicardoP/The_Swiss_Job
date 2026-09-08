"""E.1 document boundary: preserve source text verbatim; engine fields read-only."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DocType = Literal["cv", "cover_letter"]


class DocumentCreateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doc_type: DocType
    content: str = Field(max_length=1_000_000)
    offer_revision_id: uuid.UUID | None = None
    language: str | None = Field(default=None, max_length=5)
    source_ref: str | None = Field(default=None, max_length=512)
    context: dict = Field(default_factory=dict)
    model_used: str | None = Field(default=None, max_length=100)
    generation_time_ms: int | None = Field(default=None, ge=0, le=2147483647, strict=True)


class DocumentDTO(DocumentCreateDTO):
    id: uuid.UUID
    profile_id: uuid.UUID
    version: int
    async_state: Literal["ready"]
    output_hash: str
    pdf_location: str | None
    created_at: datetime


class DocumentsPageDTO(BaseModel):
    items: list[DocumentDTO]
    next_cursor: str | None = None


class DocumentBatchCreateDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DocumentCreateDTO] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def distinct_types(self):
        if len({item.doc_type for item in self.items}) != len(self.items):
            raise ValueError("one document per type in a generation")
        return self


class DocumentBatchDTO(BaseModel):
    items: list[DocumentDTO]
