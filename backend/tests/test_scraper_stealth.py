"""Tests for scraper_stealth — anti-detection helper functions."""

import random

from services.scraper_stealth import (
    DEFAULT_SOFT_BLOCK_MARKERS,
    STEALTH_INIT_SCRIPT,
    STEALTH_LAUNCH_ARGS,
    jittered_delay,
    looks_soft_blocked,
    realistic_headers,
)


class TestRealisticHeaders:
    def test_includes_core_browser_headers(self):
        h = realistic_headers()
        assert "Chrome/" in h["User-Agent"]
        assert h["Sec-Fetch-Mode"] == "navigate"
        assert "Sec-CH-UA" in h
        assert h["Accept-Language"].startswith("de-CH")
        assert h["Upgrade-Insecure-Requests"] == "1"

    def test_omits_accept_encoding(self):
        # No se fija a propósito: httpx negocia lo que sabe descomprimir.
        assert "Accept-Encoding" not in realistic_headers()

    def test_no_referer_means_origin_none(self):
        assert realistic_headers()["Sec-Fetch-Site"] == "none"
        assert "Referer" not in realistic_headers()

    def test_referer_sets_same_origin(self):
        h = realistic_headers(referer="https://example.com/")
        assert h["Referer"] == "https://example.com/"
        assert h["Sec-Fetch-Site"] == "same-origin"

    def test_custom_accept_language(self):
        h = realistic_headers(accept_language="fr-CH,fr;q=0.9")
        assert h["Accept-Language"] == "fr-CH,fr;q=0.9"

    def test_chrome_version_matches_client_hints(self):
        # El UA y los client hints deben anunciar la misma versión mayor.
        h = realistic_headers()
        assert "131" in h["User-Agent"]
        assert '"131"' in h["Sec-CH-UA"]


class TestJitteredDelay:
    def test_zero_or_negative_base_returns_zero(self):
        assert jittered_delay(0, 0.5) == 0.0
        assert jittered_delay(-5, 0.5) == 0.0

    def test_zero_ratio_is_deterministic(self):
        assert jittered_delay(2.0, 0.0) == 2.0

    def test_within_bounds_over_many_samples(self):
        random.seed(1234)
        base, ratio = 2.0, 0.5
        for _ in range(500):
            d = jittered_delay(base, ratio)
            assert base <= d <= base * (1 + ratio)

    def test_negative_ratio_treated_as_zero(self):
        assert jittered_delay(3.0, -1.0) == 3.0

    def test_produces_variation(self):
        # Con jitter, dos llamadas no deberían ser siempre idénticas.
        random.seed(99)
        values = {jittered_delay(2.0, 0.5) for _ in range(50)}
        assert len(values) > 1


class TestLooksSoftBlocked:
    def test_empty_html_is_not_blocked(self):
        assert looks_soft_blocked("", DEFAULT_SOFT_BLOCK_MARKERS) is False

    def test_clean_html_is_not_blocked(self):
        html = "<html><body><div class='job'>Developer</div></body></html>"
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is False

    def test_captcha_marker_detected(self):
        html = "<html><body>Please complete the CAPTCHA to continue</body></html>"
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is True

    def test_cloudflare_marker_detected(self):
        html = "<html><body>Checking /cdn-cgi/challenge-platform ...</body></html>"
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is True

    def test_case_insensitive(self):
        assert looks_soft_blocked("ARE YOU A ROBOT?", DEFAULT_SOFT_BLOCK_MARKERS)

    def test_custom_markers(self):
        assert looks_soft_blocked("Something went wrong", ["something went wrong"])


class TestStealthConstants:
    def test_init_script_masks_webdriver(self):
        assert "navigator" in STEALTH_INIT_SCRIPT
        assert "webdriver" in STEALTH_INIT_SCRIPT

    def test_launch_args_disable_automation_flag(self):
        assert any("AutomationControlled" in arg for arg in STEALTH_LAUNCH_ARGS)
