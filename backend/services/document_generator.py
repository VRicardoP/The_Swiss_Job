"""DocumentGeneratorService — AI-powered CV and cover letter generation.

Uses GroqService (composition) with llama-3.3-70b-versatile for generating
tailored CVs and cover letters in Markdown format.
"""

from __future__ import annotations

import hashlib
import logging

from config import settings
from services.groq_service import GroqService

logger = logging.getLogger(__name__)


CV_SYSTEM_PROMPT = """You are an expert career consultant specializing in the Swiss job market. \
Your task is to adapt a candidate's CV to a specific job posting.

RULES:
1. Output a complete CV in Markdown format
2. Restructure the candidate's experience, skills, and achievements to emphasize \
what is most relevant to the target job
3. Use keywords from the job posting naturally throughout the CV
4. Highlight matching skills prominently; downplay or omit irrelevant experience
5. Quantify achievements wherever possible (percentages, revenue, team sizes, etc.)
6. Keep the CV truthful — never invent experience or skills the candidate does not have
7. Structure: Professional Summary (3-4 lines tailored to this role), Key Skills \
(relevant ones first), Professional Experience (most relevant first, with bullet \
points focused on matching achievements), Education, Languages, Certifications (if any)
8. The CV should be 1-2 pages when rendered
9. Write in {language}
10. For Swiss market: use European date format, formal tone

IMPORTANT: Generate ONLY the Markdown content. No preamble, no explanation, no commentary."""


COVER_LETTER_SYSTEM_PROMPT = """You are an expert career consultant specializing in the Swiss \
job market. Your task is to write a personalized cover letter for a specific job posting.

RULES:
1. Output a complete cover letter in Markdown format
2. Address the specific company and role by name
3. Opening paragraph: express genuine interest in the specific role and company
4. Body paragraphs (2-3): connect the candidate's most relevant experience and \
achievements directly to the job requirements. Use specific examples and metrics \
where available
5. Highlight skills that match the job posting keywords
6. Address any key requirements from the job description specifically
7. Closing paragraph: express enthusiasm, mention availability, call to action
8. Keep it concise: 300-400 words maximum
9. Tone: professional but personal, confident but not arrogant
10. Write in {language}
11. For Swiss market: be formal but warm, respect cultural norms \
(Sehr geehrte Damen und Herren for German, etc.)
12. Include date and address placeholders in the header

IMPORTANT: Generate ONLY the Markdown content. No preamble, no explanation, no commentary."""


class DocumentGeneratorService:
    """Generates tailored CVs and cover letters using LLM."""

    def __init__(self, groq: GroqService) -> None:
        self.groq = groq

    async def generate_cv(
        self,
        cv_text: str,
        skills: list[str],
        job_title: str,
        job_company: str,
        job_description: str,
        job_tags: list[str],
        matching_skills: list[str] | None = None,
        missing_skills: list[str] | None = None,
        language: str = "en",
    ) -> str:
        """Generate a tailored CV in Markdown format."""
        system_prompt = CV_SYSTEM_PROMPT.replace(
            "{language}", self._language_name(language)
        )
        user_prompt = self._build_cv_prompt(
            cv_text,
            skills,
            job_title,
            job_company,
            job_description,
            job_tags,
            matching_skills,
            missing_skills,
        )
        return await self._call_llm(system_prompt, user_prompt)

    async def generate_cover_letter(
        self,
        cv_text: str,
        skills: list[str],
        job_title: str,
        job_company: str,
        job_description: str,
        job_tags: list[str],
        matching_skills: list[str] | None = None,
        missing_skills: list[str] | None = None,
        language: str = "en",
    ) -> str:
        """Generate a tailored cover letter in Markdown format."""
        system_prompt = COVER_LETTER_SYSTEM_PROMPT.replace(
            "{language}", self._language_name(language)
        )
        user_prompt = self._build_cover_letter_prompt(
            cv_text,
            skills,
            job_title,
            job_company,
            job_description,
            job_tags,
            matching_skills,
            missing_skills,
        )
        return await self._call_llm(system_prompt, user_prompt)

    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        """Call Groq LLM with the heavy model for document generation."""
        return await self.groq.get_chat_response(
            user_message=user_prompt,
            system_prompt=system_prompt,
            model=settings.GROQ_MODEL,  # llama-3.3-70b-versatile
            temperature=settings.GROQ_DOC_TEMPERATURE,
            max_tokens=settings.GROQ_DOC_MAX_TOKENS,
        )

    @staticmethod
    def _build_cv_prompt(
        cv_text: str,
        skills: list[str],
        job_title: str,
        job_company: str,
        job_description: str,
        job_tags: list[str],
        matching_skills: list[str] | None,
        missing_skills: list[str] | None,
    ) -> str:
        skills_text = ", ".join(skills) if skills else "Not specified"
        tags_text = ", ".join(job_tags) if job_tags else "Not specified"
        matching_text = (
            ", ".join(matching_skills) if matching_skills else "Not analyzed"
        )
        missing_text = ", ".join(missing_skills) if missing_skills else "Not analyzed"

        return (
            f"## Candidate's Current CV\n"
            f"{cv_text[:6000]}\n\n"
            f"## Candidate's Skills\n{skills_text}\n\n"
            f"## Target Job\n"
            f"**Title:** {job_title}\n"
            f"**Company:** {job_company}\n"
            f"**Required Skills/Tags:** {tags_text}\n\n"
            f"## Job Description\n{job_description[:4000]}\n\n"
            f"## Match Analysis\n"
            f"**Matching Skills:** {matching_text}\n"
            f"**Missing Skills (do NOT invent these):** {missing_text}\n\n"
            f"Generate the tailored CV now."
        )

    @staticmethod
    def _build_cover_letter_prompt(
        cv_text: str,
        skills: list[str],
        job_title: str,
        job_company: str,
        job_description: str,
        job_tags: list[str],
        matching_skills: list[str] | None,
        missing_skills: list[str] | None,
    ) -> str:
        skills_text = ", ".join(skills) if skills else "Not specified"
        tags_text = ", ".join(job_tags) if job_tags else "Not specified"
        matching_text = (
            ", ".join(matching_skills) if matching_skills else "Not analyzed"
        )

        return (
            f"## Candidate's Background\n"
            f"{cv_text[:4000]}\n\n"
            f"## Candidate's Skills\n{skills_text}\n\n"
            f"## Target Job\n"
            f"**Title:** {job_title}\n"
            f"**Company:** {job_company}\n"
            f"**Required Skills/Tags:** {tags_text}\n\n"
            f"## Job Description\n{job_description[:3000]}\n\n"
            f"## Skills That Match\n{matching_text}\n\n"
            f"Generate the cover letter now."
        )

    @staticmethod
    def _language_name(code: str) -> str:
        return {
            "en": "English",
            "de": "German (formal Swiss German style)",
            "fr": "French (formal Swiss French style)",
            "it": "Italian",
        }.get(code, "English")

    @staticmethod
    def cache_key(user_id: str, job_hash: str, doc_type: str, language: str) -> str:
        """Generate Redis cache key for generated documents."""
        raw = f"{user_id}:{job_hash}:{doc_type}:{language}"
        h = hashlib.md5(raw.encode()).hexdigest()
        return f"gendoc:{h}"
