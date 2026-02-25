from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_current_user
from database import get_db
from models.user import User
from schemas.profile import DeleteConfirmation, ProfileData, UserExport

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


@router.get("/export", response_model=UserExport)
async def export_user_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """GDPR data portability: export all user data as JSON."""
    # Eagerly load profile relationship
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
    """GDPR right to erasure: delete user account and all associated data.

    This permanently deletes the user, their profile, and all related data.
    The cascade delete on the profile FK handles cleaning up related records.
    """
    user_id = current_user.id
    now = datetime.now(timezone.utc)

    await db.delete(current_user)
    await db.commit()

    return DeleteConfirmation(
        message="All user data has been permanently deleted",
        user_id=user_id,
        deleted_at=now,
    )
