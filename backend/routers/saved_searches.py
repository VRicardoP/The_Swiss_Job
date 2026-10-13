"""Saved-search endpoints backed by the authoritative Core service."""

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.user import User
from schemas.saved_searches import (
    SavedSearchCreate,
    SavedSearchListResponse,
    SavedSearchResponse,
    SavedSearchUpdate,
)
from services.saved_searches import (
    CoreSavedSearches,
    SearchCoreError,
    block_search_writes,
)


async def _core_call(operation):
    try:
        return await operation
    except SearchCoreError as exc:
        raise HTTPException(exc.status, str(exc)) from None


router = APIRouter(
    prefix="/api/v1/searches",
    tags=["saved_searches"],
    dependencies=[Depends(block_search_writes)],
)


def _store(db, user):
    return CoreSavedSearches(db, user.id)


@router.get("", response_model=SavedSearchListResponse)
async def list_saved_searches(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await _core_call(_store(db, current_user).list(limit, offset))


@router.post(
    "", response_model=SavedSearchResponse, status_code=status.HTTP_201_CREATED
)
async def create_saved_search(
    body: SavedSearchCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    idempotency_key: uuid.UUID | None = Header(default=None, alias="Idempotency-Key"),
):
    values = body.model_dump(mode="json")
    values["filters"] = body.filters.model_dump(exclude_unset=True)
    return await _core_call(
        _store(db, current_user).create(values, idempotency_key or uuid.uuid4())
    )


@router.put("/{search_id}", response_model=SavedSearchResponse)
async def update_saved_search(
    search_id: uuid.UUID,
    body: SavedSearchUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _core_call(
        _store(db, current_user).update(
            search_id,
            body.model_dump(mode="json", exclude_unset=True, exclude_none=True),
        )
    )


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_search(
    search_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _core_call(_store(db, current_user).delete(search_id))


@router.post("/{search_id}/run", response_model=dict)
async def run_saved_search(
    search_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _core_call(_store(db, current_user).run(search_id))
