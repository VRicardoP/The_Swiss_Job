"""Saved search endpoints — CRUD + manual run."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.saved_search import SavedSearch
from models.user import User
from schemas.saved_searches import (
    SavedSearchCreate,
    SavedSearchListResponse,
    SavedSearchResponse,
    SavedSearchUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/searches", tags=["saved_searches"])


@router.get("", response_model=SavedSearchListResponse)
async def list_saved_searches(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List user's saved searches."""
    conditions = [SavedSearch.user_id == current_user.id]

    total = (
        await db.execute(
            select(func.count()).select_from(SavedSearch).where(*conditions)
        )
    ).scalar_one()

    stmt = (
        select(SavedSearch)
        .where(*conditions)
        .order_by(SavedSearch.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return SavedSearchListResponse(
        data=[SavedSearchResponse.model_validate(s) for s in rows],
        total=total,
    )


@router.post("", response_model=SavedSearchResponse, status_code=status.HTTP_201_CREATED)
async def create_saved_search(
    body: SavedSearchCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new saved search with notification preferences."""
    search = SavedSearch(
        user_id=current_user.id,
        name=body.name,
        filters=body.filters,
        min_score=body.min_score,
        notify_frequency=body.notify_frequency,
        notify_push=body.notify_push,
    )
    db.add(search)
    await db.commit()
    await db.refresh(search)

    return SavedSearchResponse.model_validate(search)


@router.put("/{search_id}", response_model=SavedSearchResponse)
async def update_saved_search(
    search_id: uuid.UUID,
    body: SavedSearchUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a saved search."""
    search = (
        await db.execute(
            select(SavedSearch).where(
                SavedSearch.id == search_id,
                SavedSearch.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if search is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved search not found",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(search, field, value)

    await db.commit()
    await db.refresh(search)

    return SavedSearchResponse.model_validate(search)


@router.delete("/{search_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_search(
    search_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved search."""
    search = (
        await db.execute(
            select(SavedSearch).where(
                SavedSearch.id == search_id,
                SavedSearch.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if search is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved search not found",
        )

    await db.delete(search)
    await db.commit()


@router.post("/{search_id}/run", response_model=dict)
async def run_saved_search(
    search_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a saved search (bypass scheduler)."""
    search = (
        await db.execute(
            select(SavedSearch).where(
                SavedSearch.id == search_id,
                SavedSearch.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if search is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Saved search not found",
        )

    # Dispatch Celery task
    from celery_app import celery_app

    celery_app.send_task(
        "tasks.search_tasks.run_single_saved_search",
        kwargs={"search_id": str(search_id), "user_id": str(current_user.id)},
    )

    return {"status": "dispatched", "search_id": str(search_id)}
