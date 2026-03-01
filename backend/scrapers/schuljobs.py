"""Scraper for schuljobs.ch — education and teaching jobs in Switzerland.

SSR HTML listing + JSON-LD (Schema.org JobPosting) on detail pages.
AJAX pagination via /scroll/searchhash/{hash}/page/{N} endpoint.
"""

import asyncio
import json
import logging

import httpx
from bs4 import BeautifulSoup

from config import settings
from services.circuit_breaker import CircuitBreakerOpen
from services.scraper_engine import BaseScraper
from utils.text import extract_canton, extract_job_skills, strip_html_tags

logger = logging.getLogger(__name__)

BASE_URL = "https://www.schuljobs.ch"
SCROLL_URL = f"{BASE_URL}/scroll/searchhash/{{searchhash}}/page/{{page}}"
SCROLL_PAGE_SIZE = 20  # AJAX returns 20 jobs per scroll page
MAX_SCROLL_PAGES = 25  # Up to 500 additional jobs


class SchulJobsScraper(BaseScraper):
    SOURCE_NAME = "schuljobs"
    LISTING_URL = f"{BASE_URL}/suche"
    RATE_LIMIT_SECONDS = 2.0
    MAX_PAGES = 1  # Initial page only; AJAX handles the rest
    NEEDS_PLAYWRIGHT = False
    FETCH_DETAILS = True  # Detail page has JSON-LD with full info
    PAGE_SIZE = 25

    def build_listing_url(self, page: int, query: str) -> str:
        return self.LISTING_URL

    def parse_listing_page(self, soup: BeautifulSoup) -> list[dict]:
        """Extract job stubs from schuljobs.ch listing page.

        Structure (verified 2026-03-01):
          <h3>
            <a class="js-joboffer-detail" href="https://...">Title</a>
          </h3>
          <p>CANTON · City · Company</p>
          <div>Date  Workload%</div>
        """
        stubs: list[dict] = []

        for link in soup.select("a.js-joboffer-detail"):
            title = link.get_text(strip=True)
            href = link.get("href", "")
            if not title or not href:
                continue

            detail_url = href if href.startswith("http") else f"{BASE_URL}{href}"

            # Parse metadata from sibling <p> element
            h3 = link.parent
            card = h3.parent if h3 else None

            company = "Unknown"
            location = ""
            canton = None

            if card:
                p_el = card.find("p")
                if p_el:
                    meta_text = p_el.get_text(strip=True)
                    # Format: "ZH · Zurich · Company Name"
                    parts = [p.strip() for p in meta_text.split("·")]
                    if len(parts) >= 3:
                        canton = (
                            parts[0].strip() if len(parts[0].strip()) == 2 else None
                        )
                        location = parts[1].strip()
                        company = parts[2].strip()
                    elif len(parts) == 2:
                        canton = (
                            parts[0].strip() if len(parts[0].strip()) == 2 else None
                        )
                        location = parts[1].strip()

            stubs.append(
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "canton": canton,
                    "detail_url": detail_url,
                    "url": detail_url,
                }
            )

        return stubs

    async def _scrape_with_httpx(self, query: str) -> list[dict]:
        """Override base httpx scraping to add AJAX scroll pagination.

        Phase 1: Fetch initial /suche page → parse stubs + extract searchhash.
        Phase 2: Loop AJAX /scroll/searchhash/{hash}/page/{N} for more stubs.
        Phase 3: Fetch detail pages (JSON-LD) for all stubs.
        """
        all_stubs: list[dict] = []

        ajax_headers = {
            **self.DEFAULT_HEADERS,
            "X-Requested-With": "XMLHttpRequest",
        }

        async with httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=settings.SCRAPER_HTTPX_TIMEOUT,
        ) as client:
            # Phase 1: initial listing page
            try:
                response = await self._circuit.call(
                    lambda: client.get(self.LISTING_URL)
                )
            except (CircuitBreakerOpen, httpx.HTTPError) as e:
                logger.error("%s initial page error: %s", self.SOURCE_NAME, e)
                return []

            if response.status_code in (403, 429):
                await self._report_block(response.status_code)
                return []
            if response.status_code != 200:
                logger.warning(
                    "%s HTTP %d on initial page", self.SOURCE_NAME, response.status_code
                )
                return []

            soup = BeautifulSoup(response.text, "lxml")
            initial_stubs = self.parse_listing_page(soup)
            all_stubs.extend(initial_stubs)

            # Extract searchhash for AJAX pagination
            result_list = soup.select_one("[data-searchhash]")
            searchhash = result_list.get("data-searchhash") if result_list else None

            # Phase 2: AJAX scroll pages
            if searchhash:
                btn = soup.select_one("[data-nextpage]")
                next_page = int(btn.get("data-nextpage")) if btn else None

                for _ in range(MAX_SCROLL_PAGES):
                    if not next_page:
                        break

                    await asyncio.sleep(self.RATE_LIMIT_SECONDS)
                    scroll_url = SCROLL_URL.format(
                        searchhash=searchhash, page=next_page
                    )

                    try:
                        resp = await self._circuit.call(
                            lambda u=scroll_url: client.get(u, headers=ajax_headers)
                        )
                    except (CircuitBreakerOpen, httpx.HTTPError) as e:
                        logger.error(
                            "%s scroll page %d error: %s",
                            self.SOURCE_NAME,
                            next_page,
                            e,
                        )
                        break

                    if resp.status_code in (403, 429):
                        await self._report_block(resp.status_code)
                        break
                    if resp.status_code != 200:
                        break

                    try:
                        data = resp.json()
                    except Exception:
                        break

                    html_fragment = data.get("html", "")
                    if not html_fragment:
                        break

                    frag_soup = BeautifulSoup(html_fragment, "lxml")
                    page_stubs = self.parse_listing_page(frag_soup)
                    all_stubs.extend(page_stubs)

                    np = data.get("nextpage")
                    next_page = int(np) if np else None

                    if len(page_stubs) < SCROLL_PAGE_SIZE:
                        break
            else:
                logger.warning(
                    "%s: no searchhash found, skipping pagination", self.SOURCE_NAME
                )

            # Phase 3: deduplicate by URL before fetching details
            seen_urls: set[str] = set()
            unique_stubs: list[dict] = []
            for stub in all_stubs:
                url = stub.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    unique_stubs.append(stub)

            logger.info(
                "%s found %d unique stubs (%d before dedup)",
                self.SOURCE_NAME,
                len(unique_stubs),
                len(all_stubs),
            )

            # Phase 4: fetch detail pages for JSON-LD
            if self.FETCH_DETAILS:
                for stub in unique_stubs:
                    detail_url = stub.get("detail_url")
                    if detail_url:
                        await asyncio.sleep(self.RATE_LIMIT_SECONDS)
                        detail = await self._fetch_detail_httpx(client, detail_url)
                        if detail:
                            stub.update(detail)

        logger.info("%s scraped %d raw jobs", self.SOURCE_NAME, len(unique_stubs))
        return unique_stubs

    def parse_job_detail(self, soup: BeautifulSoup) -> dict:
        """Extract full details from JSON-LD on schuljobs.ch detail page.

        Structure (verified 2026-03-01):
          <script type="application/ld+json">
            {"@type": "JobPosting", "title": "...", ...}
          </script>
        """
        detail: dict = {}

        for script in soup.select('script[type="application/ld+json"]'):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
            except json.JSONDecodeError:
                continue

            if data.get("@type") != "JobPosting":
                continue

            # Title (prefer JSON-LD over listing)
            if data.get("title"):
                detail["title"] = data["title"]

            # Company
            org = data.get("hiringOrganization") or {}
            if org.get("name"):
                detail["company"] = org["name"]
            if org.get("logo"):
                detail["logo"] = org["logo"]

            # Location
            loc = data.get("jobLocation") or {}
            addr = loc.get("address") or {}
            if addr.get("addressLocality"):
                detail["location"] = addr["addressLocality"]
            if addr.get("addressRegion"):
                detail["canton"] = addr["addressRegion"]

            # Description
            if data.get("description"):
                detail["description"] = strip_html_tags(data["description"])

            # Employment type
            if data.get("employmentType"):
                detail["employment_type"] = data["employmentType"]

            break  # Only need the first JobPosting

        return detail

    def normalize_job(self, raw: dict) -> dict:
        title = raw.get("title", "").strip()
        company = raw.get("company", "Unknown").strip() or "Unknown"
        url = raw.get("url", "").strip()
        description = raw.get("description", "")
        location = raw.get("location", "Switzerland").strip()
        canton = raw.get("canton")

        if not canton and location:
            canton = extract_canton(location)

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
