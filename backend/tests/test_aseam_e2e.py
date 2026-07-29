"""E2E A.SEAM contra el CORE DEV con la credencial REAL (P1 ownership).

DECISION DELEGADA validada aqui: el consumer del BFF ES `swissjob-shadow`
(CONTRATOS §3; el flip de Fase C lo consolida) — su credencial
(CORE_CONSUMER_KEY) ve los perfiles que la sombra proyecta con
external_ref=str(users.id). Escenario del revisor: enrolar un usuario legacy
sobre su UUID proyectado y leer perfil y matches POR LA COSTURA (routing
core_primary, sin fallback que enmascare) => 200 con ownership correcto.

Requisitos de entorno (skipif en su ausencia — CI sin core dev):
- core-api accesible desde el contenedor backend (red default de compose);
- CORE_CONSUMER_KEY real en el entorno o en <repo>/.env.aseam.local
  (docker compose exec -e CORE_CONSUMER_KEY=... backend pytest ...);
- BD core dev accesible como jobhunt_core (para MAPEAR usuario legacy ->
  UUID proyectado: operacion de OPERADOR de enrolamiento — el /v1 no expone
  lookup por external_ref, racional en models/jobhunt_profile_map.py).

Solo LEE del core dev; escribe unicamente en la BD de test legacy.
"""

import os
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from config import settings
from models.user import User
from models.user_profile import UserProfile
from services.matching import (
    CoreMatching,
    clear_feed_cache,
    resolve_matching,
    set_profile_link,
)
from services.profiles import CoreProfile, clear_profile_cache, resolve_profiles
from services.routing import (
    CAPABILITY_MATCHING,
    CAPABILITY_PROFILES,
    invalidate_routing_cache,
    set_routing,
)


def _credential() -> str | None:
    """CORE_CONSUMER_KEY real: entorno > settings > .env.aseam.local (host)."""
    key = os.environ.get("CORE_CONSUMER_KEY") or settings.CORE_CONSUMER_KEY
    if key:
        return key
    local = Path(__file__).resolve().parents[2] / ".env.aseam.local"
    if local.is_file():
        for line in local.read_text().splitlines():
            if line.startswith("CORE_CONSUMER_KEY="):
                return line.split("=", 1)[1].strip() or None
    return None


def _core_reachable() -> bool:
    try:
        r = httpx.get(f"{settings.CORE_API_BASE_URL}/ready", timeout=3.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


CREDENTIAL = _credential()

pytestmark = [
    pytest.mark.skipif(
        not _core_reachable(),
        reason="core-api no accesible desde el contenedor backend",
    ),
    pytest.mark.skipif(
        CREDENTIAL is None,
        reason="sin CORE_CONSUMER_KEY real (entorno o .env.aseam.local)",
    ),
]


def _core_db_url() -> str:
    """DSN de la BD core DEV (mismo postgres, rol jobhunt_core, BD dev).

    render_as_string(hide_password=False): str(URL) OFUSCARIA el password
    como '***' y el connect fallaria con autenticacion invalida."""
    url = make_url(settings.DATABASE_URL)
    return url.set(
        username="jobhunt_core",
        password=os.environ.get("CORE_DB_PASSWORD", "jobhunt_core_dev"),
        database="swissjobhunter",
    ).render_as_string(hide_password=False)


async def _pick_projected_profile() -> tuple[uuid.UUID, uuid.UUID, dict]:
    """(core_profile_id, user_id_legacy, content) de un perfil REALMENTE
    proyectado por la sombra bajo swissjob-shadow (revision vigente completa).
    Es el paso de enrolamiento del operador: mapear usuario legacy -> UUID."""
    engine = create_async_engine(_core_db_url(), pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT p.id, p.external_ref, r.content "
                        "FROM profiles p "
                        "JOIN consumers c ON c.id = p.consumer_id "
                        "JOIN LATERAL (SELECT r.content "
                        "   FROM profile_revision_activations a "
                        "   JOIN profile_revisions r ON r.id = a.revision_id "
                        "   WHERE a.profile_id = p.id "
                        "   ORDER BY a.seq DESC LIMIT 1) r ON TRUE "
                        "WHERE c.name = 'swissjob-shadow' "
                        "AND r.content ?& ARRAY['title','cv_text','skills'] "
                        "LIMIT 1"
                    )
                )
            ).one_or_none()
    except Exception as exc:  # BD core inaccesible: entorno sin core dev
        pytest.skip(f"BD core dev inaccesible para el enrolamiento: {exc}")
    finally:
        await engine.dispose()
    if row is None:
        pytest.skip("sin perfil proyectado bajo swissjob-shadow en el core dev")
    return row.id, uuid.UUID(row.external_ref), row.content


