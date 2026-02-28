"""Scraper for jobs.admin.ch (stelle.admin.ch) — Swiss federal government jobs.

Pure JS SPA — requires Playwright headless browser for rendering.
"""

import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills

logger = logging.getLogger(__name__)

BASE_URL = "https://jobs.admin.ch"


class StelleAdminScraper(BaseScraper):
    SOURCE_NAME = "stelle_admin"
    LISTING_URL = BASE_URL
    RATE_LIMIT_SECONDS = 3.0
    MAX_PAGES = 5
    NEEDS_PLAYWRIGHT = True
    FETCH_DETAILS = False  # Extract from listing page DOM after JS render
    PAGE_SIZE = 20

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{BASE_URL}/?lang=de&page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job cards from JS-rendered DOM.

        After Playwright renders the SPA, the HTML should contain
        job listing elements. Common patterns for government portals:
        .job-card, .vacancy-item, [role="listitem"], .search-result
        """
        stubs: list[dict] = []

        # Strategy 1: structured job cards
        selectors = [
            ".job-card",
            ".vacancy-item",
            ".search-result-item",
            ".job-list-item",
            "[data-job-id]",
            "article",
        ]

        records = []
        for sel in selectors:
            records = soup.select(sel)
            if records:
                break

        # Strategy 2: table rows (common in government portals)
        if not records:
            records = soup.select("table tbody tr")

        for record in records:
            # Title
            title_el = record.select_one("h2, h3, h4, .title, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            # URL
            link_el = record.select_one("a[href]")
            href = link_el.get("href", "") if link_el else ""
            url = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Company / Department
            dept_el = record.select_one(".department, .organization, .employer, .amt")
            company = (
                dept_el.get_text(strip=True)
                if dept_el
                else "Swiss Federal Administration"
            )

            # Location
            loc_el = record.select_one(".location, .ort, .arbeitsort")
            location = loc_el.get_text(strip=True) if loc_el else ""

            # Description snippet
            desc_el = record.select_one(".description, .teaser, p")
            snippet = desc_el.get_text(strip=True) if desc_el else ""

            # Employment rate
            rate_el = record.select_one(".pensum, .workload, .rate")
            employment_type = rate_el.get_text(strip=True) if rate_el else None

            stubs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "url": url,
                    "description": snippet,
                    "employment_type": employment_type,
                }
            )

        # Strategy 3: fallback to links with job-like patterns
        if not stubs:
            seen_urls: set[str] = set()
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if (
                    text
                    and len(text) > 10
                    and any(
                        p in href.lower()
                        for p in ["/job/", "/stelle/", "/vacancy/", "/detail/"]
                    )
                    and href not in seen_urls
                ):
                    seen_urls.add(href)
                    full_url = href if href.startswith("http") else f"{BASE_URL}{href}"
                    stubs.append(
                        {
                            "title": text,
                            "company": "Swiss Federal Administration",
                            "location": "",
                            "url": full_url,
                            "description": "",
                        }
                    )

        return stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Not used — FETCH_DETAILS is False for this scraper."""
        return {}

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Swiss Federal Administration").strip()
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "Bern").strip()

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
