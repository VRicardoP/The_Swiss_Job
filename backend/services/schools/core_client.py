"""E.15 school catalogue. An unbound instance preserves the rollout boundary."""

from pydantic import BaseModel

from .http_client import SchoolClient
from .port import CoreUnavailableError, SchoolsUnsupportedError


class _SchoolView(BaseModel):
    id: str
    name: str
    city: str
    group_tier: str
    policy: str
    contact_email: str | None
    contact_name: str | None
    template_id: str
    application_url: str | None
    careers_url: str
    notes: str | None


class CoreSchools:
    def __init__(self, *, enabled=False, client=None):
        self._enabled = enabled
        self._client = client or SchoolClient()

    async def list(self):
        if not self._enabled:
            raise SchoolsUnsupportedError("school storage is not bound to the core")
        try:
            items = []
            for monitor in await self._client.monitors():
                data = monitor.settings
                items.append(
                    _SchoolView(
                        id=monitor.external_ref,
                        name=data["name"],
                        city=data.get("city") or "",
                        group_tier=data["group_tier"],
                        policy=data["policy"],
                        contact_email=data.get("contact_email"),
                        contact_name=data.get("contact_name"),
                        template_id=data.get("template_letter") or "A",
                        application_url=data.get("portal_url"),
                        careers_url=data.get("jobs_page_url") or "",
                        notes=data.get("notes"),
                    ).model_dump()
                )
            return {"schools": items}
        except (ValueError, TypeError, KeyError):
            raise CoreUnavailableError("invalid school catalogue") from None
