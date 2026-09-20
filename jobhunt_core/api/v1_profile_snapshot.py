"""Durable full snapshots from the SwissJob profile writer, without CDC ordering.

The first accepted snapshot transfers this profile's content/activity authority.
Installing the endpoint alone transfers nothing. Duplicate versions are immutable;
older deliveries only acknowledge the newer version. All checks and mutations share
the profile lock, also held by evaluation publication, erasure and CDC.
"""

import hashlib
import json
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from jobhunt_core import profiles
from jobhunt_core.api.deps import (
    ApiError,
    Principal,
    ensure_json_storable,
    error_404,
    get_session,
    require_scope,
)
from jobhunt_core.api.schemas import ProfileWriteDTO

router = APIRouter(prefix="/v1")
SOURCE_FIELDS = frozenset(profiles.CONTENT_FIELDS) - {"target_roles"}


class SourceContent(ProfileWriteDTO):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def complete_snapshot(self):
        # target_roles is core-side configuration, absent from the BFF schema.
        if self.model_fields_set != SOURCE_FIELDS:
            raise ValueError(
                "source snapshot requires exactly the source profile fields"
            )
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("invalid salary range")
        return self


class ProfileSnapshotWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(gt=0, le=9223372036854775807, strict=True)
    active: StrictBool
    content: SourceContent


@router.put("/profiles/{profile_id}/source-snapshot")
async def put_source_snapshot(
    profile_id: uuid.UUID,
    body: ProfileSnapshotWrite,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("profiles:write")),
):
    payload = body.model_dump(exclude={"version"})
    ensure_json_storable(payload)
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    owner = (
        await session.execute(
            sa.text(
                "SELECT consumer_id, projection_version, projection_hash, projection_active "
                "FROM profiles WHERE id=:p FOR UPDATE"
            ),
            {"p": profile_id},
        )
    ).one_or_none()
    if owner is None or owner.consumer_id != principal.consumer_id:
        raise error_404("perfil")
    if body.version == owner.projection_version and digest != owner.projection_hash:
        raise ApiError(
            409, "profile_version_conflict", "misma version con contenido diferente"
        )
    if body.version > owner.projection_version:
        current = await profiles.current_revision(session, profile_id)
        content = body.content.model_dump()
        content["target_roles"] = (
            current.content.get("target_roles", []) if current else []
        )
        revision_id = await profiles.save_profile_revision(
            session, profile_id, content, allow_empty=True
        )
        await session.execute(
            sa.text(
                "UPDATE profiles SET projection_version=:v, projection_hash=:h, "
                "projection_active=:a WHERE id=:p"
            ),
            {"p": profile_id, "v": body.version, "h": digest, "a": body.active},
        )
        if not body.active or not profiles.build_profile_text(
            profiles.normalize_profile(content, allow_empty=True)
        ):
            # Withdraw only evaluation pointers, never bookmarks/feedback/history.
            await session.execute(
                sa.text(
                    "UPDATE profile_vacancy_state SET current_eval_id=NULL, "
                    "updated_at=GREATEST(updated_at,clock_timestamp()) "
                    "WHERE profile_id=:p AND current_eval_id IS NOT NULL"
                ),
                {"p": profile_id},
            )
        if (
            current is None
            or revision_id != current.id
            or body.active != owner.projection_active
        ):
            # A -> empty/B -> A may reactivate an already evaluated revision.
            # Its old attempt must not suppress reconstruction of the feed.
            await session.execute(
                sa.text("DELETE FROM profile_recovery_state WHERE profile_id=:p"),
                {"p": profile_id},
            )
        version = body.version
    else:
        version = owner.projection_version
    await session.commit()
    return {"version": version}
