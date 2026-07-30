"""Endpoints de negocio /v1 (A-09, read-only multi-tenant — CONTRATOS §2).

Matriz ruta→scope→ownership del contrato:
- GET /v1/vacancies/{id}        vacancies:read   GLOBAL (corpus compartido)
- GET /v1/profiles/{pid}        profiles:read    tenant (404 cross)
- GET /v1/profiles/{pid}/matches matches:read    tenant (404 cross)

ETag = hash de la REPRESENTACIÓN (versión optimista): If-None-Match → 304.
Cursor del feed = keyset OPACO base64(score_final|vacancy_id).
Todas las lecturas de página son por LOTES (queries O(1) por página).
"""

import base64
import hashlib
import json
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response

from jobhunt_core import matching
from jobhunt_core.api import schemas
from jobhunt_core.api.deps import (
    ApiError,
    Principal,
    error_404,
    get_session,
    require_scope,
)

# Los errores del contrato quedan DOCUMENTADOS en OpenAPI (auditoría A-09:
# ErrorDTO debe aparecer en components, no ser código muerto).
router = APIRouter(
    prefix="/v1",
    responses={
        400: {"model": schemas.ErrorDTO},
        401: {"model": schemas.ErrorDTO},
        403: {"model": schemas.ErrorDTO},
        404: {"model": schemas.ErrorDTO},
    },
)

MAX_PAGE_LIMIT = 100
# Cota de MAGNITUD del score del cursor (auditoría final A-09→GATE): score_final
# es NUMERIC(6,2) — nada legítimo se acerca a 1E6. Sin cota, un exponente
# gigante desborda el NUMERIC en el driver (DataError → 500) o, peor, se
# codifica en silencio como 0 y corrompe la paginación.
CURSOR_SCORE_BOUND = Decimal("1E6")


def encode_cursor(score_final, vacancy_id) -> str:
    raw = f"{score_final}|{vacancy_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[Decimal, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        score, _, vid = raw.partition("|")
        score_dec = Decimal(score)
        if not score_dec.is_finite() or abs(score_dec) > CURSOR_SCORE_BOUND:
            # Auditoría A-09: NaN/±Infinity/sNaN son Decimals VÁLIDOS que no
            # lanzan — en el keyset NaN es el mayor numeric de Postgres
            # (primera página en bucle) y -Infinity vacía la paginación.
            raise InvalidOperation("cursor fuera de dominio")
        return score_dec, uuid.UUID(vid)
    except (ValueError, InvalidOperation, UnicodeDecodeError) as exc:
        raise ApiError(
            400, "invalid_cursor", "cursor ilegible", {"cursor": cursor[:64]}
        ) from exc


def encode_vacancy_cursor(created_at: datetime, vacancy_id) -> str:
    raw = f"{created_at.isoformat()}|{vacancy_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_vacancy_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Keyset del feed de catálogo = (created_at, vacancy_id). Cota PROPIA
    (análoga a CURSOR_SCORE_BOUND del feed de matches): se exige timestamp CON
    zona horaria — uno naive compararía ambiguamente contra timestamptz en el
    driver; y datetime.fromisoformat ya acota el año a <= 9999, dentro del
    rango de Postgres timestamptz (sin DataError→500 por año desbordado)."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, _, vid = raw.partition("|")
        created = datetime.fromisoformat(ts)
        if created.tzinfo is None:
            raise ValueError("cursor sin zona horaria")
        return created, uuid.UUID(vid)
    except (ValueError, InvalidOperation, UnicodeDecodeError) as exc:
        raise ApiError(
            400, "invalid_cursor", "cursor ilegible", {"cursor": cursor[:64]}
        ) from exc


def _etag_of(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return '"' + hashlib.sha256(canonical.encode()).hexdigest()[:32] + '"'


def _if_none_match_matches(header: str, etag: str) -> bool:
    """Semántica HTTP real de If-None-Match (rev. A-09 #5): lista de
    entidades, comodín `*` y comparación DÉBIL (W/ se ignora para GET)."""
    header = header.strip()
    if header == "*":
        return True
    current = etag.strip('"')
    for part in header.split(","):
        cand = part.strip()
        if cand.startswith("W/"):
            cand = cand[2:].strip()
        if cand.strip('"') == current:
            return True
    return False


def _with_etag(request: Request, payload: dict) -> Response:
    """304 si la representación no cambió (If-None-Match); ETag siempre."""
    etag = _etag_of(payload)
    inm = request.headers.get("if-none-match")
    if inm and _if_none_match_matches(inm, etag):
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=str),
        media_type="application/json",
        headers={"ETag": etag},
    )


