"""Browser request headers preserved from the retiring producers.

Copied values, not the module: the core never imports the BFF runtime. Two
portals answer 403 to a plain client (jobgether.py:49-50 documents it), so
dropping these at handover would turn a working source into a dead one.

`scripts/test_search_handover_contract.py` pins the user agent and language to
`backend/services/scraper_stealth.py`, so the two sides cannot drift apart.

Accept-Encoding is deliberately absent: httpx negotiates the codecs it can
actually decompress. This is not evasion of a block — it is sending the same
request the producer being retired already sends.
"""

_CHROME_MAJOR = "131"

# Named separately so the cross-repo contract test can compare them with the
# retiring side's literals (scraper_stealth.DEFAULT_USER_AGENT / _ACCEPT_LANGUAGE).
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_ACCEPT_LANGUAGE = "de-CH,de;q=0.9,fr;q=0.8,en;q=0.7"

BROWSER_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": DEFAULT_ACCEPT_LANGUAGE,
    "Sec-CH-UA": (
        f'"Chromium";v="{_CHROME_MAJOR}", '
        f'"Google Chrome";v="{_CHROME_MAJOR}", '
        '"Not?A_Brand";v="24"'
    ),
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Linux"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

MAX_PAGES_PARAM = "max_pages"


def page_budget(params, cap):
    """Declared page budget of a scope, or None. Operational, never semantic.

    Sweeping a declared budget counts as a COMPLETE harvest: it is exactly the
    work the scope asked for. Validation happens before any request, so a typo
    is a permanent configuration error instead of a silently truncated sweep.
    """
    from jobhunt_core.harvest.provider import ProviderConfigError

    if MAX_PAGES_PARAM not in params:
        return None
    budget = params[MAX_PAGES_PARAM]
    if type(budget) is not int or not 0 < budget <= cap:
        raise ProviderConfigError(
            f"{MAX_PAGES_PARAM} must be an integer between 1 and {cap}"
        )
    return budget
