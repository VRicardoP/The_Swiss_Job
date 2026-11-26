"""Título de oferta en inglés, traducido UNA vez por título en el núcleo (D10).

Antes lo traducían por su cuenta el BFF (calentador + caché Redis 30 d) y
Portfolio (`job_enrichments`), con la MISMA clave de Groq y sobre el mismo
corpus (R17 del análisis conjunto del 2026-11-25). Ahora lo hace el núcleo,
una vez por título distinto, y lo sirve como `VacancyDTO.title_en`.

La clave es el TÍTULO normalizado, no la vacante: el mismo título se repite
entre vacantes, fuentes y reposts, y su traducción depende sólo del texto.
La expresión de la clave vive en `TITLE_KEY_SQL` y la usan tanto el
descubrimiento como `api.v1._vacancy_dtos`, así que no pueden desalinearse.

Estados de una fila de `title_translations` (los cuatro explícitos):

| Estado | `language` | `title_en` | `attempts` |
|---|---|---|---|
| Descubierto, sin detectar | NULL | NULL | 0 |
| No necesita traducción (EN/ES) | 'en'/'es' | NULL | 0 |
| Pendiente del LLM | otro o '' | NULL | < MAX_ATTEMPTS |
| Traducido | otro o '' | texto | — |
| Abandonado (el LLM no lo devolvió MAX_ATTEMPTS veces) | otro o '' | NULL | >= MAX |

Lección F3 (Portfolio, 2026-11-25), que aquí es invariante: **un fallo del
proveedor nunca marca nada**. Un 429, un timeout, un 5xx o un error de
configuración paran la pasada sin tocar ninguna fila; sólo un 200 cuya
respuesta omite un título suma un intento a ese título. «No necesita
traducción» lo decide únicamente el detector LOCAL de idioma.

Servir nunca traduce ni detecta: `_vacancy_dtos` lee la columna con el mismo
JOIN que ya trae el contenido (cero consultas extra por página).
"""

import json
import logging
import re
import time
from collections.abc import Callable

import httpx
import sqlalchemy as sa

from jobhunt_core.config import settings

logger = logging.getLogger(__name__)

# Clave canónica de un título. Cota de 500: ningún título real se acerca y
# mantiene el btree de la PK lejos de su límite. Usada en SQL por el
# descubrimiento y por `_vacancy_dtos` (con alias `o` para offer_revisions).
TITLE_KEY_SQL = "left(btrim(o.content->>'title'), 500)"

# Idiomas que no se traducen. Mismo criterio que tenía el BFF
# (`translation_service.SKIP_LANGUAGES`).
SKIP_LANGUAGES = frozenset({"en", "es"})

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
HTTP_TOO_MANY_REQUESTS = 429
# Una llamada = un lote. 25 títulos caben en 2.048 tokens de salida con margen.
BATCH_SIZE = 25
# Cotas de una pasada. El worker de operaciones tiene concurrencia 1 y
# comparte proceso con el outbox: una pasada no puede ocuparlo minutos.
# yagni: techo ~9.600 títulos/día a la cadencia por defecto (15 min); si el
# corpus crece más rápido, subir MAX_BATCHES_PER_PASS o bajar la cadencia.
MAX_BATCHES_PER_PASS = 4
# langdetect cuesta ~100 ms por título en el NAS (medido por el BFF): 200 son
# ~20 s de CPU por pasada como mucho.
DETECT_PER_PASS = 200
REQUEST_TIMEOUT_S = 25.0
MAX_TOKENS = 2048
# Un título que el LLM omite tres veces en respuestas válidas se abandona: se
# sirve en su idioma en vez de ocupar la ventana de cada pasada para siempre.
MAX_ATTEMPTS = 3
# Tras un 429 no se pide nada en este plazo (o el que diga `retry-after`, si
# es mayor). La cuota de Groq es rodante: reintentar al minuto siguiente
# recibe otro 429 (F1, Portfolio).
RATE_LIMIT_PAUSE_S = 15 * 60

