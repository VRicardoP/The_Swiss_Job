"""Authoritative match taxonomy shared by Core facets and category pages."""

from __future__ import annotations

import json
import re
from importlib.resources import files


_CATEGORIES = json.loads(
    files("jobhunt_core").joinpath("match_categories.json").read_text(encoding="utf-8")
)
_PATTERNS = {
    category["id"]: tuple(
        re.compile(keyword) if ".*" in keyword else keyword
        for keyword in category["keywords"]
    )
    for category in _CATEGORIES
}
CATEGORY_IDS = frozenset(category["id"] for category in _CATEGORIES) | {"otros"}


def categories() -> list[dict]:
    """Public category metadata; matching keywords remain an internal rule."""
    return [
        {
            "id": category["id"],
            "label": category["label"],
            "short_label": category["shortLabel"],
        }
        for category in _CATEGORIES
    ]


def classify(title: str | None, tags: list | None) -> str:
    """Port of ``frontend/src/utils/jobCategories.js::classifyMatch``."""
    text = " ".join(
        [title or "", *(tag for tag in (tags or []) if isinstance(tag, str))]
    ).lower()
    for category in _CATEGORIES:
        for pattern in _PATTERNS[category["id"]]:
            matches = (
                pattern in text
                if isinstance(pattern, str)
                else pattern.search(text) is not None
            )
            if matches:
                return category["id"]
    return "otros"


def content_search_text_sql(content: str = "o.content") -> str:
    """Expresión SQL del texto que clasifica `sql_case`: título + tags de la
    canónica, en minúsculas.

    Vive junto a la taxonomía porque son UNA regla: tenerla copiada permitía
    que el feed de facetas y el catálogo clasificaran la misma oferta de forma
    distinta. El llamador debe evaluarla UNA vez por fila (CTE o LATERAL) y
    pasar su alias a `sql_case`: inlinearla dentro del CASE la ejecutaría una
    vez por palabra clave.
    """
    return (
        f"lower(concat_ws(' ', COALESCE({content}->>'title',''), "
        "  (SELECT string_agg(tag #>> '{}', ' ' ORDER BY ordinal) "
        "   FROM jsonb_array_elements(CASE "
        f"     WHEN jsonb_typeof({content}->'tags')='array' "
        f"     THEN {content}->'tags' ELSE '[]'::jsonb END) "
        "     WITH ORDINALITY AS tags(tag, ordinal) "
        "   WHERE jsonb_typeof(tag)='string')))"
    )


def sql_case(search_text: str = "search_text") -> tuple[str, dict[str, str]]:
    """Return the ordered taxonomy as a parameterized PostgreSQL CASE."""
    params: dict[str, str] = {}
    clauses = []
    for category_index, category in enumerate(_CATEGORIES):
        conditions = []
        for keyword_index, keyword in enumerate(category["keywords"]):
            name = f"category_{category_index}_{keyword_index}"
            params[name] = keyword
            operator = "~" if ".*" in keyword else None
            conditions.append(
                f"{search_text} {operator} :{name}"
                if operator
                else f"strpos({search_text}, :{name}) > 0"
            )
        clauses.append(f"WHEN {' OR '.join(conditions)} THEN '{category['id']}'")
    return "CASE " + " ".join(clauses) + " ELSE 'otros' END", params
