"""Contrato HTTP compartido por los routers v1."""

import hashlib
import json

from fastapi import Request, Response

from jobhunt_core.api import schemas
from jobhunt_core.api.deps import ApiError

WRITE_RESPONSES = {
    409: {"model": schemas.ErrorDTO},
    412: {"model": schemas.ErrorDTO},
}


def request_hash(payload: dict) -> str:
    """SHA-256 del JSON canónico usado por Idempotency-Key."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def etag_of(payload: dict) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str
    )
    return '"' + hashlib.sha256(canonical.encode()).hexdigest()[:32] + '"'


def if_match_matches(header: str, etag: str) -> bool:
    """Comparación fuerte de If-Match (RFC 9110 §13.1.1)."""
    header = header.strip()
    if header == "*":
        return True
    return any(
        candidate == etag
        for candidate in (part.strip() for part in header.split(","))
        if not candidate.startswith("W/")
    )


def if_none_match_matches(header: str, etag: str) -> bool:
    """Comparación débil de If-None-Match para GET."""
    header = header.strip()
    if header == "*":
        return True
    current = etag.strip('"')
    for part in header.split(","):
        candidate = part.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate.strip('"') == current:
            return True
    return False


def with_etag(request: Request, payload: dict) -> Response:
    """304 si la representación no cambió; ETag siempre."""
    etag = etag_of(payload)
    header = request.headers.get("if-none-match")
    if header and if_none_match_matches(header, etag):
        return Response(status_code=304, headers={"ETag": etag})
    return Response(
        content=json.dumps(payload, ensure_ascii=False, default=str),
        media_type="application/json",
        headers={"ETag": etag},
    )


def check_if_match(request: Request, payload: dict) -> None:
    """Exige que If-Match coincida fuertemente con la representación."""
    header = request.headers.get("if-match")
    if header is not None and not if_match_matches(header, etag_of(payload)):
        raise ApiError(
            412,
            "precondition_failed",
            "If-Match no coincide con el ETag actual del recurso",
        )


def json_response(status: int, payload) -> Response:
    """Respuesta JSON canónica con ETag; 204 sin cuerpo."""
    if payload is None:
        return Response(status_code=status)
    return Response(
        content=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, default=str
        ),
        media_type="application/json",
        status_code=status,
        headers={"ETag": etag_of(payload)},
    )
