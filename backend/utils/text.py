"""Text processing utilities for job data normalization."""

import re

# ---------------------------------------------------------------------------
# Skills / tech tags (case-insensitive matching)
# ---------------------------------------------------------------------------

TECH_TAGS: list[str] = [
    # Programming languages
    "python",
    "javascript",
    "typescript",
    "java",
    "php",
    "ruby",
    "go",
    "rust",
    "c++",
    "c#",
    "swift",
    "kotlin",
    "scala",
    "r",
    # Frontend
    "react",
    "angular",
    "vue.js",
    "next.js",
    "svelte",
    "tailwindcss",
    # Backend / frameworks
    "node.js",
    "django",
    "flask",
    "fastapi",
    "spring",
    "laravel",
    "express",
    "rails",
    "asp.net",
    ".net",
    # Data / ML
    "machine learning",
    "data science",
    "deep learning",
    "nlp",
    "tensorflow",
    "pytorch",
    "pandas",
    "spark",
    # Databases
    "sql",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "elasticsearch",
    "oracle",
    "sqlite",
    # Cloud & DevOps
    "docker",
    "kubernetes",
    "aws",
    "azure",
    "gcp",
    "terraform",
    "ansible",
    "ci/cd",
    "jenkins",
    "github actions",
    # Tools & platforms
    "git",
    "linux",
    "jira",
    "figma",
    "graphql",
    "rest api",
    # Roles / specialties
    "devops",
    "sre",
    "qa",
    "cybersecurity",
    "blockchain",
    "product manager",
    "scrum master",
]

# ---------------------------------------------------------------------------
# Location helpers
# ---------------------------------------------------------------------------

WORLDWIDE_SYNONYMS: set[str] = {
    "worldwide",
    "remote",
    "anywhere",
    "global",
    "n/a",
    "-",
    "various",
    "multiple countries",
    "all regions",
    "international",
    "any location",
    "work from home",
    "wfh",
    "distributed",
    "location independent",
}

COUNTRY_MAPPINGS: dict[str, str] = {
    "usa": "United States",
    "us": "United States",
    "united states": "United States",
    "united states of america": "United States",
    "uk": "United Kingdom",
    "united kingdom": "United Kingdom",
    "great britain": "United Kingdom",
    "de": "Germany",
    "germany": "Germany",
    "deutschland": "Germany",
    "fr": "France",
    "france": "France",
    "ch": "Switzerland",
    "switzerland": "Switzerland",
    "schweiz": "Switzerland",
    "suisse": "Switzerland",
    "svizzera": "Switzerland",
    "at": "Austria",
    "austria": "Austria",
    "österreich": "Austria",
    "it": "Italy",
    "italy": "Italy",
    "italia": "Italy",
    "nl": "Netherlands",
    "netherlands": "Netherlands",
    "es": "Spain",
    "spain": "Spain",
    "españa": "Spain",
    "au": "Australia",
    "australia": "Australia",
    "ca": "Canada",
    "canada": "Canada",
    "br": "Brazil",
    "brazil": "Brazil",
    "in": "India",
    "india": "India",
    "sg": "Singapore",
    "singapore": "Singapore",
    "jp": "Japan",
    "japan": "Japan",
    "ie": "Ireland",
    "ireland": "Ireland",
    "se": "Sweden",
    "sweden": "Sweden",
    "no": "Norway",
    "norway": "Norway",
    "dk": "Denmark",
    "denmark": "Denmark",
    "fi": "Finland",
    "finland": "Finland",
    "pt": "Portugal",
    "portugal": "Portugal",
    "be": "Belgium",
    "belgium": "Belgium",
    "pl": "Poland",
    "poland": "Poland",
    "cz": "Czech Republic",
    "czech republic": "Czech Republic",
    "nz": "New Zealand",
    "new zealand": "New Zealand",
}

