"""Empty compatibility registry: Core owns every external job source."""

from services.job_service import BaseJobProvider

_PROVIDER_CLASSES: dict[str, type[BaseJobProvider]] = {}


def get_provider(name: str) -> BaseJobProvider | None:
    """Local providers were retired by the DT-144 Core cutover."""
    return None


def get_all_providers() -> list[BaseJobProvider]:
    return []


def get_provider_names() -> list[str]:
    return []


def log_provider_status() -> dict[str, str]:
    return {}
