"""Script de configuración del perfil de Alicia Moore.

Configura el perfil de usuario con las skills, idiomas y preferencias derivadas
del análisis maestro de empleabilidad (ANALISIS_MAESTRO_EMPLEABILIDAD_ALICIA_MOORE.md).
Crea además las búsquedas guardadas para las 7 categorías prioritarias del análisis.

Uso:
    docker compose exec backend python scripts/setup_profile_alicia.py --email <email>
    docker compose exec backend python scripts/setup_profile_alicia.py --email <email> --dry-run
"""

import argparse
import asyncio
import json
import logging
import sys

sys.path.insert(0, "/app")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Perfil base derivado del análisis maestro de empleabilidad
# ---------------------------------------------------------------------------

PROFILE_DATA = {
    "title": "Bilingual Content Specialist | L&D Professional | Native English · Spanish C1",
    "skills": [
        # Categoría A — Edición, localización y revisión lingüística (máxima afinidad)
        "Content Editor",
        "Proofreader",
        "Copy Editor",
        "Localization Specialist",
        "LQA",
        "Linguistic Quality Assurance",
        "Technical Writer",
        "Post-editing",
        # Categoría B — IA y evaluación de datos (demanda inmediata)
        "RLHF",
        "AI Evaluator",
        "Data Annotation",
        "Search Quality Rater",
        "Prompt Engineer",
        # Categoría C — Administración y operaciones
        "Virtual Assistant",
        "Executive Assistant",
        "Project Coordinator",
        "Operations Coordinator",
        "Event Coordinator",
        # Categoría D — RRHH y formación corporativa
        "Instructional Design",
        "eLearning",
        "L&D",
        "HR Coordinator",
        "Onboarding",
        "Training Coordinator",
        "Payroll",
        # Categoría E — Customer Success
        "Customer Success",
        "Client Relations",
        # Certificaciones y competencias diferenciadoras
        "CELTA",
        "TEFL",
        "TESOL",
        "IPGCE",
        "Google Educator",
        # Herramientas
        "Google Workspace",
        "Microsoft Office",
        "Education Perfect",
        "Google Classroom",
        "Moodle",
        "HubSpot",
        "CRM",
    ],
    "languages": [
        "English",    # Nativo — diferenciador global
        "Spanish",    # C1 — dominio profesional completo
        "Japanese",   # B1 — diferenciador estratégico en localización
        "French",     # A2 — potencial eje Ginebra-Bruselas
    ],
    "locations": [
        "Remote",
        "Switzerland",
        "Geneva",
        "Zurich",
        "Spain",
    ],
    "salary_min": 45000,   # CHF/year — entrada para roles remote EN/ES bilingüe
    "salary_max": 85000,   # CHF/year — techo Instructional Designer senior (EE.UU.: hasta 85k USD)
    "remote_pref": "remote_only",
    # Pesos de scoring ajustados al perfil de Alicia:
    # - Embedding: base semántica del perfil (título + CV)
    # - Language: inglés nativo es diferenciador clave en muchos roles
    # - LLM: crítico para evaluar roles no técnicos donde el embedding puede fallar
    # - Recency: el mercado remoto cambia rápido, priorizamos ofertas frescas
    # - Salary: muchos roles no publican salario → reducimos su peso
    # - Location: búsqueda 100% remote → prácticamente irrelevante
    "score_weights": {
        "embedding": 0.35,
        "language": 0.15,
        "llm": 0.25,
        "recency": 0.15,
        "salary": 0.07,
        "location": 0.03,
    },
}

# ---------------------------------------------------------------------------
# Búsquedas guardadas — 7 categorías prioritarias del análisis
# ---------------------------------------------------------------------------

SAVED_SEARCHES = [
    {
        "name": "AI Evaluator / RLHF / Search Quality Rater",
        "filters": {
            "q": "AI evaluator OR RLHF OR search quality rater OR data annotator OR content evaluator OR AI trainer",
            "remote_only": True,
        },
        "min_score": 30,
        "notify_frequency": "daily",
    },
    {
        "name": "Content Editor / Localization / LQA",
        "filters": {
            "q": "content editor OR localization specialist OR LQA OR proofreader OR copy editor OR linguistic quality OR post-editor OR MTPE",
            "remote_only": True,
        },
        "min_score": 30,
        "notify_frequency": "daily",
    },
    {
        "name": "Proofreader / Copy Editor (native English)",
        "filters": {
            "q": "proofreader OR copy editor OR native English editor OR editorial assistant OR copywriter",
            "remote_only": True,
        },
        "min_score": 25,
        "notify_frequency": "daily",
    },
    {
        "name": "Bilingual Executive Virtual Assistant (EN/ES)",
        "filters": {
            "q": "virtual assistant OR executive assistant bilingual OR VA bilingual OR remote assistant",
            "remote_only": True,
        },
        "min_score": 30,
        "notify_frequency": "daily",
    },
    {
        "name": "Instructional Designer / L&D Coordinator",
        "filters": {
            "q": "instructional designer OR L&D coordinator OR eLearning developer OR learning and development OR training coordinator",
            "remote_only": True,
        },
        "min_score": 30,
        "notify_frequency": "weekly",
    },
    {
        "name": "Content Writer EdTech / HR",
        "filters": {
            "q": "content writer edtech OR content specialist education OR HR content OR educational content creator",
            "remote_only": True,
        },
        "min_score": 25,
        "notify_frequency": "weekly",
    },
    {
        "name": "HR Coordinator / Training Coordinator",
        "filters": {
            "q": "HR coordinator OR HR administrator OR training coordinator OR onboarding specialist OR people operations",
            "remote_only": True,
        },
        "min_score": 25,
        "notify_frequency": "weekly",
    },
    {
        "name": "Customer Success / Bilingual Support",
        "filters": {
            "q": "customer success OR customer support bilingual OR VIP client relations OR guest experience OR concierge virtual OR client relations coordinator",
            "remote_only": True,
        },
        "min_score": 30,
        "notify_frequency": "daily",
    },
    {
        "name": "Organismos Internacionales / ONG (Ginebra)",
        "filters": {
            "q": "language assistant OR documentation assistant OR programme assistant OR UN OR UNESCO OR ILO OR NGO administrative",
            "remote_only": False,
            "canton": "GE",
        },
        "min_score": 20,
        "notify_frequency": "weekly",
    },
]


