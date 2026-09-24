"""Stdlib-only check of the two runtime-isolated sides of the handover.

Run: python scripts/test_search_handover_contract.py
No BFF/core imports, databases, credentials, or application side effects.
"""

import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def literal(path, name):
    tree = ast.parse((ROOT / path).read_text())
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names = [
                target.id
                for target in statement.targets
                if isinstance(target, ast.Name)
            ]
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            names = [statement.target.id]
        else:
            continue
        if name in names:
            return ast.literal_eval(statement.value)
    raise AssertionError(f"missing contract: {name}")


class SearchContract(unittest.TestCase):
    def test_workingnomads_metadata_tables_preserve_legacy_priority(self):
        core = "jobhunt_core/harvest/providers/search_metadata.py"
        for name in ("SENIORITY_PATTERNS", "CONTRACT_PATTERNS"):
            self.assertEqual(
                literal(core, name),
                literal("backend/services/data_normalizer.py", name),
            )
        self.assertEqual(
            list(literal(core, "SWISS_CANTONS").items()),
            list(literal("backend/utils/text.py", "SWISS_CANTONS").items()),
        )

    def test_browser_headers_match_the_retiring_producers(self):
        """Jobgether answers 403 without these; a drift would kill the source."""
        core = "jobhunt_core/harvest/providers/browser_headers.py"
        legacy = "backend/services/scraper_stealth.py"
        for name in ("DEFAULT_USER_AGENT", "DEFAULT_ACCEPT_LANGUAGE", "_CHROME_MAJOR"):
            self.assertEqual(literal(core, name), literal(legacy, name))

    def test_source_fingerprint_is_identical_on_both_sides(self):
        self.assertEqual(
            literal("backend/services/search_handover.py", "JOBS_FINGERPRINT_SQL"),
            literal("jobhunt_core/search_cutover.py", "JOBS_FINGERPRINT_SQL"),
        )

    def test_captured_content_covers_the_existing_projector_payload(self):
        fields = set(literal("backend/services/search_handover.py", "OFFER_FIELDS"))
        required = set(literal("jobhunt_core/shadow/projector.py", "JOB_PAYLOAD_MAP"))
        required.update(
            {"hash", "source", "url", "apply_url", "is_active", "duplicate_of"}
        )
        self.assertLessEqual(required, fields)
        self.assertNotIn("embedding", fields)


if __name__ == "__main__":
    unittest.main()
