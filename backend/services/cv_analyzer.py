"""CVAnalyzer — extrae campos estructurados del perfil desde el texto del CV vía LLM.

La salida es SIEMPRE en inglés (aunque el CV esté en otro idioma) para que las
búsquedas de empleo y el matching sean consistentes. Patrón Gemini primario + Groq
de fallback, igual que DocumentGeneratorService: si Gemini no está o falla, cae a
Groq; si ninguno responde, devuelve {} y el llamante deja el perfil como estaba.
"""

import json
import logging
from typing import Any

from config import settings
from models.enums import RemotePreference

logger = logging.getLogger(__name__)

_VALID_REMOTE = {r.value for r in RemotePreference}

_SYSTEM_PROMPT = """You are an expert résumé/CV analyst for a job-matching platform.
Extract the candidate's profile from the CV text as a STRICT JSON object, IN ENGLISH
(translate any content written in another language).

Return ONLY a JSON object with EXACTLY these keys (use empty/neutral values if unknown):
- "title": concise professional headline in English, max ~80 chars (e.g. "Bilingual Executive Assistant & Translator").
- "skills": array of 10-25 concrete skills/tools/competencies in English (short phrases), most relevant first.
- "languages": array of languages the candidate speaks/writes, English names (e.g. "English", "Spanish", "German").
- "experience_years": integer, total years of professional experience (0 if unknown).
- "locations": array of places the candidate can work from; include "Remote" if they work or seek remote work.
- "remote_pref": one of "remote_only", "hybrid", "onsite", "any" — infer from the CV ("any" if unclear).

Respond with ONLY the JSON object. No markdown fences, no comments, no extra text."""


class CVAnalyzer:
    """Analiza el CV y devuelve campos de perfil normalizados (en inglés)."""

    def __init__(self, groq: Any, gemini: Any | None = None):
        self.groq = groq
        self.gemini = gemini

    @property
    def is_available(self) -> bool:
        groq_ok = bool(self.groq and self.groq.is_available)
        gemini_ok = bool(self.gemini and getattr(self.gemini, "is_available", False))
        return groq_ok or gemini_ok

    async def extract_fields(self, cv_text: str) -> dict[str, Any]:
        """Devuelve un dict con los campos extraídos (ya normalizados). {} si falla."""
        if not cv_text or not self.is_available:
            return {}
        prompt = (
            "Extract the profile fields from this CV as JSON:\n\n"
            f"{cv_text[:6000]}"
        )
        try:
            raw = await self._call_llm(prompt)
        except Exception:
            logger.warning("CVAnalyzer: ningún proveedor LLM respondió", exc_info=True)
            return {}
        return self._normalize(self._parse_json(raw))

    async def _call_llm(self, user_prompt: str) -> str:
        """Gemini primario (calidad), Groq de fallback. Lanza si ninguno responde."""
        if self.gemini is not None and getattr(self.gemini, "is_available", False):
            try:
                return await self.gemini.get_chat_response(
                    user_message=user_prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    temperature=0.2,
                    max_tokens=1024,
                )
            except Exception:
                logger.warning(
                    "CVAnalyzer: Gemini falló; fallback a Groq", exc_info=True
                )

        if self.groq is not None and self.groq.is_available:
            return await self.groq.get_chat_response(
                user_message=user_prompt,
                system_prompt=_SYSTEM_PROMPT,
                model=settings.GROQ_MODEL,
                temperature=0.2,
                max_tokens=1024,
            )
        raise RuntimeError("No hay proveedor LLM disponible para analizar el CV")

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """Parsea la respuesta del LLM a dict, tolerando fences markdown y texto extra."""
        if not text:
            return {}
        s = text.strip()
        if s.startswith("```"):
            s = s.split("\n", 1)[-1]
            if s.endswith("```"):
                s = s[:-3]
        # Recorta a las llaves externas por si el modelo añadió prosa.
        start, end = s.find("{"), s.rfind("}")
        if start != -1 and end != -1 and end > start:
            s = s[start : end + 1]
        try:
            data = json.loads(s)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            logger.warning("CVAnalyzer: respuesta del LLM no es JSON válido")
            return {}

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        """Coacciona tipos y valida rangos. Solo incluye claves con valor útil."""
        out: dict[str, Any] = {}

        title = data.get("title")
        if isinstance(title, str) and title.strip():
            out["title"] = title.strip()[:200]

        for key in ("skills", "languages", "locations"):
            val = data.get(key)
            if isinstance(val, list):
                items = [str(x).strip() for x in val if str(x).strip()]
                # dedupe preservando orden, tope 50 (mismo límite que ProfileUpdate)
                seen: set[str] = set()
                deduped = []
                for it in items:
                    low = it.lower()
                    if low not in seen:
                        seen.add(low)
                        deduped.append(it)
                if deduped:
                    out[key] = deduped[:50]

        exp = data.get("experience_years")
        if isinstance(exp, bool):  # bool es subclase de int; descártalo
            exp = None
        if isinstance(exp, (int, float)):
            out["experience_years"] = max(0, min(50, int(exp)))

        remote = data.get("remote_pref")
        if isinstance(remote, str) and remote.strip().lower() in _VALID_REMOTE:
            out["remote_pref"] = remote.strip().lower()

        return out