async def _update_profile(user_id: str, db) -> None:
    """Actualiza el perfil del usuario con los datos de Alicia."""
    from sqlalchemy import select
    from models.user_profile import UserProfile

    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        logger.info("Creando perfil nuevo para user_id=%s", user_id)
    else:
        logger.info("Actualizando perfil existente para user_id=%s", user_id)

    profile.title = PROFILE_DATA["title"]
    profile.skills = PROFILE_DATA["skills"]
    profile.languages = PROFILE_DATA["languages"]
    profile.locations = PROFILE_DATA["locations"]
    profile.salary_min = PROFILE_DATA["salary_min"]
    profile.salary_max = PROFILE_DATA["salary_max"]
    profile.remote_pref = PROFILE_DATA["remote_pref"]
    profile.score_weights = PROFILE_DATA["score_weights"]

    await db.flush()
    logger.info("Perfil configurado: %d skills, %d idiomas", len(profile.skills), len(profile.languages))


async def _create_saved_searches(user_id: str, db) -> None:
    """Crea las búsquedas guardadas para las categorías prioritarias."""
    from sqlalchemy import select, delete
    from models.saved_search import SavedSearch

    # Eliminar búsquedas anteriores del script (identificadas por prefijo conocido)
    existing = await db.execute(select(SavedSearch).where(SavedSearch.user_id == user_id))
    count_existing = len(existing.scalars().all())
    if count_existing > 0:
        logger.info("Eliminando %d búsquedas guardadas existentes", count_existing)
        await db.execute(delete(SavedSearch).where(SavedSearch.user_id == user_id))

    for search_data in SAVED_SEARCHES:
        search = SavedSearch(
            user_id=user_id,
            name=search_data["name"],
            filters=search_data["filters"],
            min_score=search_data["min_score"],
            notify_frequency=search_data["notify_frequency"],
            notify_push=True,
            is_active=True,
        )
        db.add(search)
        logger.info("  + Búsqueda: %s", search_data["name"])

    await db.flush()
    logger.info("%d búsquedas guardadas creadas", len(SAVED_SEARCHES))


async def run(email: str, dry_run: bool = False) -> None:
    """Ejecuta la configuración del perfil."""
    from database import task_session
    from sqlalchemy import select
    from models.user import User

    async with task_session() as db:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user is None:
            logger.error("Usuario no encontrado con email: %s", email)
            sys.exit(1)

        logger.info("Usuario encontrado: %s (id=%s)", user.email, user.id)

        if dry_run:
            logger.info("--- DRY RUN — no se realizarán cambios ---")
            logger.info("Perfil a configurar:")
            logger.info("  title: %s", PROFILE_DATA["title"])
            logger.info("  skills (%d): %s", len(PROFILE_DATA["skills"]), ", ".join(PROFILE_DATA["skills"][:5]) + "...")
            logger.info("  languages: %s", PROFILE_DATA["languages"])
            logger.info("  locations: %s", PROFILE_DATA["locations"])
            logger.info("  salary: %s–%s CHF/year", PROFILE_DATA["salary_min"], PROFILE_DATA["salary_max"])
            logger.info("  remote_pref: %s", PROFILE_DATA["remote_pref"])
            logger.info("  score_weights: %s", json.dumps(PROFILE_DATA["score_weights"]))
            logger.info("Búsquedas a crear: %d", len(SAVED_SEARCHES))
            for s in SAVED_SEARCHES:
                logger.info("  - %s (min_score=%s, freq=%s)", s["name"], s["min_score"], s["notify_frequency"])
            return

        await _update_profile(str(user.id), db)
        await _create_saved_searches(str(user.id), db)
        await db.commit()
        logger.info("✓ Configuración completada.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Configurar perfil de búsqueda de empleo para Alicia Moore")
    parser.add_argument("--email", required=True, help="Email del usuario a configurar")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar qué se haría sin aplicar cambios")
    args = parser.parse_args()

    asyncio.run(run(email=args.email, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
