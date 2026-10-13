from pathlib import Path

from check_no_secrets import revisar


def test_fixed_refresh_body_flag_is_not_a_secret(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("AUTH_REFRESH_TOKEN_IN_BODY: 'false'\n")
    assert revisar(compose) == []


def test_refresh_body_flag_only_allows_the_safe_fixed_value(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("AUTH_REFRESH_TOKEN_IN_BODY: 'true'\n")
    assert revisar(compose) == [
        f"{compose}:1: AUTH_REFRESH_TOKEN_IN_BODY lleva un literal"
    ]


def test_other_secret_like_keys_still_reject_false_literal(tmp_path: Path):
    compose = tmp_path / "compose.yml"
    compose.write_text("SMTP_PASSWORD: 'false'\n")
    assert revisar(compose) == [f"{compose}:1: SMTP_PASSWORD lleva un literal"]