# Swiss cantons: maps name variants (DE/FR/IT/EN, lowercase) → 2-letter code
SWISS_CANTONS: dict[str, str] = {
    # Zürich
    "zurich": "ZH",
    "zürich": "ZH",
    "zh": "ZH",
    # Bern
    "bern": "BE",
    "berne": "BE",
    "be": "BE",
    # Luzern
    "luzern": "LU",
    "lucerne": "LU",
    "lu": "LU",
    # Uri
    "uri": "UR",
    "ur": "UR",
    # Schwyz
    "schwyz": "SZ",
    "sz": "SZ",
    # Obwalden
    "obwalden": "OW",
    "ow": "OW",
    # Nidwalden
    "nidwalden": "NW",
    "nw": "NW",
    # Glarus
    "glarus": "GL",
    "gl": "GL",
    # Zug
    "zug": "ZG",
    "zg": "ZG",
    # Fribourg
    "fribourg": "FR",
    "freiburg": "FR",
    # Solothurn
    "solothurn": "SO",
    "so": "SO",
    # Basel-Stadt
    "basel-stadt": "BS",
    "basel": "BS",
    "bs": "BS",
    "bâle": "BS",
    # Basel-Landschaft
    "basel-landschaft": "BL",
    "bl": "BL",
    # Schaffhausen
    "schaffhausen": "SH",
    "sh": "SH",
    # Appenzell Ausserrhoden
    "appenzell ausserrhoden": "AR",
    "ar": "AR",
    # Appenzell Innerrhoden
    "appenzell innerrhoden": "AI",
    # St. Gallen
    "st. gallen": "SG",
    "st.gallen": "SG",
    "sg": "SG",
    "saint-gall": "SG",
    # Graubünden
    "graubünden": "GR",
    "graubunden": "GR",
    "grisons": "GR",
    "gr": "GR",
    # Aargau
    "aargau": "AG",
    "argovie": "AG",
    "ag": "AG",
    # Thurgau
    "thurgau": "TG",
    "thurgovie": "TG",
    "tg": "TG",
    # Ticino
    "ticino": "TI",
    "tessin": "TI",
    "ti": "TI",
    # Vaud
    "vaud": "VD",
    "waadt": "VD",
    "vd": "VD",
    # Valais
    "valais": "VS",
    "wallis": "VS",
    "vs": "VS",
    # Neuchâtel
    "neuchâtel": "NE",
    "neuchatel": "NE",
    "neuenburg": "NE",
    "ne": "NE",
    # Genève
    "genève": "GE",
    "geneve": "GE",
    "geneva": "GE",
    "genf": "GE",
    "ge": "GE",
    # Jura
    "jura": "JU",
    "ju": "JU",
}


def strip_html_tags(text: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_job_skills(title: str, description: str) -> list[str]:
    """Extract skills/technologies mentioned in job title and description.

    Returns at most 15 unique skills.
    """
    found: list[str] = []
    combined = f"{title} {description}".lower()
    for tag in TECH_TAGS:
        if tag.lower() in combined and tag not in found:
            found.append(tag)
    return found[:15]


def process_job_location(location: str) -> str:
    """Standardize job location names to a canonical form."""
    if not location or not isinstance(location, str):
        return "Unknown"
    stripped = location.strip()
    if not stripped or stripped.lower() in WORLDWIDE_SYNONYMS:
        return "Remote / Worldwide"
    loc_lower = stripped.lower()
    return COUNTRY_MAPPINGS.get(loc_lower, stripped.title())


def extract_canton(location: str) -> str | None:
    """Try to extract a Swiss canton 2-letter code from a location string.

    Returns the canton code (e.g. 'ZH') or None if not recognized.
    """
    if not location:
        return None
    loc_lower = location.lower().strip()

    # Direct match on the whole string
    if loc_lower in SWISS_CANTONS:
        return SWISS_CANTONS[loc_lower]

    # Substring match — only use names longer than 2 chars to avoid false positives
    for name, code in SWISS_CANTONS.items():
        if len(name) > 2 and name in loc_lower:
            return code

    return None
