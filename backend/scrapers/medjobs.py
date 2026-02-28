"""Scraper for med-jobs.com (redirected from med-jobs.ch) — healthcare jobs in Switzerland.

Cloudflare-protected — requires Playwright headless browser.
"""

import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.med-jobs.com"


class MedJobsScraper(BaseScraper):
    SOURCE_NAME = "medjobs"
    LISTING_URL = f"{BASE_URL}/de/stellenangebote"
    RATE_LIMIT_SECONDS = 3.0  # Conservative — Cloudflare protected
    MAX_PAGES = 10
    NEEDS_PLAYWRIGHT = True  # Cloudflare blocks httpx requests with 403
    FETCH_DETAILS = False  # Extract from Playwright-rendered listing
    PAGE_SIZE = 20

    # Extra headers to avoid 403
    DEFAULT_HEADERS: dict[str, str] = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-CH,de;q=0.9,fr;q=0.8,en;q=0.7",
        "Referer": f"{BASE_URL}/",
        "DNT": "1",
    }

    def build_listing_url(self, page: int, query: str) -> str:
        if page == 1:
            return self.LISTING_URL
        return f"{self.LISTING_URL}?page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job stubs from med-jobs.com listing page."""
        stubs: list[dict] = []

        # Common job listing selectors for medical job portals
        selectors = [
            ".job-item",
            ".stellenangebot",
            ".job-listing",
            "article.job",
            ".search-result-item",
            "tr.job-row",
            ".list-group-item",
        ]

        records = []
        for sel in selectors:
            records = soup.select(sel)
            if records:
                break

        # Fallback: find links with job-like patterns
        if not records:
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if (
                    text
                    and len(text) > 10
                    and any(
                        p in href
                        for p in [
                            "/stelle/",
                            "/job/",
                            "/detail/",
                            "/stellenangebot/",
                            "/vacancy/",
                        ]
                    )
                ):
                    detail_url = (
                        href if href.startswith("http") else f"{BASE_URL}{href}"
                    )
                    stubs.append(
                        {
                            "title": text,
                            "company": "Unknown",
                            "location": "",
                            "detail_url": detail_url,
                            "url": detail_url,
                        }
                    )

        for record in records:
            title_el = record.select_one("h2, h3, h4, .title, .job-title, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title:
                continue

            link_el = record.select_one("a[href]") or title_el
            href = link_el.get("href", "") if link_el.name == "a" else ""
            if not href:
                link_el = record.find("a", href=True)
                href = link_el["href"] if link_el else ""

            detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

            company_el = record.select_one(".company, .employer, .arbeitgeber, .firma")
            company = company_el.get_text(strip=True) if company_el else "Unknown"

            loc_el = record.select_one(".location, .ort, .arbeitsort")
            location = loc_el.get_text(strip=True) if loc_el else ""

            stubs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "detail_url": detail_url,
                    "url": detail_url,
                }
            )

        return stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Extract full details from med-jobs.com detail page."""
        detail: dict = {}

        # Description
        for sel in [
            ".job-description",
            ".stellenbeschreibung",
            ".detail-content",
            "article",
            ".content-main",
        ]:
            desc_el = soup.select_one(sel)
            if desc_el:
                detail["description"] = strip_html_tags(
                    desc_el.get_text(separator="\n", strip=True)
                )
                break

        # Company
        company_el = soup.select_one(".company-name, .arbeitgeber, h2.employer")
        if company_el:
            detail["company"] = company_el.get_text(strip=True)

        # Location
        loc_el = soup.select_one(".arbeitsort, .location, .standort")
        if loc_el:
            detail["location"] = loc_el.get_text(strip=True)

        return detail

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip()
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "Switzerland").strip()

        tags = extract_job_skills(title, description)

        return {
            "hash": self.compute_hash(title, company, url),
            "source": self.SOURCE_NAME,
            "title": title,
            "company": company,
            "location": location,
            "canton": extract_canton(location),
            "description": description,
            "description_snippet": self._snippet(description),
            "url": url,
            "remote": False,
            "tags": tags[: self.MAX_TAGS],
            "logo": None,
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
