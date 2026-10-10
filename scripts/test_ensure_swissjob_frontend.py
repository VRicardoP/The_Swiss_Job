from scripts.ensure_swissjob_frontend import ensure


def test_adds_frontend_once_without_touching_existing_services(tmp_path):
    compose = tmp_path / "compose.yml"
    original = "services:\n  backend:\n    image: backend:one\n\nnetworks:\n  app: {}\n"
    compose.write_text(original)

    assert ensure(compose) is True
    first = compose.read_text()
    assert ensure(compose) is False
    assert compose.read_text() == first
    assert "    image: backend:one\n" in first
    assert "  frontend:\n" in first
    assert "    image: swissjob-frontend:point5-0000000\n" in first
    assert first.index("  frontend:\n") < first.index("\nnetworks:\n")
