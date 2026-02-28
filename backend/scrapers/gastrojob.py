"""Scraper for Gastrojob.ch — hospitality and gastronomy jobs in Switzerland.

TYPO3 CMS with server-rendered HTML. Job data may load via AJAX fragments,
so this scraper also checks for JSON API endpoints.
"""

import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.gastrojob.ch"


class GastrojobScraper(BaseScraper):
    SOURCE_NAME = "gastrojob"
    LISTING_URL = f"{BASE_URL}/stellen"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 5
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = True
    PAGE_SIZE = 20

    def build_listing_url(self, page: int, query: str) -> str:
        if page == 1:
            return self.LISTING_URL
        return f"{self.LISTING_URL}?page={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job stubs from Gastrojob listing.

        Gastrojob uses TYPO3 — look for common job listing patterns:
        .job-item, .job-list-item, article, .teaser, .list-item, tr.job
        """
        stubs: list[dict] = []

        # Strategy 1: look for common job listing selectors
        selectors = [
            ".job-item",
            ".job-list-item",
            ".stellenangebot",
            "article.job",
            ".teaser-job",
            ".list-group-item",
        ]

        records = []
        for sel in selectors:
            records = soup.select(sel)
            if records:
                break

        # Strategy 2: fallback to <a> links with job-like href patterns
        if not records:
            for link in soup.select("a[href]"):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if (
                    text
                    and len(text) > 10
                    and any(p in href for p in ["/stelle/", "/job/", "/detail/"])
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

        # Process records found via selectors
        for record in records:
            title_el = record.select_one("h2, h3, h4, .title, a")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title:
                continue

            # Find the link
            link_el = record.select_one("a[href]") or title_el
            href = link_el.get("href", "") if link_el.name == "a" else ""
            if not href:
                link_el = record.find("a", href=True)
                href = link_el["href"] if link_el else ""

            detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Company
            company_el = record.select_one(".company, .employer, .firma")
            company = company_el.get_text(strip=True) if company_el else "Unknown"

            # Location
            loc_el = record.select_one(".location, .ort, .region")
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
        """Extract full details from a Gastrojob detail page."""
        detail: dict = {}

        # Description: look for common content containers
        for sel in [".job-detail", ".stellenbeschreibung", "article", ".content"]:
            desc_el = soup.select_one(sel)
            if desc_el:
                detail["description"] = strip_html_tags(
                    desc_el.get_text(separator="\n", strip=True)
                )
                break

        # Company from meta or heading
        company_el = soup.select_one(".company-name, .arbeitgeber, h2.company")
        if company_el:
            detail["company"] = company_el.get_text(strip=True)

        # Location
        loc_el = soup.select_one(".arbeitsort, .location")
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
