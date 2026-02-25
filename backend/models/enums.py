import enum


class UserPlan(str, enum.Enum):
    free = "free"
    premium = "premium"


class RemotePreference(str, enum.Enum):
    remote_only = "remote_only"
    hybrid = "hybrid"
    onsite = "onsite"
    any = "any"