async def _vacancy_dtos(session, vacancy_ids) -> dict:
    """{vacancy_id: VacancyDTO} de vacantes ACTIVAS y presentables (con
    canónica vigente). Tres queries por LOTE, sea cual sea la página."""
    if not vacancy_ids:
        return {}
    ids = sorted(set(vacancy_ids), key=str)
    contents = (
        await session.execute(
            sa.text(
                "SELECT v.id, o.content FROM vacancies v "
                "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
                "WHERE v.id = ANY(:ids) "
                "AND v.archived_at IS NULL AND v.merged_into IS NULL"
            ),
            {"ids": ids},
        )
    ).all()
    primaries = {
        r.vid: r
        for r in (
            await session.execute(
                sa.text(
                    "SELECT v.id AS vid, s.name AS source, sl.external_id, "
                    "i.url, i.apply_url, i.first_seen_at, i.last_seen_at "
                    "FROM vacancies v "
                    "JOIN source_listing_incarnations i ON i.id = v.primary_incarnation_id "
                    "JOIN source_listings sl ON sl.id = i.source_listing_id "
                    "JOIN sources s ON s.id = sl.source_id "
                    "WHERE v.id = ANY(:ids)"
                ),
                {"ids": ids},
            )
        ).all()
    }
    listings: dict = {}
    for r in (
        await session.execute(
            sa.text(
                # external_id en TODOS los listings (P2 rev. externa): los
                # alias legacy no-primary tambien portan su MD5 accionable.
                "SELECT i.vacancy_id, s.name AS source, sl.external_id, "
                "i.url, i.apply_url "
                "FROM source_listing_incarnations i "
                "JOIN source_listings sl ON sl.id = i.source_listing_id "
                "JOIN sources s ON s.id = sl.source_id "
                "WHERE i.vacancy_id = ANY(:ids) AND i.ended_at IS NULL "
                "ORDER BY s.name, i.url"
            ),
            {"ids": ids},
        )
    ).all():
        listings.setdefault(r.vacancy_id, []).append(
            schemas.ListingDTO(
                source=r.source, external_id=r.external_id,
                url=r.url, apply_url=r.apply_url,
            )
        )
    out = {}
    for row in contents:
        c = row.content
        primary = primaries.get(row.id)
        out[row.id] = schemas.VacancyDTO(
            id=row.id,
            title=c.get("title") or "",
            company=c.get("company"),
            description=c.get("description"),
            salary=c.get("salary"),
            tags=c.get("tags") or [],
            location=c.get("location"),
            remote=c.get("remote"),
            primary_listing=(
                schemas.PrimaryListingDTO(
                    source=primary.source, external_id=primary.external_id,
                    url=primary.url, apply_url=primary.apply_url,
                    first_seen_at=primary.first_seen_at,
                    last_seen_at=primary.last_seen_at,
                )
                if primary is not None
                else None
            ),
            listings=listings.get(row.id, []),
        )
    return out


@router.get("/vacancies", response_model=schemas.VacanciesPageDTO,
            responses={304: {"description": "Not Modified"}})
