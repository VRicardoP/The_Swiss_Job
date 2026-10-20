from schemas.documents import (
    DocType,
    DocumentListResponse,
    GenerateDocumentRequest,
    GeneratedDocumentResponse,
)
from schemas.auth import (
    AuthResponse,
    TokenRefresh,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
)
from schemas.job import (
    JobBrief,
    JobCreate,
    JobResponse,
    JobSearchResponse,
)
from schemas.notifications import (
    NotificationListResponse,
    NotificationResponse,
)
from schemas.profile import (
    CVDeleteResponse,
    CVUploadResponse,
    DeleteConfirmation,
    ProfileData,
    ProfileResponse,
    ProfileUpdate,
    UserExport,
)
from schemas.saved_searches import (
    SavedSearchCreate,
    SavedSearchListResponse,
    SavedSearchResponse,
    SavedSearchUpdate,
)

__all__ = [
    "DocType",
    "DocumentListResponse",
    "GenerateDocumentRequest",
    "GeneratedDocumentResponse",
    "AuthResponse",
    "CVDeleteResponse",
    "CVUploadResponse",
    "DeleteConfirmation",
    "JobBrief",
    "JobCreate",
    "JobResponse",
    "JobSearchResponse",
    "NotificationListResponse",
    "NotificationResponse",
    "ProfileData",
    "ProfileResponse",
    "ProfileUpdate",
    "SavedSearchCreate",
    "SavedSearchListResponse",
    "SavedSearchResponse",
    "SavedSearchUpdate",
    "TokenRefresh",
    "TokenResponse",
    "UserExport",
    "UserLogin",
    "UserRegister",
    "UserResponse",
]