# Estado de PROCESO, a propósito: la tarea corre en el worker de operaciones
# (concurrencia 1). yagni: si el proceso hijo se recicla se pierde la pausa
# y la marca de descubrimiento; el coste es UNA llamada que recibe otro 429 o
# UN descubrimiento de más. Si llegara a importar, persistirlo en Redis.
_pause = {"until": 0.0}
_discovered_at_generation: dict[str, int | None] = {"gen": None}

TRANSLATION_SYSTEM_PROMPT = """\
You are a professional translator specializing in Swiss job market titles.
Translate the following job titles to English.

RULES:
1. Translate ONLY the job title text — no explanations, no commentary.
2. Keep proper nouns, brand names, and technical acronyms unchanged \
(e.g. "KV", "ICT", "SAP", "ERP", "HR", "CTO", "CEO").
3. Preserve percentage ranges like "80-100%" as-is.
4. Preserve gender markers like "(m/w/d)", "(m/f/d)", "m/w" as-is.
5. If the title is already in English, return it unchanged.
6. For German compound words, split and translate meaningfully: \
"Sachbearbeiter" → "Clerk/Administrator", \
"Fachbereichsleiter" → "Department Head", \
"Projektleiter" → "Project Manager", \
"Softwareentwickler" → "Software Developer", \
"Pflegefachperson" → "Registered Nurse", \
"Lehrperson" → "Teacher", \
"Kauffrau/Kaufmann" → "Commercial Employee".
7. Return ONLY a JSON object mapping the index (as string) to its English translation.

Example input:
{"0": "Sachbearbeiter/in Finanzbuchhaltung 80-100%", \
"1": "Développeur Full Stack Senior", \
"2": "Software Engineer"}
Example output:
{"0": "Financial Accounting Clerk 80-100%", \
"1": "Senior Full Stack Developer", \
"2": "Software Engineer"}

Respond with ONLY the JSON object. No markdown fences. No extra text."""

# --- detección local de idioma (mismo criterio que el BFF) -------------------

_GERMAN_CHARS = frozenset("äöüÄÖÜß")
_FRENCH_CHARS = frozenset("éèêëàâîïùûçœæÉÈÊÀÂÎÙÛÇ")
_ITALIAN_CHARS = frozenset("àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ")
LANGDETECT_MIN_PROB = 0.35
LONG_TOKEN_CHARS = 12


def _lang_from_chars(title: str) -> str:
    """Caracteres propios de DE/FR/IT o palabras compuestas largas (alemán).
    `''` si la heurística no decide."""
    chars = set(title)
    if chars & _GERMAN_CHARS:
        return "de"
    if chars & _FRENCH_CHARS:
        return "fr"
    if chars & _ITALIAN_CHARS:
        return "it"
    # Asterisco y barra son marcadores de género suizos: fuera antes de medir.
    words = title.replace("*", "").replace("/", " ").split()
    if any(len(w) > LONG_TOKEN_CHARS for w in words):
        return "de"
    return ""


def detect_language(title: str) -> str:
    """Idioma de un título, o `''` si no se puede decidir.

    `''` NO es «no necesita traducción»: sólo EN/ES lo son, y un desconocido
    va al LLM, que devuelve el título igual si ya estaba en inglés. Es el
    criterio que usaba el BFF (`_resolve_language(title, "")`)."""
    by_chars = _lang_from_chars(title)
    if by_chars:
        return by_chars
    from langdetect import DetectorFactory, detect_langs
    from langdetect.lang_detect_exception import LangDetectException

    # langdetect no es determinista en textos cortos; con semilla fija la
    # respuesta persistida no depende de la suerte del proceso.
    DetectorFactory.seed = 0
    try:
        results = detect_langs(title)
    except LangDetectException:
        return ""
    if not results or results[0].prob < LANGDETECT_MIN_PROB:
        return ""
    return results[0].lang


def needs_translation(language: str) -> bool:
    return language not in SKIP_LANGUAGES


# --- respuesta del LLM --------------------------------------------------------

