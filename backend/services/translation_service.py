"""TranslationService — batch translation of job titles via Groq LLM.

Uses settings.GROQ_RERANK_MODEL (llama-4-scout, fast) for translating DE/FR/IT
job titles to English. Results are cached per-title in Redis with 30-day TTL.

Mejoras v2:
- Detección de idioma mejorada: heurística por caracteres especiales antes de langdetect.
- Retry automático en fallo de parsing JSON (prompt simplificado).
- max_tokens aumentado a 2048 para batches de 25 títulos largos.
- Prompt ampliado con ejemplos de compound words alemanes y marcadores suizos.
- Log de fallos de traducción para detectar patrones de degradación.
"""

import hashlib
import json
import logging
import re

from groq import APIStatusError
from langdetect import detect_langs
from langdetect.lang_detect_exception import LangDetectException

from config import settings
from services.groq_service import GroqService

logger = logging.getLogger(__name__)

# Languages that do NOT need translation
SKIP_LANGUAGES = frozenset({"en", "es"})

CACHE_PREFIX = "translate:title:"
CACHE_TTL_SECONDS = 30 * 86_400  # 30 días

# Caracteres característicos de idiomas no-inglés frecuentes en ofertas suizas
_GERMAN_CHARS = frozenset("äöüÄÖÜß")
_FRENCH_CHARS = frozenset("éèêëàâîïùûçœæÉÈÊÀÂÎÙÛÇ")
_ITALIAN_CHARS = frozenset("àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ")

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
"Systemadministrator" → "System Administrator", \
"Buchhalter" → "Accountant", \
"Pflegefachperson" → "Registered Nurse", \
"Lehrperson" → "Teacher", \
"Kauffrau/Kaufmann" → "Commercial Employee".
7. Return ONLY a JSON object mapping the index (as string) to its English translation.

Example input:
{"0": "Sachbearbeiter/in Finanzbuchhaltung 80-100%", \
"1": "Développeur Full Stack Senior", \
"2": "Fachbereichsleiter Informatik (m/w/d)", \
"3": "Software Engineer"}
Example output:
{"0": "Financial Accounting Clerk 80-100%", \
"1": "Senior Full Stack Developer", \
"2": "IT Department Head (m/w/d)", \
"3": "Software Engineer"}

