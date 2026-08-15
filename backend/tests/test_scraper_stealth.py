"""Tests for scraper_stealth — anti-detection helper functions."""

import random
from pathlib import Path

from services.scraper_stealth import (
    DEFAULT_SOFT_BLOCK_MARKERS,
    STEALTH_INIT_SCRIPT,
    STEALTH_LAUNCH_ARGS,
    jittered_delay,
    looks_soft_blocked,
    realistic_headers,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Pantalla de challenge REAL de Cloudflare (interstitial moderno): título
# "Just a moment..." + texto de error + orquestador activo de challenge-platform.
# Debe seguir detectándose por sus marcadores de texto tras VD.4a.
_CF_CHALLENGE_HTML = """<!DOCTYPE html><html lang="en-US">
<head><title>Just a moment...</title></head>
<body class="no-js">
<div class="main-wrapper" role="main">
<h1><span id="challenge-error-text">Enable JavaScript and cookies to continue</span></h1>
</div>
<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>
</body></html>"""


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

    def test_passive_cloudflare_beacon_is_not_blocked(self):
        # Regresión VD.4a: el HTML REAL de ISB (200, board vacío legítimo) incluye
        # el beacon pasivo de telemetría /cdn-cgi/challenge-platform/scripts/jsd/
        # — presente en CUALQUIER sitio tras Cloudflare. No es un challenge y no
        # debe contar como soft-block (antes disparaba el kill-switch en bucle).
        html = (FIXTURES / "swiss_schools_isb_listing_empty.html").read_text()
        assert "/cdn-cgi/challenge-platform" in html  # el beacon está presente
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is False

    def test_real_cloudflare_challenge_detected(self):
        # Un challenge REAL se sigue cazando por sus marcadores de texto
        # ("enable javascript and cookies to continue"), sin necesitar la ruta
        # del beacon.
        assert looks_soft_blocked(_CF_CHALLENGE_HTML, DEFAULT_SOFT_BLOCK_MARKERS)

    def test_cloudflare_1020_access_denied_detected(self):
        # Pantalla 1020 de Cloudflare ("Access denied"): normalmente llega con
        # 403 y la corta _listing_status_stops antes de parsear, pero una regla
        # WAF custom que la devuelva con otro estado debe cazarse por su texto.
        html = (
            "<!DOCTYPE html><html><head><title>Access denied | example.ch"
            " used Cloudflare to restrict access</title></head><body>"
            '<h1><span class="error-description">Sorry, you have been blocked</span></h1>'
            '<h2 class="cf-subheadline">You are unable to access example.ch</h2>'
            '<div class="cf-error-footer">Cloudflare Ray ID: 8f2a1b3c4d5e6f70</div>'
            "</body></html>"
        )
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is True

    def test_legacy_cloudflare_verification_detected(self):
        # Variante clásica de Cloudflare: página con cf-browser-verification.
        html = (
            "<html><body><div id='cf-content'>Checking your browser before"
            " accessing…</div><div class='cf-browser-verification'></div>"
            "</body></html>"
        )
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is True

    def test_turnstile_managed_challenge_detected(self):
        # VD.4a 2ª ronda: Turnstile "managed challenge" sin <noscript> ni divs
        # cf-*, solo el texto en gerundio. OJO: "verify you are human" NO es
        # substring de "verifying you are human" — sin marcador propio, este
        # challenge contaba como vacío verificado y rehabilitaba la fuente.
        html = (
            "<html><head><title>Just a moment...</title></head><body>"
            "<p>Verifying you are human. This may take a few seconds.</p>"
            "</body></html>"
        )
        assert looks_soft_blocked(html, DEFAULT_SOFT_BLOCK_MARKERS) is True

    def test_checking_your_browser_without_cf_div_detected(self):
        # VD.4a 2ª ronda: interstitial clásico de Cloudflare servido por
        # instalaciones antiguas SIN div cf-* — debe cazarse por la frase sola
        # (el test de arriba lo detecta vía cf-browser-verification, no por texto).
        html = (
            "<html><body><p>Checking your browser before accessing"
            " example.ch.</p></body></html>"
        )
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
