"""Frozen SwissJob provider-title rule for the producer handover.

Copied from backend/tasks/fetch_tasks.py at 6ba912e, NOT imported at runtime:
the core must remain independent of the retiring BFF. Applied only to NEW
entries of scopes explicitly opting into legacy_title_filter. It is not a
general-purpose relevance filter for every consumer.
Sorted compact JSON fingerprint: 0ee1f83fd569d622490ad53bb7de71526194f922efcfbd8ad0812acd06e8a883.
"""

LEGACY_TITLE_KEYWORDS = frozenset(
    {
        "ai engineer",
        "ai research engineer",
        "android developer",
        "ansible",
        "backend developer",
        "backend engineer",
        "bauführer",
        "blockchain",
        "cloud architect",
        "cloud engineer",
        "computer vision",
        "cybersecurity",
        "data architect",
        "data engineer",
        "data scientist",
        "deep learning",
        "devops",
        "devsecops",
        "elektriker",
        "embedded",
        "ergotherap",
        "firmware",
        "flutter developer",
        "frontend developer",
        "frontend engineer",
        "full stack",
        "full-stack",
        "fullstack",
        "hauswirtschaft",
        "hochbau",
        "infosec",
        "infrastructure engineer",
        "installateur",
        "ios developer",
        "kaminbaumonteur",
        "krankenpfleger",
        "krankenschwester",
        "kubernetes",
        "küchenhilfe",
        "logopäd",
        "machine learning engineer",
        "maurer",
        "metallbau",
        "ml engineer",
        "mobile developer",
        "network administrator",
        "network engineer",
        "nlp engineer",
        "penetration tester",
        "pflegefachfrau",
        "pflegefachmann",
        "pflegefachperson",
        "physiotherap",
        "platform engineer",
        "polier",
        "psychiatriepflege",
        "react native",
        "reinigungskraft",
        "sanitärmonteur",
        "security engineer",
        "site reliability",
        "smart contract",
        "software architect",
        "software developer",
        "software engineer",
        "solidity",
        "sre",
        "systems engineer",
        "terraform",
        "tiefbau",
        "web3 developer",
        "zimmermann",
    }
)


def excluded_title(title: str | None) -> bool:
    if not isinstance(title, str):
        return False
    lowered = title.lower()
    return any(keyword in lowered for keyword in LEGACY_TITLE_KEYWORDS)