Respond with ONLY the JSON object. No markdown fences. No extra text."""

# Prompt simplificado para el retry (menos instrucciones = menos confusión para el LLM)
_RETRY_SYSTEM_PROMPT = """\
Translate these Swiss job titles to English.
Return ONLY a JSON object: {"0": "translation", "1": "translation", ...}
Keep brand names, acronyms, and percentage ranges unchanged.
No markdown, no explanation."""

# Pares "índice": "valor" de un objeto JSON, tolerando comillas escapadas en el valor.
_JSON_PAIR_RE = re.compile(r'"(\d+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _salvage_pairs(text: str) -> dict[str, str]:
    """Rescata pares índice→traducción de un JSON truncado o ligeramente malformado.

    json.loads es todo-o-nada: un solo error de sintaxis (típicamente truncado por
    max_tokens) descartaría las 25 traducciones del lote. Aquí recuperamos los pares
    completos que sí están presentes antes del punto de rotura.
    """
    salvaged: dict[str, str] = {}
    for idx, raw_value in _JSON_PAIR_RE.findall(text):
        try:
            # Reusar json para des-escapar secuencias (\", \\, \uXXXX) del valor.
            salvaged[idx] = json.loads(f'"{raw_value}"')
        except json.JSONDecodeError:
            salvaged[idx] = raw_value
    return salvaged


class TranslationService:
    """Translate job titles to English via Groq LLM with Redis caching."""

    def __init__(self, groq: GroqService) -> None:
        self._groq = groq

    async def translate_titles(
        self,
        titles_with_lang: list[dict[str, str]],
    ) -> dict[str, str]:
        """Translate a batch of job titles to English.

        Args:
            titles_with_lang: List of {"title": str, "language": str}.

        Returns:
            Dict mapping original title -> English translation.
            Titles confirmadas como EN/ES (por heurística de caracteres + langdetect)
            se devuelven tal cual. El resto se intentan traducir — el LLM devuelve
            el título sin cambios si ya está en inglés.
        """
        result: dict[str, str] = {}
        to_translate: list[str] = []

        if not self._groq.is_available:
            logger.debug("Groq no disponible — devolviendo títulos originales")
            return {item["title"]: item["title"] for item in titles_with_lang}

        for item in titles_with_lang:
            title = item["title"]
            if not title:
                result[title] = title
                continue

            # Determinar idioma real: la heurística de caracteres tiene prioridad
            # sobre el campo language de BD (puede ser erróneo para títulos cortos).
            lang = self._resolve_language(title, item.get("language") or "")

            # Solo omitir traducción si estamos seguros de que es inglés/español
            if lang in SKIP_LANGUAGES:
                result[title] = title
                continue

            cached = await self._get_cached(title)
            if cached is not None:
                result[title] = cached
            else:
                to_translate.append(title)

        if not to_translate:
            return result

        try:
            await self._translate_pending(to_translate, result)
        except APIStatusError as e:
            # 401/403: la API key es inválida/sin permisos. Reintentar no arregla nada;
            # avisamos una vez de forma accionable y dejamos los títulos sin traducir
            # (el router los muestra en su idioma original al faltar en el dict).
            logger.error(
                "Groq rechazó la petición (HTTP %s): revisa GROQ_API_KEY. "
                "%d títulos quedan sin traducir (se muestran en su idioma original).",
                e.status_code,
                len(to_translate),
            )

        return result

    async def _translate_pending(
        self, to_translate: list[str], result: dict[str, str]
    ) -> None:
        """Traduce los títulos pendientes por lotes, con retry de los que no parsean.

        Escribe en `result` y en la caché. Propaga APIStatusError (p.ej. 401/403)
        para que el llamante aborte: un error de auth fallaría igual en cada lote.
        """
        # Batch LLM calls (25 titles per request)
        failed_titles: list[str] = []
        for i in range(0, len(to_translate), 25):
            batch = to_translate[i : i + 25]
            translations = await self._translate_batch(batch)
            for title in batch:
                translated = translations.get(title)
                if translated is None:
                    # Primer intento fallido — acumular para retry
                    failed_titles.append(title)
                else:
                    result[title] = translated
                    await self._set_cached(title, translated)

        # Retry con prompt simplificado para los que fallaron
        if not failed_titles:
            return
        logger.warning(
            "Retry de traducción para %d títulos tras fallo de parsing",
            len(failed_titles),
        )
        for i in range(0, len(failed_titles), 25):
            batch = failed_titles[i : i + 25]
            translations = await self._translate_batch(batch, retry=True)
            for title in batch:
                translated = translations.get(title, title)
                result[title] = translated
                if translated != title:
                    await self._set_cached(title, translated)

    async def _translate_batch(
        self, titles: list[str], *, retry: bool = False
    ) -> dict[str, str]:
        """Send a single Groq request to translate a batch of titles.

        Returns dict mapping original_title → translated (only successfully parsed ones).
        On failure devuelve dict vacío (no title → title fallback aquí).
        """
        index_to_title = {str(i): t for i, t in enumerate(titles)}
        user_prompt = json.dumps(index_to_title, ensure_ascii=False)
        system_prompt = _RETRY_SYSTEM_PROMPT if retry else TRANSLATION_SYSTEM_PROMPT

        try:
            response = await self._groq.get_chat_response(
                user_message=user_prompt,
                system_prompt=system_prompt,
                model=settings.GROQ_RERANK_MODEL,
                temperature=0.05,
                max_tokens=2048,
            )
            parsed = self._parse_response(response, index_to_title)
            # Validar que el resultado tiene sentido (no devuelve None ni vacío)
            return {t: v for t, v in parsed.items() if v and v.strip()}
        except APIStatusError as e:
            # 401/403 (key inválida/sin permisos): propagar — no tiene sentido
            # reintentar ni seguir con más lotes, fallarían igual.
            if e.status_code in (401, 403):
                raise
            # 429/5xx u otros: transitorio → dict vacío (retry/fallback lo cubren).
            logger.warning(
                "Groq HTTP %s en batch de traducción (retry=%s)", e.status_code, retry
            )
            return {}
        except Exception:
            logger.exception("Translation batch failed (retry=%s)", retry)
            return {}

    @staticmethod
    def _parse_response(
        raw: str,
        index_to_title: dict[str, str],
    ) -> dict[str, str]:
        """Parse LLM JSON response back to original_title -> translated."""
        text = raw.strip()
        # Eliminar markdown fences si los hay
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        # Intentar extraer JSON aunque haya texto antes/después
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

        try:
            translated_map = json.loads(text)
        except json.JSONDecodeError:
            # Un único fallo (p.ej. truncado por max_tokens) tiraría el lote entero
            # de 25 títulos. Rescatamos los pares "idx":"valor" bien formados.
            translated_map = _salvage_pairs(text)
            if not translated_map:
                logger.warning("Failed to parse translation JSON: %.200s", text)
                return {}
            logger.warning(
                "JSON de traducción malformado; rescatados %d de %d títulos",
                len(translated_map),
                len(index_to_title),
            )

        result: dict[str, str] = {}
        for idx, original in index_to_title.items():
            translated = translated_map.get(idx)
            if translated:
                result[original] = str(translated).strip()
        return result

    @classmethod
    def _resolve_language(cls, title: str, db_lang: str) -> str:
        """Determina el idioma real de un título, corrigiendo errores del campo BD.

        Estrategia:
        1. Si hay caracteres especiales DE/FR/IT en el título → ese idioma (fiable 100%).
        2. Si el título tiene tokens largos (compound words alemanas) → "de".
        3. Si la BD dice "en"/"es" pero no hay evidencia → desconfiamos y devolvemos "".
           Esto fuerza que el título se envíe al LLM (que lo devuelve igual si es inglés).
        4. Si langdetect tiene alta confianza (≥0.65) → usamos ese resultado.
        """
        # Reglas 1-2: heurística de caracteres/tokens (fiable, sin langdetect).
        by_chars = cls._lang_from_chars(title)
        if by_chars:
            return by_chars

        # Regla 3: si la BD dice EN/ES, solo lo confirmamos con langdetect ALTO y
        # coincidente; si no, "" → el LLM decide (devuelve el título igual si es EN).
        if db_lang.lower() in SKIP_LANGUAGES:
            return cls._langdetect_lang(
                title, min_prob=0.70, restrict_to=SKIP_LANGUAGES
            )

        # Regla 4: idiomas no-EN con confianza moderada.
        return cls._langdetect_lang(title, min_prob=0.35)

    @staticmethod
    def _lang_from_chars(title: str) -> str:
        """Reglas 1-2: caracteres especiales DE/FR/IT o tokens largos (alemán).

        Devuelve el código de idioma o "" si la heurística de caracteres no decide.
        """
        chars = set(title)
        if chars & _GERMAN_CHARS:
            return "de"
        if chars & _FRENCH_CHARS:
            return "fr"
        if chars & _ITALIAN_CHARS:
            return "it"
        # Tokens muy largos → probable alemán (compound words). Asterisco y slash
        # son marcadores de género suizos — eliminar antes de medir longitud.
        clean_words = title.replace("*", "").replace("/", " ").split()
        if any(len(w) > 12 for w in clean_words):
            return "de"
        return ""

    @staticmethod
    def _langdetect_lang(
        title: str, min_prob: float, restrict_to: frozenset[str] | None = None
    ) -> str:
        """Idioma top de langdetect si supera `min_prob` (y está en `restrict_to`).

        Devuelve "" si langdetect falla, no hay confianza suficiente o el idioma
        no está en el conjunto restringido.
        """
        try:
            results = detect_langs(title)
        except LangDetectException:
            return ""
        if not results or results[0].prob < min_prob:
            return ""
        top = results[0]
        if restrict_to is not None and top.lang not in restrict_to:
            return ""
        return top.lang

    @classmethod
    def _detect_language(cls, text: str) -> str:
        """Detecta idioma de un texto corto. Wrapper para compatibilidad externa."""
        return cls._resolve_language(text, "")

    # --- Redis cache helpers ---

    @staticmethod
    def _cache_key(title: str) -> str:
        h = hashlib.md5(title.encode()).hexdigest()  # noqa: S324
        return f"{CACHE_PREFIX}{h}"

    async def _get_cached(self, title: str) -> str | None:
        if not self._groq.redis:
            return None
        try:
            data = await self._groq.redis.get(self._cache_key(title))
            if data:
                return data.decode() if isinstance(data, bytes) else data
        except Exception:
            logger.debug("Translation cache read failed for %.50s", title)
        return None

    async def _set_cached(self, title: str, translated: str) -> None:
        if not self._groq.redis:
            return
        try:
            await self._groq.redis.set(
                self._cache_key(title), translated, ex=CACHE_TTL_SECONDS
            )
        except Exception:
            logger.debug("Translation cache write failed for %.50s", title)
