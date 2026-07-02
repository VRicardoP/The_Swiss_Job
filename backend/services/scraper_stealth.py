"""Anti-detection helpers for HTML scrapers.

Pure, side-effect-free utilities derived from common bot-detection mechanisms:
realistic browser headers, Playwright stealth tweaks, randomized request delays
(jitter) and soft-block / CAPTCHA detection.

Reference: web scraping course notes (bot detection §2, anti-detection §3,
best practices §10). Each function does ONE thing so scrapers can compose them.
"""

import random

# User-agent de un Chrome estable reciente sobre Linux. Debe mantenerse
# coherente con los client hints (Sec-CH-UA) de abajo: misma versión mayor.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Versión mayor de Chrome anunciada por el user-agent y los client hints.
_CHROME_MAJOR = "131"

# Idioma por defecto: prioriza alemán suizo (mayoría de portales del proyecto).
DEFAULT_ACCEPT_LANGUAGE = "de-CH,de;q=0.9,fr;q=0.8,en;q=0.7"

# Marcadores de soft-block: HTML que devuelve 200 pero NO contiene datos reales
# porque es una pantalla de verificación anti-bot (Cloudflare, PerimeterX, etc.).
# Se comparan en minúsculas como substrings. Se eligen marcadores de alta
# confianza, improbables en un listado de empleo legítimo, para evitar falsos
# positivos. Cada scraper puede ampliar esta lista con marcadores propios.
DEFAULT_SOFT_BLOCK_MARKERS: tuple[str, ...] = (
    # "captcha" a secas NO se incluye: es substring de widgets legítimos embebidos
    # (reCAPTCHA/hCaptcha, p.ej. "g-recaptcha") y dispararía falsos positivos en
    # páginas de resultados válidas — un falso soft-block cuenta hacia el kill-switch
    # de compliance. Se usan frases de challenge de alta confianza en su lugar.
    "complete the captcha",
    "captcha to continue",
    "cf-challenge",
    "cf-browser-verification",
    "/cdn-cgi/challenge-platform",
    "px-captcha",
    "verify you are human",
    "are you a robot",
    "unusual traffic from your",
    "enable javascript and cookies to continue",
)

# Argumento de lanzamiento de Chromium anti-detección: desactivar
# AutomationControlled es lo que oculta navigator.webdriver en el origen del
# browser. Puramente stealth — NO reduce el aislamiento del renderer.
STEALTH_LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
)

# Argumentos que Chromium exige al correr como root en un contenedor. NO son
# stealth y REDUCEN el sandbox del renderer, por eso van aparte y gateados por
# settings.SCRAPER_PLAYWRIGHT_NO_SANDBOX (ver _build_launch_args). Se descartó
# --disable-features=IsolateOrigins,site-per-process: rebajaba el site-isolation
# sin aportar nada a la ocultación de automatización.
CHROMIUM_CONTAINER_ARGS: tuple[str, ...] = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
)

# Script inyectado antes de cualquier script de la página. Enmascara las pistas
# clásicas que delatan a Playwright/headless en tests como bot.sannysoft.com:
# navigator.webdriver, ausencia de plugins, languages vacío y window.chrome.
STEALTH_INIT_SCRIPT = """
// Ocultar la bandera de automatización
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
// Un navegador real expone idiomas
Object.defineProperty(navigator, 'languages', { get: () => ['de-CH', 'de', 'en'] });
// Un navegador real expone plugins (longitud > 0)
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
// Chrome expone el objeto window.chrome
window.chrome = window.chrome || { runtime: {} };
// La API de permisos no debe delatar el modo headless
const originalQuery = window.navigator.permissions && window.navigator.permissions.query;
if (originalQuery) {
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
}
"""


def realistic_headers(
    referer: str | None = None,
    accept_language: str = DEFAULT_ACCEPT_LANGUAGE,
) -> dict[str, str]:
    """Construye cabeceras que imitan a un Chrome real.

    Incluye client hints (Sec-CH-UA) y cabeceras Sec-Fetch coherentes con una
    navegación de documento de nivel superior. NO fija Accept-Encoding a
    propósito: dejar que httpx negocie la compresión que sabe descomprimir
    (evita cuerpos ilegibles si falta el códec brotli/zstd).
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": accept_language,
        "Sec-CH-UA": (
            f'"Chromium";v="{_CHROME_MAJOR}", '
            f'"Google Chrome";v="{_CHROME_MAJOR}", '
            '"Not?A_Brand";v="24"'
        ),
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": '"Linux"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if referer:
        # Con un referer, la petición ya no es de origen "none".
        headers["Referer"] = referer
        headers["Sec-Fetch-Site"] = "same-origin"
    return headers


def jittered_delay(base_seconds: float, jitter_ratio: float) -> float:
    """Devuelve un retardo aleatorizado para no crear intervalos constantes.

    Los intervalos perfectamente regulares son una señal de bot. El resultado
    está en el rango [base, base * (1 + jitter_ratio)]. Con base<=0 o ratio<=0
    se comporta de forma determinista (devuelve base saneado).
    """
    if base_seconds <= 0:
        return 0.0
    ratio = max(0.0, jitter_ratio)
    return base_seconds + random.uniform(0.0, base_seconds * ratio)


def looks_soft_blocked(html: str, markers: tuple[str, ...] | list[str]) -> bool:
    """Indica si el HTML parece una pantalla anti-bot en vez de datos reales.

    Pensado para combinarse con "0 resultados parseados": un 200 sin empleos y
    con un marcador de verificación es un soft-block que conviene reportar.
    """
    if not html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in markers)
