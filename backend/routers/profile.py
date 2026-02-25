"""Profile CRUD: preferences, CV upload, GDPR export/delete."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import get_current_user
from database import get_db
from models.user import User
from schemas.profile import (
    CVDeleteResponse,
    CVUploadResponse,
    DeleteConfirmation,
    ProfileData,
    ProfileResponse,
    ProfileUpdate,
    UserExport,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
async def get_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the authenticated user's complete profile."""
    await db.refresh(current_user, ["profile"])
    profile = current_user.profile
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )
    resp = ProfileResponse.model_validate(profile)
    resp.has_cv_embedding = profile.cv_embedding is not None
    return resp


@router.put("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user profile preferences (partial update)."""
    await db.refresh(current_user, ["profile"])
    profile = current_user.profile
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(profile, field, value)

    await db.commit()
    await db.refresh(profile)

    resp = ProfileResponse.model_validate(profile)
    resp.has_cv_embedding = profile.cv_embedding is not None
    return resp


@router.post("/cv", response_model=CVUploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a CV (PDF or DOCX), parse it, extract skills, trigger embedding."""
    # Validate content type
    if file.content_type not in settings.CV_ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type: {file.content_type}. Allowed: PDF, DOCX",
        )

    # Read and validate size
    file_bytes = await file.read()
    max_bytes = settings.CV_MAX_SIZE_MB * 1024 * 1024
    if len(file_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum: {settings.CV_MAX_SIZE_MB} MB",
        )

    if len(file_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty file",
        )

    # Parse CV
    from services.cv_parser import CVParser

    try:
        raw_text = CVParser.extract_text(file_bytes, file.content_type)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to parse file: {e}",
        )

    if not raw_text or len(raw_text.strip()) < 50:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract meaningful text from the file",
        )

    cleaned_text = CVParser.clean_text(raw_text)
    skills = CVParser.extract_skills(cleaned_text)

    # Update profile
    await db.refresh(current_user, ["profile"])
    profile = current_user.profile
    profile.cv_text = cleaned_text
    # Merge extracted skills with existing ones
    existing_skills = set(profile.skills or [])
    existing_skills.update(skills)
    profile.skills = sorted(existing_skills)

    await db.commit()

    # Dispatch embedding generation task
    task_id = None
    try:
        from tasks.embedding_tasks import generate_profile_embedding

        result = generate_profile_embedding.delay(str(current_user.id))
        task_id = result.id
    except Exception:
        logger.warning("Failed to dispatch embedding task for user %s", current_user.id)

    return CVUploadResponse(
        message="CV uploaded and parsed successfully",
        cv_text_length=len(cleaned_text),
        skills_extracted=skills,
        embedding_task_id=task_id,
    )


@router.delete("/cv", response_model=CVDeleteResponse)
async def delete_cv(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete CV text and embedding from the user's profile."""
    await db.refresh(current_user, ["profile"])
    profile = current_user.profile
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Profile not found",
        )

    profile.cv_text = None
    profile.cv_embedding = None
    await db.commit()

    return CVDeleteResponse(message="CV data deleted successfully")


# --- GDPR endpoints (unchanged) ---


@router.get("/export", response_model=UserExport)
async def export_user_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """GDPR data portability: export all user data as JSON."""
    await db.refresh(current_user, ["profile"])

    profile_data = None
    if current_user.profile:
        profile_data = ProfileData.model_validate(current_user.profile)

    return UserExport(
        id=current_user.id,
        email=current_user.email,
        is_active=current_user.is_active,
        plan=current_user.plan.value
        if hasattr(current_user.plan, "value")
        else str(current_user.plan),
        created_at=current_user.created_at,
        last_login=current_user.last_login,
        gdpr_consent=current_user.gdpr_consent,
        gdpr_consent_at=current_user.gdpr_consent_at,
        profile=profile_data,
        exported_at=datetime.now(timezone.utc),
    )


@router.delete("/delete-all", response_model=DeleteConfirmation)
async def delete_all_user_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """GDPR right to erasure: delete user account and all associated data."""
    user_id = current_user.id
    now = datetime.now(timezone.utc)

    await db.delete(current_user)
    await db.commit()

    return DeleteConfirmation(
        message="All user data has been permanently deleted",
        user_id=user_id,
        deleted_at=now,
    )
