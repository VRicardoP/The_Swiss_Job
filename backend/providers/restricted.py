"""Conectores de FUENTES RESTRINGIDAS (jobs.ch/jobup.ch, LinkedIn, Indeed,
Glassdoor, XING).

Estas fuentes no se tratan como "prohibidas para siempre" sino como
**restringidas con ruta de integración autorizable** (ver a.txt §6-7 y
docs/PLAN_STEALTH_SCRAPER_JOBUP.md). Principio no negociable: **NO scraping público
de estos portales**. La única vía de activación es una credencial de partner / feed
oficial / import autorizado.

Arrancan DESHABILITADAS: sin la credencial correspondiente en settings,
`fetch_jobs` devuelve [] con un log `auth_missing` y no realiza ninguna petición.
El registro en `providers/__init__.py` las incluye en `_KEY_REQUIREMENTS`, de modo
que `get_all_providers()` ni siquiera las instancia mientras no haya credencial.
"""

import logging
from typing import Any

from config import settings
from services.job_service import BaseJobProvider

logger = logging.getLogger(__name__)


class RestrictedPartnerProvider(BaseJobProvider):
    """Base de las fuentes restringidas: gate por credencial + esqueleto de mapeo.

    Subclases: definen SOURCE_NAME, CREDENTIAL_ATTR (nombre del setting con la
    credencial) y AUTHORIZED_ROUTE (documenta cómo activarla). Cuando exista la
    credencial, se implementará aquí la llamada al feed/API partner autorizado.
    """

    CREDENTIAL_ATTR: str = ""
    AUTHORIZED_ROUTE: str = ""

    def _credential(self) -> str:
        return getattr(settings, self.CREDENTIAL_ATTR, "") or ""

    async def fetch_jobs(
        self, query: str, location: str = "Switzerland"
    ) -> list[dict]:
        """Sin credencial → auth_missing (0 peticiones). Con credencial → feed partner."""
        if not self._credential():
            logger.info(
                "%s deshabilitado: auth_missing (%s vacío). Ruta autorizada: %s",
                self.SOURCE_NAME,
                self.CREDENTIAL_ATTR,
                self.AUTHORIZED_ROUTE,
            )
            return self._finalize_fetch([])

        # Con credencial presente pero conector aún sin implementar: se deja
        # explícito para no simular datos ni hacer scraping no autorizado.
        logger.warning(
            "%s: credencial presente pero el conector partner no está implementado "
            "todavía. Implementar la llamada al feed autorizado en fetch_jobs.",
            self.SOURCE_NAME,
        )
        return self._finalize_fetch([])

    def normalize_job(self, raw: Any) -> dict:
        """Esqueleto de normalización: mapea un item del feed partner al esquema unificado.

        Se completará con los campos reales del feed cuando se active la fuente.
        """
        title = (raw.get("title") or "").strip()
        company = (raw.get("company") or "").strip()
        url = (raw.get("url") or "").strip()
        description = raw.get("description", "") or ""
        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": raw.get("location"),
            "canton": None,
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": bool(raw.get("remote", False)),
            "tags": [],
            "logo": None,
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": raw.get("language"),
            "seniority": None,
            "contract_type": None,
            "employment_type": None,
        }


class JobCloudPartnerProvider(RestrictedPartnerProvider):
    """jobs.ch / jobup.ch vía JobCloud partner/API/XML oficial. NO scraping público."""

    SOURCE_NAME = "jobcloud_partner"
    CREDENTIAL_ATTR = "JOBCLOUD_PARTNER_API_KEY"
    AUTHORIZED_ROUTE = "JobCloud partner API / XML oficial o import de enlaces del usuario"


class LinkedInAuthorizedProvider(RestrictedPartnerProvider):
    """LinkedIn Jobs vía Talent Solutions / Job Posting API o import de alertas."""

    SOURCE_NAME = "linkedin_authorized"
    CREDENTIAL_ATTR = "LINKEDIN_PARTNER_TOKEN"
    AUTHORIZED_ROUTE = "LinkedIn Talent Solutions / Job Posting API o import manual del usuario"


class IndeedPartnerProvider(RestrictedPartnerProvider):
    """Indeed vía Partner APIs / Job Sync / feed aprobado. NO endpoints internos."""

    SOURCE_NAME = "indeed_partner"
    CREDENTIAL_ATTR = "INDEED_PARTNER_KEY"
    AUTHORIZED_ROUTE = "Indeed Partner APIs / Job Sync / feed aprobado o import de alertas"


class GlassdoorPartnerProvider(RestrictedPartnerProvider):
    """Glassdoor vía API partner (partner id/key). Solo jobs autorizados."""

    SOURCE_NAME = "glassdoor_partner"
    CREDENTIAL_ATTR = "GLASSDOOR_PARTNER_KEY"
    AUTHORIZED_ROUTE = "Glassdoor API partner (canal aprobado; vía Indeed/partner)"


class XingPartnerProvider(RestrictedPartnerProvider):
    """XING vía e-recruiting feed/API o Apply With XING. Requiere partner approval."""

    SOURCE_NAME = "xing_partner"
    CREDENTIAL_ATTR = "XING_PARTNER_TOKEN"
    AUTHORIZED_ROUTE = "XING e-recruiting feed/API (Apply With XING) con partner approval"