_JSON_PAIR_RE = re.compile(r'"(\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_response(raw: str, titles: list[str]) -> dict[str, str]:
    """{título: traducción} de lo que la respuesta trae bien formado.

    Tolera fences de markdown, texto alrededor y un JSON truncado (rescata los
    pares completos). Lo que falte simplemente no aparece."""
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    try:
        mapping = json.loads(text)
        if not isinstance(mapping, dict):
            mapping = {}
    except json.JSONDecodeError:
        mapping = {}
        for idx, value in _JSON_PAIR_RE.findall(text):
            try:
                mapping[idx] = json.loads(f'"{value}"')
            except json.JSONDecodeError:
                mapping[idx] = value
    out = {}
    for i, title in enumerate(titles):
        value = mapping.get(str(i))
        if isinstance(value, str) and value.strip():
            out[title] = " ".join(value.split())
    return out


class ProviderUnavailable(Exception):
    """El proveedor no dio una respuesta utilizable: la pasada para sin
    marcar ninguna fila."""


def _retry_after_s(response: httpx.Response) -> float:
    try:
        return max(float(response.headers.get("retry-after", 0)), RATE_LIMIT_PAUSE_S)
    except ValueError:
        return RATE_LIMIT_PAUSE_S


async def _call_llm(client: httpx.AsyncClient, titles: list[str]) -> str:
    """Una llamada a Groq para un lote. Lanza ProviderUnavailable ante
    cualquier respuesta que no sea un 200 legible; ante un 429 arma la pausa."""
    body = {
        "model": settings.CORE_TITLE_TRANSLATION_MODEL,
        "messages": [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {str(i): t for i, t in enumerate(titles)}, ensure_ascii=False
                ),
            },
        ],
        "temperature": 0.05,
        "max_tokens": MAX_TOKENS,
    }
    if settings.CORE_TITLE_TRANSLATION_REASONING_EFFORT:
        body["reasoning_effort"] = settings.CORE_TITLE_TRANSLATION_REASONING_EFFORT
    try:
        response = await client.post(
            GROQ_CHAT_URL,
            json=body,
            headers={"Authorization": f"Bearer {settings.CORE_GROQ_API_KEY}"},
        )
    except httpx.HTTPError as exc:
        raise ProviderUnavailable(type(exc).__name__) from exc
    if response.status_code == HTTP_TOO_MANY_REQUESTS:
        pause = _retry_after_s(response)
        _pause["until"] = time.monotonic() + pause
        logger.info(
            "title_translation: límite de tasa de Groq; pausa de %d min", pause // 60
        )
        raise ProviderUnavailable("429")
    if response.status_code in (401, 403, 404):
        # Configuración, no transitorio. Groq decomisa modelos sin avisar (el
        # 404): el mensaje nombra el mando que hay que girar.
        logger.error(
            "title_translation: Groq respondió %s; revisa CORE_GROQ_API_KEY y "
            "CORE_TITLE_TRANSLATION_MODEL=%r contra el catálogo vivo",
            response.status_code,
            settings.CORE_TITLE_TRANSLATION_MODEL,
        )
        raise ProviderUnavailable(str(response.status_code))
    if response.status_code != 200:
        raise ProviderUnavailable(str(response.status_code))
    try:
        return response.json()["choices"][0]["message"]["content"] or ""
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderUnavailable("cuerpo ilegible") from exc


# --- SQL ---------------------------------------------------------------------

_ACTIVE_TITLES = (
    "FROM vacancies v JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
    "WHERE v.archived_at IS NULL AND v.merged_into IS NULL "
    f"AND {TITLE_KEY_SQL} <> ''"
)

# Los títulos de los feeds primero (prioridad 1): es lo que alguien mira.
DISCOVER_FEED_SQL = (
    "INSERT INTO title_translations (title_key, priority) "
    f"SELECT DISTINCT {TITLE_KEY_SQL}, 1 {_ACTIVE_TITLES} "
    "AND EXISTS (SELECT 1 FROM profile_vacancy_state s "
    "            WHERE s.vacancy_id = v.id AND s.current_eval_id IS NOT NULL) "
    "ON CONFLICT (title_key) DO UPDATE SET priority = 1 "
    "WHERE title_translations.priority = 0"
)
DISCOVER_ALL_SQL = (
    "INSERT INTO title_translations (title_key) "
    f"SELECT DISTINCT {TITLE_KEY_SQL} {_ACTIVE_TITLES} "
    "ON CONFLICT (title_key) DO NOTHING"
)
UNDETECTED_SQL = (
    "SELECT title_key FROM title_translations WHERE language IS NULL "
    "ORDER BY priority DESC, created_at, title_key LIMIT :lim"
)
PENDING_SQL = (
    "SELECT title_key FROM title_translations "
    "WHERE language IS NOT NULL AND language NOT IN ('en', 'es') "
    "AND title_en IS NULL AND attempts < :max_attempts "
    "ORDER BY priority DESC, attempted_at NULLS FIRST, created_at, title_key "
    "LIMIT :lim"
)