@pytest.fixture
async def enrolled(db_session, monkeypatch):
    """Usuario legacy con el id del external_ref proyectado + fila local +
    vinculo jobhunt_profile_map + credencial REAL en settings."""
    invalidate_routing_cache()
    clear_profile_cache()
    clear_feed_cache()
    core_profile_id, legacy_user_id, content = await _pick_projected_profile()
    user = User(
        id=legacy_user_id,
        email=f"aseam-e2e-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        gdpr_consent=True,
    )
    db_session.add(user)
    await db_session.flush()
    # Respaldo local OBLIGATORIO (docstring de services/profiles/core_client).
    db_session.add(UserProfile(user_id=user.id, salary_min=70000))
    await db_session.commit()
    await set_profile_link(db_session, user.id, core_profile_id, updated_by="e2e")
    monkeypatch.setattr(settings, "CORE_CONSUMER_KEY", CREDENTIAL)
    yield user, core_profile_id, content
    invalidate_routing_cache()
    clear_profile_cache()
    clear_feed_cache()


async def test_e2e_profile_ownership_via_seam(enrolled, db_session):
    """Perfil por la costura (core_primary, sin fallback) contra el core DEV:
    la credencial de swissjob-shadow LEE el perfil proyectado (200, no el 404
    cross-tenant que asumia el docstring corregido) y sirve {title, cv_text,
    skills} del core + el resto del escritor local."""
    user, _core_profile_id, content = enrolled
    await set_routing(
        db_session, CAPABILITY_PROFILES, "core_primary", profile_id=user.id
    )
    port = await resolve_profiles(db_session, user.id)
    assert isinstance(port, CoreProfile)  # sin fallback que enmascare
    view = await port.get(user)
    assert view is not None  # 200 del core (404/caida => CoreUnavailableError)
    assert view.title == content["title"]
    assert view.cv_text == content["cv_text"]
    assert view.skills == (content["skills"] or [])
    assert view.salary_min == 70000  # overlay del escritor local


async def test_e2e_profile_ownership_raw_v1(enrolled):
    """Ownership explicito en el /v1: GET del perfil vinculado => 200 y
    external_ref == usuario legacy (el perfil ES de nuestro consumer)."""
    user, core_profile_id, _content = enrolled
    resp = httpx.get(
        f"{settings.CORE_API_BASE_URL}/profiles/{core_profile_id}",
        headers={"Authorization": f"Bearer {CREDENTIAL}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )
    assert resp.status_code == 200
    assert resp.json()["external_ref"] == str(user.id)


async def test_e2e_matches_ownership_via_seam(enrolled, db_session):
    """Feed de matches por la costura contra el core DEV: 200 con ownership
    correcto — un 404 de tenant levantaria CoreUnavailableError. El total
    puede ser 0 (exclusion por accionabilidad: sin Jobs locales de respaldo
    en la BD de test), pero la lectura del feed completo debe funcionar."""
    user, core_profile_id, _content = enrolled
    await set_routing(
        db_session, CAPABILITY_MATCHING, "core_primary", profile_id=user.id
    )
    port = await resolve_matching(db_session, user.id)
    assert isinstance(port, CoreMatching)
    items, total = await port.results(user.id)  # no levanta => ownership OK
    assert isinstance(items, list) and total == len(items)
    # Y en crudo: el endpoint del feed responde 200 para nuestro consumer.
    resp = httpx.get(
        f"{settings.CORE_API_BASE_URL}/profiles/{core_profile_id}/matches",
        params={"limit": 1},
        headers={"Authorization": f"Bearer {CREDENTIAL}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )
    assert resp.status_code == 200
