"""Provider for publicjobs.ch — public sector and education jobs in Switzerland.

Uses SvelteKit's dehydrated ``__data.json`` endpoint which returns all jobs
in a single request with index-based encoding.
"""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils.text import extract_job_skills

logger = logging.getLogger(__name__)

BASE_URL = "https://www.publicjobs.ch"
DATA_URL = f"{BASE_URL}/jobs/__data.json"


def _dehydrate_sveltekit(raw_json: dict) -> list[dict]:
    """Decode SvelteKit dehydrated __data.json into a list of job dicts.

    SvelteKit stores values in a flat array (``d``) and objects reference
    values by their array index.  The metadata at ``d[0]`` maps named keys
    (like ``jobSearch``) to the index where the corresponding value lives.
    """
    try:
        d = raw_json["nodes"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return []

    meta = d[0]
    if not isinstance(meta, dict):
        return []

    js_idx = meta.get("jobSearch")
    if js_idx is None or js_idx >= len(d):
        return []

    job_search = d[js_idx]
    if not isinstance(job_search, dict):
        return []

    data_ref = job_search.get("data")
    if not isinstance(data_ref, int) or data_ref >= len(d):
        return []

    job_indices = d[data_ref]
    if not isinstance(job_indices, list):
        return []

    jobs: list[dict] = []
    for idx in job_indices:
        if not isinstance(idx, int) or idx >= len(d):
            continue
        obj = d[idx]
        if not isinstance(obj, dict):
            continue

        # Dereference each value: if int and within bounds, follow the index
        decoded: dict = {}
        for key, val in obj.items():
            if isinstance(val, int) and 0 < val < len(d):
                decoded[key] = d[val]
            else:
                decoded[key] = val
        jobs.append(decoded)

    return jobs


class PublicJobsProvider(BaseJobProvider):
    """Fetch public sector jobs from publicjobs.ch SvelteKit JSON endpoint."""

    SOURCE_NAME = "publicjobs"

    async def fetch_jobs(self, query: str, location: str = "Switzerland") -> list[dict]:
        """Fetch all jobs from publicjobs.ch __data.json endpoint."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                response = await self._circuit.call(
                    lambda: client.get(
                        DATA_URL,
                        headers=self.DEFAULT_HEADERS,
                        timeout=20.0,
                    )
                )
            except Exception as e:
                logger.error("publicjobs.ch request failed: %s", e)
                return []

        if response.status_code != 200:
            logger.warning("publicjobs.ch returned HTTP %d", response.status_code)
            return []

        try:
            raw_json = response.json()
        except Exception as e:
            logger.error("publicjobs.ch JSON parse failed: %s", e)
            return []

        decoded_jobs = _dehydrate_sveltekit(raw_json)
        logger.info("publicjobs.ch decoded %d jobs", len(decoded_jobs))

        # Convert to raw dicts for normalize_job
        raw_jobs: list[dict] = []
        for job in decoded_jobs:
            title = job.get("title", "")
            if not title:
                continue

            company = job.get("contactCompany", "") or "Unknown"
            city = job.get("workingAddressCity", "")
            region = job.get("workingAddressRegion", "")
            path = job.get("path", "")

            wl_from = job.get("workloadFrom")
            wl_to = job.get("workloadTo")
            if wl_from and wl_to and wl_from != wl_to:
                employment_type = f"{wl_from}% - {wl_to}%"
            elif wl_from:
                employment_type = f"{wl_from}%"
            else:
                employment_type = None

            raw_jobs.append(
                {
                    "title": title,
                    "company": company,
                    "location": city or region or "Switzerland",
                    "canton": region if len(str(region)) == 2 else None,
                    "url": f"{BASE_URL}{path}" if path else "",
                    "description": "",
                    "employment_type": employment_type,
                    "logo": job.get("contactLogo"),
                    "category": job.get("jobCategory"),
                }
            )

        all_jobs = self._process_raw_jobs(raw_jobs)

        if query:
            q_lower = query.lower()
            results = [
                job
                for job in all_jobs
                if q_lower
                in f"{job['title']} {job['company']} {job.get('description', '')}".lower()
            ]
        else:
            results = all_jobs

        return self._finalize_fetch(results)

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip() or "Unknown"
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "Switzerland").strip()
        canton = raw.get("canton")

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": canton,
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": False,
            "tags": tags[: self.MAX_TAGS],
            "logo": raw.get("logo"),
            "salary_min_chf": None,
            "salary_max_chf": None,
            "salary_original": None,
            "salary_currency": None,
            "salary_period": None,
            "language": None,
            "seniority": None,
            "contract_type": None,
            "employment_type": raw.get("employment_type"),
        }
