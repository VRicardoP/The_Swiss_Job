"""Scraper for myScience.ch — science, research, and academic jobs in Switzerland."""

import logging

from bs4 import BeautifulSoup

from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills

logger = logging.getLogger(__name__)

BASE_URL = "https://www.myscience.ch"


class MyScienceScraper(BaseScraper):
    SOURCE_NAME = "myscience"
    LISTING_URL = f"{BASE_URL}/jobs"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 5
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = True
    PAGE_SIZE = 20

    def build_listing_url(self, page: int, query: str) -> str:
        return f"{self.LISTING_URL}?p={page}"

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job stubs from myScience listing page.

        Real structure (verified 2026-02-28):
          #results_table > div[itemscope]  (schema.org JobPosting)
            a[href]  (wraps entire card)
              .results_title  (job title text)
              .results_organization  (company name)
              .location  (city name)
        """
        stubs: list[dict] = []

        results_table = soup.select_one("#results_table")
        if not results_table:
            return stubs

        for record in results_table.select("div[itemscope]"):
            title_el = record.select_one(".results_title")
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            if not title:
                continue

            link_el = record.select_one("a[href]")
            href = link_el.get("href", "") if link_el else ""
            detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

            org_el = record.select_one(".results_organization")
            company = org_el.get_text(strip=True) if org_el else "Unknown"

            loc_el = record.select_one(".location")
            location = loc_el.get_text(strip=True) if loc_el else ""

            # Logo from listing if present
            logo_el = record.select_one(".centered_logo img")
            logo = None
            if logo_el and logo_el.get("src"):
                src = logo_el["src"]
                logo = src if src.startswith("http") else f"{BASE_URL}{src}"

            stubs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "detail_url": detail_url,
                    "url": detail_url,
                    "logo": logo,
                }
            )

        return stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Extract full details from myScience job detail page.

        Real structure (verified 2026-02-28):
          #middle_content > #results_table
            .employer  (company)
            .location  (city)
            .long_value_row > .descriptor + .long_value  (metadata rows)
            #Description  (full description text)
            .centered_logo img  (logo)
        """
        detail: dict = {}

        container = soup.select_one("#middle_content #results_table")
        if not container:
            container = soup.select_one("#middle_content")
        if not container:
            return detail

        # Description from #Description element
        desc_el = container.select_one("#Description")
        if desc_el:
            detail["description"] = desc_el.get_text(separator="\n", strip=True)

        # Logo
        logo_el = container.select_one(".centered_logo img")
        if logo_el and logo_el.get("src"):
            src = logo_el["src"]
            detail["logo"] = src if src.startswith("http") else f"{BASE_URL}{src}"

        # Metadata from .long_value_row
        for row in container.select(".long_value_row"):
            descriptor = row.select_one(".descriptor")
            value = row.select_one(".long_value")
            if not descriptor or not value:
                continue
            label = descriptor.get_text(strip=True).lower()
            val_text = value.get_text(strip=True)

            if "workplace" in label or "arbeitsort" in label:
                detail["location"] = val_text
            elif "occupation" in label or "pensum" in label or "funktion" in label:
                detail["employment_type"] = val_text

        return detail

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip()
        url = raw.get("url", "").strip()
        description = raw.get("description", "") or raw.get("description_snippet", "")
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
