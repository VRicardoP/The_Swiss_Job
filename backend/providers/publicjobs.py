"""Provider for publicjobs.ch — public sector and education jobs in Switzerland.

Uses SvelteKit's dehydrated ``__data.json`` endpoint which returns all jobs
in a single request with index-based encoding.
"""

import logging

import httpx

from services.job_service import BaseJobProvider
from utils import fetch_diagnostics as diag
from utils.dates import parse_published_at
from utils.http import fetch_with_retry
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
        """Fetch all jobs from publicjobs.ch __data.json endpoint.

        G1/P1-1: era el ÚNICO provider que no pasaba por `fetch_with_retry`
        — todos sus modos de fallo (4xx/5xx/parseo/red) devolvían `[]` sin
        registrar issue y el run salía `empty` (sequía legítima) en vez de
        `error`. Ahora la descarga usa el helper común: reintentos con
        backoff y fallo VISIBLE en fetch_diagnostics (contrato V.0: el None
        de fetch_with_retry es un fetch fallido cuyo issue ya está anotado).
        """
        async with httpx.AsyncClient(follow_redirects=True) as client:
            raw_json = await self._circuit.call(
                lambda: fetch_with_retry(
                    client,
                    DATA_URL,
                    headers=self.DEFAULT_HEADERS,
                    timeout=20.0,
                )
            )

        if raw_json is None:
            return []

        if not isinstance(raw_json, dict):
            # 200 con JSON válido pero no-objeto: estructura desconocida, no
            # "no hay ofertas" — mismo criterio que thehub/financejobs.
            diag.record(
                diag.KIND_NETWORK,
                DATA_URL,
                detail="__data.json no es un objeto JSON: estructura desconocida",
            )
            return []

        decoded_jobs = _dehydrate_sveltekit(raw_json)
        if not decoded_jobs and "nodes" not in raw_json:
            # SvelteKit renombró la raíz: 0 ofertas por estructura ilegible,
            # no una sequía legítima.
            diag.record(
                diag.KIND_NETWORK,
                DATA_URL,
                detail="__data.json sin la clave 'nodes': estructura desconocida",
            )
            return []
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
                    # Fecha de publicación del portal (ISO8601 Z).
                    "public_from": job.get("publicFrom"),
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
            "published_at": parse_published_at(raw.get("public_from")),
        }
