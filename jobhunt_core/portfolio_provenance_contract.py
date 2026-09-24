"""Contrato compartido de identidades capturadas y borradas por C-4."""

COMPOSITE_PK_COLUMNS = {
    "profile_vacancy_state": ("profile_id", "vacancy_id"),
    "offer_revision_sources": (
        "offer_revision_id",
        "source_listing_revision_id",
    ),
}

# El orden es child->parent y pertenece al rollback, pero se declara junto al
# contrato para que productor y consumidor no puedan divergir silenciosamente.
SINGLE_PK_DELETE_ORDER = (
    "application_status_events",
    "applications",
    "saved_searches",
    "link_evidence",
    "dedup_candidates",
    "source_listing_revisions",
    "source_listing_incarnations",
    "offer_revisions",
    "source_listings",
    "vacancies",
    "harvest_scopes",
    "sources",
)

PROVENANCE_TABLES = frozenset(SINGLE_PK_DELETE_ORDER) | frozenset(COMPOSITE_PK_COLUMNS)