async def _discover(session) -> int:
    """Da de alta los títulos activos que aún no tienen fila.

    Recorre el corpus activo (un escaneo de ~95.000 vacantes), así que sólo
    se ejecuta cuando la generación del corpus cambió desde el último
    descubrimiento: toda alta, baja o cambio de canónica de una vacante la sube
    (core0022)."""
    gen = await session.scalar(
        sa.text("SELECT generation FROM corpus_generation WHERE id = 1")
    )
    if gen is not None and gen == _discovered_at_generation["gen"]:
        return 0
    feed = (await session.execute(sa.text(DISCOVER_FEED_SQL))).rowcount or 0
    rest = (await session.execute(sa.text(DISCOVER_ALL_SQL))).rowcount or 0
    await session.commit()
    _discovered_at_generation["gen"] = gen
    return feed + rest


async def _detect(session) -> int:
    keys = (
        await session.scalars(sa.text(UNDETECTED_SQL), {"lim": DETECT_PER_PASS})
    ).all()
    if not keys:
        return 0
    rows = [{"k": k, "lang": detect_language(k)} for k in keys]
    await session.execute(
        sa.text("UPDATE title_translations SET language = :lang WHERE title_key = :k"),
        rows,
    )
    await session.commit()
    return len(rows)


async def _store(session, translated: dict[str, str], missed: list[str]) -> None:
    if translated:
        await session.execute(
            sa.text(
                "UPDATE title_translations SET title_en = :en, "
                "translated_at = clock_timestamp(), attempted_at = clock_timestamp() "
                "WHERE title_key = :k"
            ),
            [{"k": k, "en": v[:1000]} for k, v in translated.items()],
        )
    if missed:
        await session.execute(
            sa.text(
                "UPDATE title_translations SET attempts = attempts + 1, "
                "attempted_at = clock_timestamp() WHERE title_key = ANY(:keys)"
            ),
            {"keys": missed},
        )
    await session.commit()


async def _translate(session, client: httpx.AsyncClient) -> tuple[int, int]:
    """Traduce hasta MAX_BATCHES_PER_PASS lotes. Devuelve (traducidos, omitidos)."""
    done = missed_total = 0
    for _ in range(MAX_BATCHES_PER_PASS):
        if time.monotonic() < _pause["until"]:
            break
        batch = (
            await session.scalars(
                sa.text(PENDING_SQL),
                {"lim": BATCH_SIZE, "max_attempts": MAX_ATTEMPTS},
            )
        ).all()
        if not batch:
            break
        try:
            raw = await _call_llm(client, batch)
        except ProviderUnavailable as exc:
            # F3: un fallo del proveedor NO toca ninguna fila. Lo pendiente
            # sigue pendiente y vuelve en la pasada siguiente.
            logger.warning("title_translation: pasada detenida (%s)", exc)
            break
        translated = parse_response(raw, batch)
        missed = [t for t in batch if t not in translated]
        await _store(session, translated, missed)
        done += len(translated)
        missed_total += len(missed)
    return done, missed_total


async def run_pass(
    session_factory,
    transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
) -> dict[str, int]:
    """Una pasada: descubrir (si cambió el corpus), detectar y traducir.

    Sin `CORE_GROQ_API_KEY` no hace NADA, ni siquiera descubrir: sin quien
    traduzca, la tabla sólo crecería."""
    result = {"discovered": 0, "detected": 0, "translated": 0, "missed": 0}
    if not settings.CORE_GROQ_API_KEY:
        return result
    async with session_factory() as session:
        result["discovered"] = await _discover(session)
        result["detected"] = await _detect(session)
        transport = transport_factory() if transport_factory else None
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_S, transport=transport
        ) as client:
            result["translated"], result["missed"] = await _translate(session, client)
    return result
