"""Tipos del pipeline de ingesta (A-03)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RawListing:
    """Un anuncio tal y como lo entrega la fuente (sin normalizar, ADR-05:
    el raw se persiste ANTES de normalizar — la persistencia llega en A-04)."""

    external_id: str
    url: str
    payload: dict
    apply_url: str | None = None


@dataclass(frozen=True)
class FetchResult:
    """Resultado de un barrido de provider para UN scope.

    `next_cursor` SOLO debe commitearse si la persistencia del run completa sin
    error (disciplina commit-del-cursor-al-final, ADR-05). `complete=False`
    significa barrido INCOMPLETO (tope de seguridad): el runner lo persiste
    igualmente pero NO actualiza last_complete_at y reporta `partial`.

    `error` marca un barrido incompleto POR FALLO (p.ej. sobre inválido en la
    página k>1) frente a uno incompleto por el tope de páginas: lo cosechado
    antes del fallo es válido y se persiste, pero el runner INCREMENTA
    `consecutive_failures` — sin esa marca, preservar el barrido parcial
    convertía un rojo mudo en silencio absoluto (auditoría G9 P2-C).
    Invariante: `error is not None` ⇒ `complete is False`.
    """

    listings: tuple[RawListing, ...]
    next_cursor: dict
    pages_fetched: int = 1
    complete: bool = True
    error: str | None = None


@dataclass
class ScopeRunResult:
    scope_id: str
    # "ok" · "partial" (barrido incompleto: persistido pero SIN last_complete_at)
    # · "skipped" (disabled) · "stale" (otro run/config ganó; sin pisar, no
    # cuenta como fallo) · "not_found" (scope eliminado tras encolar: caso
    # normal PERMANENTE, sin retry) · "error"
    status: str
    listings: int = 0
    pages: int = 0
    error: str | None = None
    detail: dict = field(default_factory=dict)
