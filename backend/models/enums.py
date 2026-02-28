import enum


class UserPlan(str, enum.Enum):
    free = "free"
    premium = "premium"


class RemotePreference(str, enum.Enum):
    remote_only = "remote_only"
    hybrid = "hybrid"
    onsite = "onsite"
    any = "any"


class SalaryPeriod(str, enum.Enum):
    yearly = "yearly"
    monthly = "monthly"
    hourly = "hourly"


class Seniority(str, enum.Enum):
    intern = "intern"
    junior = "junior"
    mid = "mid"
    senior = "senior"
    lead = "lead"
    head = "head"
    director = "director"


class ContractType(str, enum.Enum):
    full_time = "full_time"
    part_time = "part_time"
    contract = "contract"
    internship = "internship"
    apprenticeship = "apprenticeship"
    temporary = "temporary"


class MatchFeedback(str, enum.Enum):
    thumbs_up = "thumbs_up"
    thumbs_down = "thumbs_down"
    applied = "applied"
    dismissed = "dismissed"


class ApplicationStatus(str, enum.Enum):
    saved = "saved"
    applied = "applied"
    phone_screen = "phone_screen"
    technical = "technical"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


class NotifyFrequency(str, enum.Enum):
    realtime = "realtime"
    daily = "daily"
    weekly = "weekly"
