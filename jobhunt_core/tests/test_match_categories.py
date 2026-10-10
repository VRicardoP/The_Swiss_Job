from jobhunt_core.match_categories import classify


def test_classification_preserves_precedence_tags_regex_and_fallback():
    assert classify("Senior Content Editor", []) == "A"
    assert classify("Account Executive", []) == "E"  # precedes sales category I
    assert classify("Unlabelled role", ["international school"]) == "H"
    assert classify("Standort Zürich Betreuung", []) == "H"
    assert classify("Quantum Mechanic", []) == "otros"
