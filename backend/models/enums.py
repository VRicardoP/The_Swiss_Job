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
