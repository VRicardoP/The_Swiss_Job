"""Endpoints /v1 de candidaturas y bookmarks (C-4, DISEÑO v2.1).

Espejo 1:1 del puerto real del BFF (`APPLICATION_OPS`): list, create, update,
delete, sync_bookmarks. Scopes exactos `applications:read`/`applications:write`
(H13); ownership por JOIN con el consumer del Bearer — cross-tenant/ausente →
404 INDISTINGUIBLE (Decisión 7). ETag = hash de la representación + If-Match
FUERTE bajo FOR UPDATE (Decisión 2); Idempotency-Key OPCIONAL en toda
escritura (Decisión 1). El GET compone applications + bookmarks PUROS con
cursor keyset UNIFICADO de un reloj y una identidad (Decisión 10). PATCH y
DELETE direccionan {id} DUAL: application.id o bookmark puro (=vacancy_id),
con promoción a application en la misma tx (Decisión 4).
"""

import hashlib
import json
import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query, Request, Response

from jobhunt_core import applications as apps
from jobhunt_core import matching
from jobhunt_core.api import schemas
from jobhunt_core.api.deps import (
    ApiError,
    Principal,
    ensure_json_storable,
    error_404,
    get_session,
    require_scope,
)
from jobhunt_core.api.idempotency import run_idempotent
from jobhunt_core.api.v1 import (
    MAX_PAGE_LIMIT,
    _etag_of,
    _if_match_matches,
    _with_etag,
    decode_vacancy_cursor,
    encode_vacancy_cursor,
)

router = APIRouter(
    prefix="/v1",
    responses={
        400: {"model": schemas.ErrorDTO},
        401: {"model": schemas.ErrorDTO},
        403: {"model": schemas.ErrorDTO},
        404: {"model": schemas.ErrorDTO},
    },
)

_WRITE_RESPONSES = {
    409: {"model": schemas.ErrorDTO},
    412: {"model": schemas.ErrorDTO},
}


