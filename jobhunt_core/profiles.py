"""Perfiles + revisiones versionadas (A-07, CONTRATOS §1 + ADR-02/03).

- `profiles` = identidad multi-tenant: UNIQUE(consumer_id, external_ref).
- `profile_revisions` = contenido INMUTABLE versionado por `content_hash` —
  hash del contenido CANÓNICO normalizado (misma disciplina que la canónica
  de ofertas, rev. A-06 2ª #2); `text_hash` = hash del TEXTO embebible
  EXACTO (misma composición que el legacy `profile_tasks`: title + cv_text +
  skills — vectores comparables en la sombra de Fase B).
- El perfil VIGENTE = su ÚLTIMA revisión (created_at, id): `profiles` no
  tiene puntero en el esquema ratificado; las evaluaciones (A-08) fijan la
  revisión usada vía FK compuesta (mismo perfil).
- La coerción de tipos es central y defensiva (lección A-05 #2): contenido
  basura degrada o devuelve None — jamás revienta al llamador.
"""

import hashlib
import json
import logging
import uuid

import sqlalchemy as sa

logger = logging.getLogger(__name__)

# Campos canónicos del contenido (espejo de user_profile del legacy).
CONTENT_FIELDS = (
    "title", "cv_text", "skills", "languages", "locations",
    "experience_years", "salary_min", "salary_max", "remote_pref",
)
# Campos que componen el TEXTO embebible (legacy profile_tasks:151):
# salario/idiomas/ubicaciones NO re-embeben.
TEXT_FIELDS = ("title", "cv_text", "skills")


def normalize_profile(content) -> dict | None:
    """Contenido canónico del perfil; None si no queda TEXTO embebible
    (un perfil sin texto no puede participar en el matching)."""
    if not isinstance(content, dict):
        return None
    out = {
        "title": _text(content.get("title")),
        "cv_text": _text(content.get("cv_text")),
        "skills": _str_list(content.get("skills")),
        "languages": _str_list(content.get("languages")),
        "locations": _str_list(content.get("locations")),
        "experience_years": _int(content.get("experience_years")),
        "salary_min": _int(content.get("salary_min")),
        "salary_max": _int(content.get("salary_max")),
        "remote_pref": _text(content.get("remote_pref")),
    }
    if not build_profile_text(out):
        logger.warning("profiles: contenido sin texto embebible — sin revisión")
        return None
    return out


def build_profile_text(content: dict) -> str:
    """Texto de embedding — MISMA composición que el legacy (title + cv_text
    + skills) para que los vectores sean comparables en la sombra de Fase B."""
    parts = [
        content.get("title") or "",
        content.get("cv_text") or "",
        " ".join(content.get("skills") or []),
    ]
    return " ".join(p for p in parts if p)


def profile_text_hash(content: dict) -> str:
    """Hash del texto embebible EXACTO (disciplina rev. A-06 2ª #5): deriva
    de la MISMA representación que alimenta al encoder."""
    return hashlib.sha256(build_profile_text(content).encode()).hexdigest()


def profile_content_hash(content: dict) -> str:
    """Hash del contenido CANÓNICO normalizado (identifica el resultado de la
    normalización; disciplina rev. A-06 2ª #2)."""
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


async def ensure_consumer(session, name: str, active: bool = True) -> uuid.UUID:
    """Alta idempotente del consumer (tenant). No muta uno existente."""
    await session.execute(
        sa.text(
            "INSERT INTO consumers (id, name, active) VALUES (:id, :name, :active) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "name": name, "active": active},
    )
    return (
        await session.execute(
            sa.text("SELECT id FROM consumers WHERE name = :name"), {"name": name}
        )
    ).scalar_one()


async def upsert_profile(session, consumer_id, external_ref: str) -> uuid.UUID:
    """Alta idempotente del perfil por (consumer, external_ref)."""
    await session.execute(
        sa.text(
            "INSERT INTO profiles (id, consumer_id, external_ref) "
            "VALUES (:id, :cid, :ref) "
            "ON CONFLICT (consumer_id, external_ref) DO NOTHING"
        ),
        {"id": uuid.uuid4(), "cid": consumer_id, "ref": external_ref},
    )
    return (
        await session.execute(
            sa.text(
                "SELECT id FROM profiles "
                "WHERE consumer_id = :cid AND external_ref = :ref"
            ),
            {"cid": consumer_id, "ref": external_ref},
        )
    ).scalar_one()


async def save_profile_revision(session, profile_id, content) -> uuid.UUID | None:
    """Revisión INMUTABLE del contenido normalizado; idempotente por
    (profile_id, content_hash): el mismo contenido devuelve la MISMA revisión
    (jamás duplica). None si el contenido no es normalizable."""
    norm = normalize_profile(content)
    if norm is None:
        return None
    chash = profile_content_hash(norm)
    await session.execute(
        sa.text(
            "INSERT INTO profile_revisions "
            "(id, profile_id, content, content_hash, text_hash) "
            "VALUES (:id, :pid, CAST(:content AS jsonb), :chash, :thash) "
            "ON CONFLICT (profile_id, content_hash) DO NOTHING"
        ),
        {
            "id": uuid.uuid4(), "pid": profile_id,
            "content": json.dumps(norm, ensure_ascii=False),
            "chash": chash, "thash": profile_text_hash(norm),
        },
    )
    return (
        await session.execute(
            sa.text(
                "SELECT id FROM profile_revisions "
                "WHERE profile_id = :pid AND content_hash = :chash"
            ),
            {"pid": profile_id, "chash": chash},
        )
    ).scalar_one()


async def latest_revision(session, profile_id):
    """Revisión VIGENTE del perfil = la última (created_at, id) — el esquema
    ratificado no tiene puntero en profiles; A-08 fija la usada por FK."""
    return (
        await session.execute(
            sa.text(
                "SELECT id, profile_id, content, content_hash, text_hash "
                "FROM profile_revisions WHERE profile_id = :pid "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"pid": profile_id},
        )
    ).one_or_none()


def _text(value) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _str_list(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [v.strip() for v in value if isinstance(v, str) and v.strip()]


def _int(value) -> int | None:
    # bool es subtipo de int: True colaría como 1 — se excluye explícito.
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