async def list_vacancies(
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("vacancies:read")),
    limit: int = Query(20, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
):
    """Feed/búsqueda de catálogo (C-API-R): corpus GLOBAL (cualquier credencial
    con vacancies:read; sin ownership) — solo vacantes ACTIVAS y presentables
    (archived_at IS NULL, merged_into IS NULL, con offer_revision canónica
    vigente). Orden DETERMINISTA (created_at DESC, id DESC) para keyset opaco
    base64(created_at|vacancy_id), apoyado en ix_vacancies_feed_keyset
    (core0012). ETag de la página; los límites de `limit` los declara Query
    (ge/le → OpenAPI).

    `q` = búsqueda MÍNIMA y HONESTA: substring case-insensitive (position, sin
    comodines LIKE que escapar) sobre title/company del content canónico.
    yagni: el ranking SEMÁNTICO no entra aquí — vive en /profiles/{id}/matches
    (cota registrada en CONTRATOS_FASE_C C-API-R).

    COTA de `q` (1ª rev. C-API-R): NO está indexado — el keyset barre
    ix_vacancies_feed_keyset en orden y `position` filtra por fila, de modo
    que una `q` poco selectiva recorre el índice PARCIAL de activas completo
    hasta llenar la página (O(activas), NO seq scan del corpus; sigue siendo
    index scan ordenado). Un GIN trigram es trabajo futuro solo si el volumen
    lo exige — hoy el corpus es pequeño y `q` es el filtro mínimo, no ranking."""
    cur = decode_vacancy_cursor(cursor) if cursor else None
    where = ["v.archived_at IS NULL", "v.merged_into IS NULL"]
    params: dict = {"lim": limit}
    if cur is not None:
        where.append(
            "(v.created_at < :cts OR (v.created_at = :cts AND v.id < :cid))"
        )
        params["cts"], params["cid"] = cur
    if q is not None and q.strip():
        where.append(
            "(position(lower(:q) in lower(coalesce(o.content->>'title', ''))) > 0 "
            "OR position(lower(:q) in lower(coalesce(o.content->>'company', ''))) > 0)"
        )
        params["q"] = q.strip()
    rows = (
        await session.execute(
            sa.text(
                "SELECT v.id, v.created_at FROM vacancies v "
                "JOIN offer_revisions o ON o.id = v.current_offer_revision_id "
                "WHERE " + " AND ".join(where) + " "
                "ORDER BY v.created_at DESC, v.id DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    # _vacancy_dtos re-filtra por presentabilidad: el JOIN de arriba ya lo
    # garantiza, pero el guard `if r.id in dtos` tolera un archivado a mitad de
    # request (rarísimo) — misma disciplina que el feed de matches.
    dtos = await _vacancy_dtos(session, [r.id for r in rows])
    items = [dtos[r.id] for r in rows if r.id in dtos]
    next_cur = (
        (rows[-1].created_at, rows[-1].id) if rows and len(rows) == limit else None
    )
    page = schemas.VacanciesPageDTO(
        items=items,
        next_cursor=encode_vacancy_cursor(*next_cur) if next_cur else None,
    )
    return _with_etag(request, page.model_dump(mode="json"))


@router.get("/vacancies/{vacancy_id}", response_model=schemas.VacancyDTO,
            responses={304: {"description": "Not Modified"}})
async def get_vacancy(
    vacancy_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("vacancies:read")),
):
    """Corpus GLOBAL (§2): cualquier credencial con vacancies:read; sin
    ownership por consumidor. Solo vacantes ACTIVAS y presentables."""
    dtos = await _vacancy_dtos(session, [vacancy_id])
    dto = dtos.get(vacancy_id)
    if dto is None:
        raise error_404("vacante")
    return _with_etag(request, dto.model_dump(mode="json"))


@router.get("/profiles/{profile_id}", response_model=schemas.ProfileDTO,
            responses={304: {"description": "Not Modified"}})
async def get_profile(
    profile_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("profiles:read")),
):
    # OWNERSHIP EN SQL y en UNA sola sentencia (rev. A-09 #1): el filtro por
    # consumer_id va en la query (§2) y la identidad + revisión vigente salen
    # del MISMO snapshot — sin ventana TOCTOU entre comprobación y contenido.
    # Cross-tenant y ausente son INDISTINGUIBLES: 404 (no revelar).
    row = (
        await session.execute(
            sa.text(
                "SELECT p.id, p.external_ref, p.created_at, "
                "pr.content, pr.content_hash, pr.text_hash "
                "FROM profiles p "
                "LEFT JOIN (SELECT DISTINCT ON (a.profile_id) a.profile_id, "
                "           r.content, r.content_hash, r.text_hash "
                "           FROM profile_revision_activations a "
                "           JOIN profile_revisions r ON r.id = a.revision_id "
                "           WHERE a.profile_id = :pid "
                "           ORDER BY a.profile_id, a.seq DESC) pr "
                "  ON pr.profile_id = p.id "
                "WHERE p.id = :pid AND p.consumer_id = :cid"
            ),
            {"pid": profile_id, "cid": principal.consumer_id},
        )
    ).one_or_none()
    if row is None:
        raise error_404("perfil")
    dto = schemas.ProfileDTO(
        id=row.id, external_ref=row.external_ref, created_at=row.created_at,
        current_revision=(
            schemas.ProfileRevisionDTO(
                content=row.content, content_hash=row.content_hash,
                text_hash=row.text_hash,
            )
            if row.content_hash is not None
            else None
        ),
    )
    return _with_etag(request, dto.model_dump(mode="json"))


@router.get("/profiles/{profile_id}/matches", response_model=schemas.MatchesPageDTO,
            responses={304: {"description": "Not Modified"}})
async def get_matches(
    profile_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("matches:read")),
    limit: int = Query(20, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = Query(None),
):
    """Feed del perfil (§2/A-08): excluye dismissed y no-activas; orden
    score_final DESC; keyset opaco base64(score_final|vacancy_id). ETag de la
    página (rev. A-09 #2). Los límites del `limit` los declara Query (ge/le →
    OpenAPI) y el sobre lo pone el handler de validación."""
    # 404 para cross-tenant/ausente (indistinguibles); además el feed filtra
    # el tenant EN SQL (rev. A-09 #1) — una reasignación entre esta lectura y
    # la página jamás filtra filas ajenas (solo degradaría a página vacía).
    owner = (
        await session.execute(
            sa.text("SELECT consumer_id FROM profiles WHERE id = :pid"),
            {"pid": profile_id},
        )
    ).scalar_one_or_none()
    if owner is None or owner != principal.consumer_id:
        raise error_404("perfil")

    cur = decode_cursor(cursor) if cursor else None
    rows, next_cur = await matching.feed(
        session, profile_id, limit=limit, cursor=cur,
        consumer_id=principal.consumer_id,
    )
    eval_ids = [r.eval_id for r in rows]
    evals = {
        e.id: e
        for e in (
            await session.execute(
                sa.text(
                    "SELECT e.id, e.eval_key, e.explanation, "
                    "m.name AS model_name, m.version AS model_version, "
                    "p.name AS policy_name, p.prompt_version "
                    "FROM match_evaluations e "
                    "JOIN embedding_models m ON m.id = e.model_id "
                    "JOIN scoring_policies p ON p.id = e.scoring_policy_id "
                    "WHERE e.id = ANY(:ids) AND e.profile_id = :pid"
                ),
                {"ids": eval_ids, "pid": profile_id},
            )
        ).all()
    } if eval_ids else {}
    vacancies = await _vacancy_dtos(session, [r.vacancy_id for r in rows])

    items = []
    for r in rows:
        vac = vacancies.get(r.vacancy_id)
        ev = evals.get(r.eval_id)
        if vac is None or ev is None:
            continue  # despresentable a mitad de página (rarísimo): se omite
        items.append(
            schemas.MatchDTO(
                vacancy=vac,
                evaluation=schemas.EvaluationDTO(
                    eval_key=ev.eval_key,
                    model=schemas.ModelRefDTO(
                        name=ev.model_name, version=ev.model_version
                    ),
                    policy=schemas.PolicyRefDTO(
                        name=ev.policy_name, prompt_version=ev.prompt_version
                    ),
                    score_final=float(r.score_final),
                    scores=r.scores,
                    explanation=ev.explanation,
                ),
                state=schemas.MatchStateDTO(
                    saved=r.saved_at is not None,
                    dismissed=False,  # el feed EXCLUYE dismissed por contrato
                    feedback=r.feedback,
                    notes=r.notes,
                ),
            )
        )
    page = schemas.MatchesPageDTO(
        items=items,
        next_cursor=encode_cursor(*next_cur) if next_cur else None,
    )
    # ETag también en el feed (rev. A-09 #2): la página es una representación.
    return _with_etag(request, page.model_dump(mode="json"))