def request_hash(payload: dict) -> str:
    """sha256 del JSON canónico (sort_keys — mismo contrato que C-3)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def json_response(status: int, payload) -> Response:
    """Respuesta con ETag de la representación; 204 sin cuerpo. Serialización
    CANÓNICA (sort_keys): el replay idempotente relee el payload de un JSONB
    (que NO conserva el orden de claves) — sin canonicalizar, el replay no
    sería byte a byte (Decisión 1)."""
    if payload is None:
        return Response(status_code=status)
    return Response(
        content=json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
        media_type="application/json",
        status_code=status,
        headers={"ETag": _etag_of(payload)},
    )


def _check_storable(body) -> None:
    """G7-P3-1 en este router, con UNA excepción CONDICIONADA: la `url`.

    El snapshot (title/company/url/source/description) va a un
    `CAST(:snap AS jsonb)` y `notes` a una columna `text`; ninguno de los dos
    admite el NUL que el `json.loads` de Starlette decodifica desde `\\u0000`
    sin rechistar y que ningún `max_length` filtra. La `url` se exceptúa
    porque tiene una frontera más específica y contractual —la cuarentena del
    sink en `_link` responde `400 invalid_url`— y adelantarse a ella
    degradaría el diagnóstico del cliente.

    G8-P3-3: pero esa frontera SOLO corre en la rama por-URL.
    `applications.link_vacancy` llama a `durable_synthesizable` dentro de
    `if url is not None:`, y a esa rama solo se llega tras
    `if vacancy_id is not None: return await resolve_direct(...)`. Los DTO
    permiten mandar AMBOS: con `vacancy_id` presente el vínculo ni mira la
    url, pero la url sigue entrando en el snapshot (`SNAPSHOT_KEYS`) ⇒
    `CAST(:snap AS jsonb)` ⇒ 500 por entrada de usuario. La excepción se
    condiciona por tanto a la MISMA condición que decide si `_link` va a
    validarla, que es la única forma de que la justificación escrita y el
    código no vuelvan a divergir.

    Volcado PYTHON: en `mode="json"` pydantic serializa los floats no finitos
    a `null` y los escondería."""
    cuerpo = body.model_dump()
    if cuerpo.get("vacancy_id") is None:
        cuerpo.pop("url", None)
    for item in cuerpo.get("bookmarks") or []:
        if item.get("vacancy_id") is None:
            item.pop("url", None)
    ensure_json_storable(cuerpo)


def check_if_match(request: Request, payload: dict) -> None:
    """Precondición If-Match (comparación FUERTE) contra el ETag ACTUAL de la
    representación — se llama BAJO el FOR UPDATE del recurso (Decisión 2)."""
    if_match = request.headers.get("if-match")
    if if_match is not None and not _if_match_matches(if_match, _etag_of(payload)):
        raise ApiError(
            412, "precondition_failed",
            "If-Match no coincide con el ETag actual del recurso",
        )


def _dto_json(item: dict) -> dict:
    return schemas.ApplicationDTO(**item).model_dump(mode="json")


async def _link(session, profile_id, source_dto) -> uuid.UUID:
    """Cascada Decisión 3 con el mapeo de errores del contrato: camino (a)
    irresoluble → 404 indistinguible; (b)-(d) irresolubles → 400."""
    try:
        vid = await apps.link_vacancy(
            session, profile_id,
            vacancy_id=source_dto.vacancy_id, url=source_dto.url,
            title=source_dto.title, company=source_dto.company,
            description=source_dto.description,
        )
    except apps.LinkError as exc:
        raise ApiError(
            400, "invalid_url",
            "la URL no es resoluble ni sintetizable",
            {"reason": exc.reason},
        ) from exc
    if vid is None:
        raise error_404("vacante")
    return vid


# ------------------------------------------- direccionamiento dual (Decisión 4)

_APP_COLS = (
    "a.id, a.profile_id, a.vacancy_id, a.status, a.notes, a.follow_up_date, "
    "a.created_at, a.updated_at, a.snapshot, a.revision, "
    "c.name AS consumer_name "
)
_APP_FROM = (
    "FROM applications a "
    "JOIN profiles p ON p.id = a.profile_id AND p.consumer_id = :cid "
    "JOIN consumers c ON c.id = p.consumer_id "
)
_PVS_SQL = (
    "SELECT s.profile_id, s.vacancy_id, s.saved_at, s.notes, s.updated_at, "
    "c.name AS consumer_name "
    "FROM profile_vacancy_state s "
    "JOIN profiles p ON p.id = s.profile_id AND p.consumer_id = :cid "
    "JOIN consumers c ON c.id = p.consumer_id "
    "WHERE s.vacancy_id = :iid AND s.saved_at IS NOT NULL FOR UPDATE OF s"
)


def _sole(rows, item_id):
    """Una sola fila o nada; >1 (varios perfiles del MISMO consumer sobre la
    misma vacante — borde inalcanzable en el piloto mono-perfil) → 409
    defensivo, jamás mutar a ciegas la fila equivocada."""
    if len(rows) > 1:
        raise ApiError(
            409, "ambiguous_id",
            "el identificador direcciona más de un recurso del tenant",
            {"id": str(item_id)},
        )
    return rows[0] if rows else None


async def _lock_target(session, item_id, consumer_id):
    """Resuelve {id} bajo FOR UPDATE: application.id → redirección por
    vacancy_id (promoción idempotente, R2-2) → bookmark puro. None → 404."""
    row = (
        await session.execute(
            sa.text(
                "SELECT " + _APP_COLS + _APP_FROM
                + "WHERE a.id = :iid FOR UPDATE OF a"
            ),
            {"iid": item_id, "cid": consumer_id},
        )
    ).one_or_none()
    if row is not None:
        return "application", row
    rows = (
        await session.execute(
            sa.text(
                "SELECT " + _APP_COLS + _APP_FROM
                + "WHERE a.vacancy_id = :iid FOR UPDATE OF a"
            ),
            {"iid": item_id, "cid": consumer_id},
        )
    ).all()
    row = _sole(rows, item_id)
    if row is not None:
        return "application", row
    rows = (
        await session.execute(sa.text(_PVS_SQL), {"iid": item_id, "cid": consumer_id})
    ).all()
    row = _sole(rows, item_id)
    if row is not None:
        return "bookmark", row
    return None


async def _current_payload(session, kind: str, row) -> dict:
    """Representación ACTUAL del objetivo (su hash es el ETag del If-Match)."""
    corpus = (await apps.corpus_fields(session, [row.vacancy_id])).get(
        row.vacancy_id, {}
    )
    if kind == "application":
        return _dto_json(apps.compose_application(row, corpus))
    return _dto_json(apps.compose_bookmark(row, corpus))


# ------------------------------------------------------------------ endpoints


@router.get(
    "/applications", response_model=schemas.ApplicationsPageDTO,
    responses={304: {"description": "Not Modified"}},
)
async def list_applications(
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("applications:read")),
    profile: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = Query(None),
):
    """GET compuesto (Decisión 10): applications + bookmarks PUROS del perfil
    con UN reloj (created_at | saved_at) y UNA identidad (application.id |
    vacancy_id), orden (ts DESC, id DESC), keyset opaco. Cross-tenant → 404."""
    if await apps.profile_owner(session, profile, principal.consumer_id) is None:
        raise error_404("perfil")
    cur = decode_vacancy_cursor(cursor) if cursor else None
    items, next_cur = await apps.feed_page(session, profile, limit, cur)
    page = schemas.ApplicationsPageDTO(
        items=[schemas.ApplicationDTO(**i) for i in items],
        next_cursor=encode_vacancy_cursor(*next_cur) if next_cur else None,
    )
    return _with_etag(request, page.model_dump(mode="json"))


@router.post(
    "/applications", status_code=201, response_model=schemas.ApplicationDTO,
    responses=_WRITE_RESPONSES,
)
async def create_application(
    request: Request,
    body: schemas.ApplicationCreateDTO,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("applications:write")),
):
    """Alta de candidatura (Decisiones 3 y 4): vincula vacante EN LA MISMA TX
    por la cascada (vacancy_id directo / URL en todas las fuentes / síntesis
    portfolio-import / manual sin url); snapshot = lo que el usuario vio;
    status ausente → saved (+ upsert saved_at); evento inicial + revision=1.
    Duplicado del par (perfil, vacante) → 409 application_exists."""
    idem_key = request.headers.get("idempotency-key")
    route = f"POST {request.url.path}"
    _check_storable(body)  # G7-P3-1, antes de reservar la idempotencia
    req_hash = request_hash(body.model_dump(mode="json"))

    async def handler():
        consumer_name = await apps.profile_owner(
            session, body.profile_id, principal.consumer_id
        )
        if consumer_name is None:
            raise error_404("perfil")
        vid = await _link(session, body.profile_id, body)
        status_value = body.status or apps.SAVED_STATUS
        snapshot = {k: getattr(body, k) for k in apps.SNAPSHOT_KEYS}
        aid = await apps.create_application(
            session, profile_id=body.profile_id, vacancy_id=vid,
            status=status_value, notes=body.notes,
            follow_up_date=body.follow_up_date, snapshot=snapshot,
            destination=consumer_name,
        )
        if aid is None:
            raise ApiError(
                409, "application_exists",
                "el perfil ya tiene una candidatura para esa vacante",
                {"vacancy_id": str(vid)},
            )
        return 201, _dto_json(await apps.application_item(session, aid))

    status, payload = await run_idempotent(
        session, principal, route, req_hash, idem_key, handler
    )
    return json_response(status, payload)


async def _apply_patch(session, row, body, provided) -> dict:
    """Mutación parcial de una application REAL: cada mutación incrementa
    `revision`; cambio de status → evento (Decisión 6); PATCH a saved
    re-upserta saved_at; al salir de saved NO se toca (Decisión 4)."""
    if not provided:
        return _dto_json(await apps.application_item(session, row.id))
    new_status = (
        body.status
        if ("status" in provided and body.status is not None)
        else row.status
    )
    new_notes = body.notes if "notes" in provided else row.notes
    new_fud = (
        body.follow_up_date if "follow_up_date" in provided else row.follow_up_date
    )
    new_revision = row.revision + 1
    await session.execute(
        sa.text(
            "UPDATE applications SET status = :st, notes = :n, "
            "follow_up_date = :fud, revision = :rev, "
            "updated_at = clock_timestamp() WHERE id = :id"
        ),
        {
            "st": new_status, "n": new_notes, "fud": new_fud,
            "rev": new_revision, "id": row.id,
        },
    )
    if new_status != row.status:
        await apps.record_status_event(
            session, application_id=row.id, profile_id=row.profile_id,
            vacancy_id=row.vacancy_id, status=new_status,
            revision=new_revision, destination=row.consumer_name,
        )
    if new_status == apps.SAVED_STATUS and "status" in provided:
        await matching.set_saved(session, row.profile_id, row.vacancy_id, True)
    return _dto_json(await apps.application_item(session, row.id))


async def _promote_bookmark(session, row, body, provided) -> dict:
    """Promoción de bookmark PURO a application en la MISMA tx (Decisión 4):
    status resultante (saved si solo cambian notes/follow_up), snapshot desde
    el corpus, notes de profile_vacancy_state, evento inicial + revision=1."""
    new_status = (
        body.status
        if ("status" in provided and body.status is not None)
        else apps.SAVED_STATUS
    )
    notes = body.notes if "notes" in provided else row.notes
    fud = body.follow_up_date if "follow_up_date" in provided else None
    corpus = (await apps.corpus_fields(session, [row.vacancy_id])).get(
        row.vacancy_id, {}
    )
    snapshot = {k: corpus.get(k) for k in apps.SNAPSHOT_KEYS}
    aid = await apps.create_application(
        session, profile_id=row.profile_id, vacancy_id=row.vacancy_id,
        status=new_status, notes=notes, follow_up_date=fud, snapshot=snapshot,
        destination=row.consumer_name,
    )
    if aid is None:
        # Carrera rarísima (otra tx creó la application pese al lock pvs):
        # reintible por el cliente, jamás doble fila.
        raise ApiError(
            409, "application_exists",
            "el perfil ya tiene una candidatura para esa vacante",
            {"vacancy_id": str(row.vacancy_id)},
        )
    return _dto_json(await apps.application_item(session, aid))


@router.patch(
    "/applications/{item_id}", response_model=schemas.ApplicationDTO,
    responses=_WRITE_RESPONSES,
)
async def patch_application(
    item_id: uuid.UUID,
    request: Request,
    body: schemas.ApplicationPatchDTO,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("applications:write")),
):
    """PATCH con direccionamiento DUAL (Decisión 4): application.id o
    bookmark puro (=vacancy_id, promoción idempotente — si ya hay application
    del perfil, el identificador REDIRIGE a ella). If-Match bajo FOR UPDATE."""
    idem_key = request.headers.get("idempotency-key")
    route = f"PATCH {request.url.path}"
    _check_storable(body)  # G7-P3-1
    req_hash = request_hash(body.model_dump(mode="json", exclude_unset=True))
    provided = body.model_fields_set

    async def handler():
        target = await _lock_target(session, item_id, principal.consumer_id)
        if target is None:
            raise error_404("candidatura")
        kind, row = target
        check_if_match(request, await _current_payload(session, kind, row))
        if kind == "application":
            return 200, await _apply_patch(session, row, body, provided)
        return 200, await _promote_bookmark(session, row, body, provided)

    status, payload = await run_idempotent(
        session, principal, route, req_hash, idem_key, handler
    )
    return json_response(status, payload)


@router.delete(
    "/applications/{item_id}", status_code=204, responses=_WRITE_RESPONSES,
)
async def delete_application(
    item_id: uuid.UUID,
    request: Request,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("applications:write")),
):
    """DELETE dual (Decisión 4): application → borra la fila (los eventos caen
    por CASCADE); bookmark puro → saved_at=NULL conservando notes. 204."""
    idem_key = request.headers.get("idempotency-key")
    route = f"DELETE {request.url.path}"

    async def handler():
        target = await _lock_target(session, item_id, principal.consumer_id)
        if target is None:
            raise error_404("candidatura")
        kind, row = target
        check_if_match(request, await _current_payload(session, kind, row))
        if kind == "application":
            # G3-A-P2-2: el alta SIN `status` vale 'saved' y escribe DOS filas
            # (application + profile_vacancy_state.saved_at); borrar solo la
            # primera hacía que el NOT EXISTS del feed dejara de excluir la
            # vacante y el item RESUCITARA como bookmark, con id = vacancy_id
            # (otra identidad) y un segundo DELETE en 404. El item del feed es
            # UNO: se retira entero, igual que ya hacía la rama bookmark.
            await matching.set_saved(
                session, row.profile_id, row.vacancy_id, False
            )
            await session.execute(
                sa.text("DELETE FROM applications WHERE id = :id"), {"id": row.id}
            )
        else:
            await matching.set_saved(
                session, row.profile_id, row.vacancy_id, False
            )
        return 204, None

    status, payload = await run_idempotent(
        session, principal, route, request_hash({}), idem_key, handler
    )
    return json_response(status, payload)


@router.put(
    "/profiles/{profile_id}/bookmarks",
    response_model=schemas.BookmarksSyncResultDTO,
    responses=_WRITE_RESPONSES,
)
async def sync_bookmarks(
    profile_id: uuid.UUID,
    request: Request,
    body: schemas.BookmarksPutDTO,
    session=Depends(get_session),
    principal: Principal = Depends(require_scope("applications:write")),
):
    """Sync ADITIVO (paridad con el puerto real: crea, NO borra ausentes —
    Decisión 4): por cada bookmark resuelve/sintetiza vacante (Decisión 3,
    incl. camino sin url), crea application status=saved si el perfil no la
    tiene + upsert de saved_at SIEMPRE; dedupe por vacante resuelta. Devuelve
    SOLO las creadas + los items salteados por irresolubles (G1-P3-2)."""
    idem_key = request.headers.get("idempotency-key")
    route = f"PUT {request.url.path}"
    _check_storable(body)  # G7-P3-1
    req_hash = request_hash(body.model_dump(mode="json"))

    async def handler():
        consumer_name = await apps.profile_owner(
            session, profile_id, principal.consumer_id
        )
        if consumer_name is None:
            raise error_404("perfil")
        created = []
        skipped = []
        seen: set[uuid.UUID] = set()
        for item in body.bookmarks:
            try:
                vid = await _link(session, profile_id, item)
            except ApiError as exc:
                # G1-P3-2: en el SYNC (solo aquí — POST/PATCH conservan el 404
                # por-item), un item irresoluble (camino 3a: vacante archivada
                # tras el snapshot del BFF) NO aborta el PUT entero: sin esto,
                # la tx única hacía 404 global, los items válidos tampoco se
                # creaban y el retry del BFF repetía el 404 para siempre (un
                # sync «aditivo» estructuralmente sin progreso). Se saltea y
                # se REPORTA; los errores de forma (400) siguen abortando.
                if exc.status_code != 404:
                    raise
                skipped.append(
                    schemas.BookmarkSkippedDTO(
                        vacancy_id=item.vacancy_id, url=item.url,
                        title=item.title, reason="vacante irresoluble (3a)",
                    )
                )
                continue
            if vid in seen:
                continue  # dedupe por vacante resuelta (Decisión 4)
            seen.add(vid)
            snapshot = {k: getattr(item, k) for k in apps.SNAPSHOT_KEYS}
            aid = await apps.create_application(
                session, profile_id=profile_id, vacancy_id=vid,
                status=apps.SAVED_STATUS, notes=item.notes,
                follow_up_date=item.follow_up_date, snapshot=snapshot,
                destination=consumer_name,
            )
            if aid is None:
                # Ya tenía application: el sync sigue siendo aditivo — solo
                # re-marca el bookmark (upsert de saved_at).
                await matching.set_saved(session, profile_id, vid, True)
            else:
                created.append(await apps.application_item(session, aid))
        result = schemas.BookmarksSyncResultDTO(
            created=[schemas.ApplicationDTO(**c) for c in created],
            skipped=skipped,
        )
        return 200, result.model_dump(mode="json")

    status, payload = await run_idempotent(
        session, principal, route, req_hash, idem_key, handler
    )
    return json_response(status, payload)
