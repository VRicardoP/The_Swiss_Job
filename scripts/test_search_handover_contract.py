"""Stdlib-only check of the two runtime-isolated sides of the handover.

Run: python scripts/test_search_handover_contract.py
No BFF/core imports, databases, credentials, or application side effects.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def literal(path, name):
    tree = ast.parse((ROOT/path).read_text())
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            names = [target.id for target in statement.targets if isinstance(target, ast.Name)]
        elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
            names = [statement.target.id]
        else:
            continue
        if name in names:
            return ast.literal_eval(statement.value)
    raise AssertionError(f"missing contract: {name}")


class SearchContract(unittest.TestCase):
    def test_source_fingerprint_is_identical_on_both_sides(self):
        self.assertEqual(literal('backend/services/search_handover.py','JOBS_FINGERPRINT_SQL'),
                         literal('jobhunt_core/search_cutover.py','JOBS_FINGERPRINT_SQL'))

    def test_captured_content_covers_the_existing_projector_payload(self):
        fields = set(literal('backend/services/search_handover.py','OFFER_FIELDS'))
        required = set(literal('jobhunt_core/shadow/projector.py','JOB_PAYLOAD_MAP'))
        required.update({'hash','source','url','apply_url','is_active','duplicate_of'})
        self.assertLessEqual(required,fields)
        self.assertNotIn('embedding',fields)


if __name__ == '__main__':
    unittest.main()
