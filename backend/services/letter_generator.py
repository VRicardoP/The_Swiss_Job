"""LetterGenerator — borrador de carta de presentación para candidatura.

Dos plantillas placeholder (A y B) que el usuario reemplazará después con
el contenido final. Mientras tanto generan un borrador estructurado usando
Groq con el contexto del colegio, el job y el perfil del usuario.

Plantilla A: tono urbano/formal — colegios grandes, formales, ciudad.
Plantilla B: tono cálido/personalizado — boarding, internacionales pequeños,
            alpinos, familiares.

Los placeholders se rellenan con metadata del colegio + perfil. El párrafo
del "school-specific hook" (párrafo 3) se deja con un marcador explícito
[REVISAR: añadir referencia específica al colegio] porque el doc dice
que esa parte NO debe automatizarse.
"""

import asyncio
import logging

from scrapers.swiss_schools_config import WatchedSchool

logger = logging.getLogger(__name__)

# Timeout duro para evitar workers colgados si Groq se atasca.
_GROQ_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT_A = """\
Eres un asistente que redacta cartas de presentación PROFESIONALES y FORMALES
para candidaturas a colegios internacionales urbanos en Suiza. Tono:
sobrio, directo, énfasis en cualificaciones y profesionalidad.

ESTRUCTURA OBLIGATORIA (cuatro párrafos):
1. Apertura formal: saludo + motivo de la candidatura (el rol/spontaneous).
2. Resumen de cualificaciones relevantes del perfil del candidato.
3. Marcador literal: [REVISAR: añadir referencia específica al colegio]
   — el usuario rellenará el gancho específico del centro a mano.
4. Cierre profesional + disponibilidad para entrevista.

IDIOMA: si la oferta está en EN responde en EN; si está en FR responde en FR;
si está en DE responde en DE.

REGLAS:
- No inventes experiencia, certificaciones ni hechos no presentes en el perfil.
- NO uses emoji, exclamaciones excesivas ni jerga.
- Longitud objetivo: 250-350 palabras.
- NO incluyas la firma final (la añade el usuario).
"""

_SYSTEM_PROMPT_B = """\
Eres un asistente que redacta cartas de presentación CÁLIDAS Y PERSONALIZADAS
para candidaturas a colegios internacionales boarding, alpinos o pequeños
familiares en Suiza. Tono: humano, cercano, énfasis en encaje cultural y
motivación personal por el entorno del colegio.

ESTRUCTURA OBLIGATORIA (cuatro párrafos):
1. Apertura cercana: saludo + por qué este colegio en concreto te atrae.
2. Conexión entre tu trayectoria y los valores del colegio (boarding,
   pequeño, internacional, comunidad).
3. Marcador literal: [REVISAR: añadir referencia específica al colegio]
   — el usuario rellenará el detalle concreto del centro.
4. Cierre cálido + disponibilidad para una conversación, no entrevista formal.

IDIOMA: si la oferta está en EN responde en EN; si está en FR responde en FR;
si está en DE responde en DE.

REGLAS:
- No inventes experiencia ni certificaciones.
- Permite alguna referencia al lugar (Alpes, ambiente boarding, etc.) si es
  apropiado, pero sin caer en clichés.
- Longitud objetivo: 250-350 palabras.
- NO incluyas la firma final.
"""


async def generate_draft_letter(
    *,
    groq,
    school: WatchedSchool,
    job,
    profile,
    template_id: str,
) -> str:
    """Llama a Groq para producir el borrador. Devuelve texto plano."""
    if not groq.is_available:
        # Fallback sin LLM: devuelve plantilla mínima rellenada
        return _fallback_template(school, job, profile, template_id)

    system = _SYSTEM_PROMPT_A if template_id == "A" else _SYSTEM_PROMPT_B
    user_prompt = _build_user_prompt(school, job, profile)

    try:
        return await asyncio.wait_for(
            groq.get_chat_response(
                user_message=user_prompt,
                system_prompt=system,
                temperature=0.4,
                max_tokens=900,
            ),
            timeout=_GROQ_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "letter_generator: Groq timeout (%ss) — devolviendo fallback",
            _GROQ_TIMEOUT_SECONDS,
        )
        return _fallback_template(school, job, profile, template_id)


def _build_user_prompt(school: WatchedSchool, job, profile) -> str:
    salutation = (
        f"Estimado/a {school.contact_name}"
        if school.contact_name
        else "Estimado equipo de selección"
    )
    languages = ", ".join(profile.languages or []) or "no especificadas"
    skills = ", ".join((profile.skills or [])[:15]) or "no especificadas"
    candidate_title = profile.title or "el/la candidato/a"

    return f"""\
## Colegio
- Nombre: {school.name}
- Ciudad: {school.city}
- Tipo/notas: {school.notes or 'sin notas'}

## Oferta
- Título: {job.title}
- Ubicación: {job.location or school.city}
- URL: {job.url}
- Idioma del anuncio: {job.language or 'desconocido'}

## Perfil del candidato (resumen)
- Título profesional: {candidate_title}
- Skills: {skills}
- Idiomas: {languages}
- Años de experiencia: {profile.experience_years or 'no especificados'}

## Saludo
{salutation},

Genera el borrador siguiendo la estructura. Incluye el marcador
[REVISAR: añadir referencia específica al colegio] en el párrafo 3.
"""


def _fallback_template(
    school: WatchedSchool, job, profile, template_id: str
) -> str:
    """Plantilla mínima sin LLM (cuando Groq no está disponible)."""
    name = profile.title or "[nombre del candidato]"
    salutation = (
        f"Estimado/a {school.contact_name}"
        if school.contact_name
        else "Estimado equipo de selección"
    )
    return f"""\
{salutation},

Le escribo para postular al puesto de {job.title} en {school.name}
({school.city}).

Soy {name}. [REVISAR: añadir resumen de cualificaciones relevantes para
el rol concreto.]

[REVISAR: añadir referencia específica al colegio — qué te motiva de
{school.name} concretamente.]

Quedo a su disposición para una entrevista en el momento que les sea
conveniente.

Atentamente,
"""
