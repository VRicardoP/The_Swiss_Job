"""Shared helpers for CH Media job portals (Ostjob, Zentraljob)."""

from services.job_service import BaseJobProvider
from utils.text import extract_canton, extract_job_skills, strip_html_tags


def build_chmedia_url(domain: str, job: dict) -> str:
    """Build job URL from CH Media API response."""
    url = job.get("urlApplication", "")
    if url and not url.startswith("mailto:"):
        return url
    url_desc = job.get("urlDescription", "")
    if url_desc:
        return url_desc
    ext_id = job.get("externalId", "")
    if ext_id:
        return f"https://{domain}/stelle/{ext_id}"
    return f"https://{domain}"


def normalize_chmedia_job(raw: dict, source: str, domain: str) -> dict:
    """Normalize a CH Media API job dict to unified schema."""
    title = raw.get("title", "").strip()
    company_data = raw.get("company", {})
    company = (
        company_data.get("name", "").strip() if isinstance(company_data, dict) else ""
    )
    url = build_chmedia_url(domain, raw)

    city = raw.get("workplaceCity", "")
    cantons = raw.get("cantons", [])
    canton_raw = cantons[0] if cantons else ""
    location_str = (
        f"{city}, {canton_raw}"
        if city and canton_raw
        else city or canton_raw or "Switzerland"
    )

    description = strip_html_tags(raw.get("activity", ""))

    keywords_str = raw.get("keywords", "")
    keywords = (
        [k.strip() for k in keywords_str.split(",") if k.strip()]
        if keywords_str
        else []
    )
    extracted = extract_job_skills(title, description)
    tags = list(dict.fromkeys(keywords + extracted))[: BaseJobProvider.MAX_TAGS]

    is_remote = raw.get("homeOffice", False)

    type_min = raw.get("typeValueMin", 0)
    type_max = raw.get("typeValueMax", 0)
    employment_type = f"{type_min}-{type_max}%" if type_min or type_max else None

    logo_id = company_data.get("logoId", "") if isinstance(company_data, dict) else ""
    logo = f"https://cdn.{domain}/logos/{logo_id}" if logo_id else None

    return {
        "hash": BaseJobProvider.compute_hash(title, company, url),
        "source": source,
        "title": title,
        "company": company,
        "location": location_str,
        "canton": canton_raw if len(canton_raw) == 2 else extract_canton(location_str),
        "description": description,
        "description_snippet": description[: BaseJobProvider.SNIPPET_LENGTH]
        if description
        else None,
        "url": url,
        "remote": is_remote,
        "tags": tags,
        "logo": logo,
        "employment_type": employment_type,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
    }
