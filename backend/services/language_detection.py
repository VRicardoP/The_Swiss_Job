"""LanguageDetector — idioma de un título de oferta, para el indicador de la UI.

Lo llama SOLO `tasks.language_tasks`, en el worker, y el resultado se persiste
por título en `job_title_languages`. **Nunca desde el camino de una petición**
(invariantes 4 y 5 de `CLAUDE.md`).

D10 (2026-11-26): este módulo era `translation_service.py` y además traducía
títulos a inglés con Groq (calentador de fondo, `translate=true` del router y
una caché Redis de 30 días `translate:title:*`). Esa traducción la hace ahora el
CORE una vez por título y la sirve como `VacancyDTO.title_en`; aquí sólo queda
la detección de idioma.
"""

import functools

from langdetect import detect_langs
from langdetect.lang_detect_exception import LangDetectException

# EN/ES: un `language` de origen que los declare sólo se acepta si langdetect
# lo confirma con confianza alta (regla 3 de `_resolve_language`).
SKIP_LANGUAGES = frozenset({"en", "es"})

# Caracteres característicos de idiomas no-inglés frecuentes en ofertas suizas
_GERMAN_CHARS = frozenset("äöüÄÖÜß")
_FRENCH_CHARS = frozenset("éèêëàâîïùûçœæÉÈÊÀÂÎÙÛÇ")
_ITALIAN_CHARS = frozenset("àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ")


class LanguageDetector:
    """Detección de idioma de títulos: heurística de caracteres + langdetect."""

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
    @functools.lru_cache(maxsize=20000)
    def _detect_language(cls, text: str) -> str:
        """Detecta idioma de un texto corto. Wrapper para compatibilidad externa.

        DONDE SE LLAMA, que es lo que importa: desde `tasks.language_tasks`, en
        el worker. **Nunca desde el camino de una peticion.** Hasta el
        2026-09-23 lo llamaba el router una vez POR OFERTA SERVIDA, tambien con
        `translate=false`, porque el indicador de idioma de la UI lo necesita:
        el 96,9 % de las 1.800 ofertas del feed llega SIN `language` y cada
        deteccion cuesta 50,1 ms — ~90 s por peticion, el cuello real de
        `GET /match/results?limit=3000` (79 s extremo a extremo). Hoy el
        resultado se PERSISTE por titulo (`models/title_language.py`) y servir
        solo lee.

        QUE GARANTIZA, exactamente: reutiliza la PRIMERA respuesta de cada
        titulo mientras siga en la cache de ESTE proceso. Ni mas ni menos.
        NO es una funcion pura, aunque su firma lo parezca: langdetect no es
        determinista en titulos cortos —30 vaciados de cache de «Sviluppatore
        software» dieron en=21, sv=6, it=3— asi que sin memoizar el indicador
        de idioma de la UI podia cambiar entre dos cargas de la misma oferta.
        La cache ESTABILIZA esa respuesta; no la hace correcta, ni la hace
        igual entre workers, reinicios o expulsiones. Cualquier afirmacion de
        equivalencia entre procesos seria falsa.

        `maxsize` acota la memoria. La memoizacion SE QUEDA, pero ya es de
        segundo orden: ahorra repetir un titulo dentro del mismo lote de la
        tarea. Lo que resuelve la PRIMERA carga —que una cache en proceso no
        podia resolver, porque cada arranque o expulsion la vaciaba— es la
        persistencia.
        """
        return cls._resolve_language(text, "")
