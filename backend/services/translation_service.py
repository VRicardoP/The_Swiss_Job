"""TranslationService — batch translation of job titles via Groq LLM.

Uses llama-3.1-8b-instant (fast model) for translating DE/FR/IT job titles
to English. Results are cached per-title in Redis with 30-day TTL.
"""

import hashlib
import json
import logging

from langdetect import detect_langs
from langdetect.lang_detect_exception import LangDetectException

from config import settings
from services.groq_service import GroqService

logger = logging.getLogger(__name__)

# Languages that do NOT need translation
SKIP_LANGUAGES = frozenset({"en", "es"})

CACHE_PREFIX = "translate:title:"
CACHE_TTL_SECONDS = 30 * 86_400  # 30 days

TRANSLATION_SYSTEM_PROMPT = """\
You are a professional translator specializing in Swiss job market titles.
Translate the following job titles to English.

RULES:
1. Translate ONLY the job title text — no explanations, no commentary.
2. Keep proper nouns, brand names, and technical acronyms unchanged \
(e.g. "KV", "ICT", "SAP", "ERP").
3. Preserve percentage ranges like "80-100%" as-is.
4. If the title is already in English, return it unchanged.
5. Return ONLY a JSON object mapping the index to its English translation.

Example input: {"0": "Sachbearbeiter/in Finanzbuchhaltung 80-100%", \
"1": "Développeur Full Stack Senior"}
Example output: {"0": "Financial Accounting Clerk 80-100%", \
"1": "Senior Full Stack Developer"}

Respond with ONLY the JSON object. No markdown fences."""


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
            Titles in EN/ES or that fail translation are returned as-is.
        """
        result: dict[str, str] = {}
        to_translate: list[str] = []

        if not self._groq.is_available:
            return {item["title"]: item["title"] for item in titles_with_lang}

        # Check cache and filter titles needing translation
        for item in titles_with_lang:
            title = item["title"]
            lang = (item.get("language") or "").lower()

            # Detect language from title text when DB field is empty
            if not lang and title:
                lang = self._detect_language(title)

            if not title or lang in SKIP_LANGUAGES:
                result[title] = title
                continue

            cached = await self._get_cached(title)
            if cached is not None:
                result[title] = cached
            else:
                to_translate.append(title)

        if not to_translate:
            return result

        # Batch LLM calls (25 titles per request)
        for i in range(0, len(to_translate), 25):
            batch = to_translate[i : i + 25]
            translations = await self._translate_batch(batch)
            for title in batch:
                translated = translations.get(title, title)
                result[title] = translated
                await self._set_cached(title, translated)

        return result

    async def _translate_batch(self, titles: list[str]) -> dict[str, str]:
        """Send a single Groq request to translate a batch of titles."""
        index_to_title = {str(i): t for i, t in enumerate(titles)}
        user_prompt = json.dumps(index_to_title, ensure_ascii=False)

        try:
            response = await self._groq.get_chat_response(
                user_message=user_prompt,
                system_prompt=TRANSLATION_SYSTEM_PROMPT,
                model=settings.GROQ_RERANK_MODEL,
                temperature=0.1,
                max_tokens=1024,
            )
            return self._parse_response(response, index_to_title)
        except Exception:
            logger.exception("Translation batch failed, returning originals")
            return {t: t for t in titles}

    @staticmethod
    def _parse_response(
        raw: str,
        index_to_title: dict[str, str],
    ) -> dict[str, str]:
        """Parse LLM JSON response back to original_title -> translated."""
        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:])
            if text.endswith("```"):
                text = text[:-3].strip()

        try:
            translated_map = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Failed to parse translation JSON: %.200s", text)
            return {t: t for t in index_to_title.values()}

        result: dict[str, str] = {}
        for idx, original in index_to_title.items():
            result[original] = translated_map.get(idx, original)
        return result

    @staticmethod
    def _detect_language(text: str) -> str:
        """Detect language of a short text using langdetect.

        Returns 2-letter language code or empty string on failure.
        """
        try:
            results = detect_langs(text)
            if results and results[0].prob >= 0.5:
                return results[0].lang
        except LangDetectException:
            pass
        return ""

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
