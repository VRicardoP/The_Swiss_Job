"""Recetas versionadas para construir embeddings comparables y reproducibles."""

import math

import numpy as np


LEGACY_V1 = "legacy_v1"
ROLE_COMPOSITE_V2 = "role_composite_v2"
SUPPORTED = frozenset((LEGACY_V1, ROLE_COMPOSITE_V2))

_FIELDS_WEIGHT = 0.60
_TITLE_WEIGHT = 0.40


def validate(recipe: str) -> str:
    if recipe not in SUPPORTED:
        raise ValueError(
            f"receta de embedding desconocida: {recipe!r}; "
            f"válidas: {', '.join(sorted(SUPPORTED))}"
        )
    return recipe


def offer_views(content: dict, recipe: str) -> tuple[tuple[str, float], ...]:
    """Textos y pesos de la oferta para una receta inmutable."""
    validate(recipe)
    title = content.get("title") or ""
    company = content.get("company") or ""
    description = content.get("description") or ""
    tags = " ".join(content.get("tags") or [])
    if recipe == LEGACY_V1:
        return ((" ".join(p for p in (title, company, description, tags) if p), 1.0),)
    fields_first = " ".join(
        p for p in (title, tags, company, description) if p
    )
    return ((fields_first, _FIELDS_WEIGHT), (title, _TITLE_WEIGHT))


def profile_views(content: dict, recipe: str) -> tuple[tuple[str, float], ...]:
    """Textos y pesos del perfil para una receta inmutable."""
    validate(recipe)
    title = content.get("title") or ""
    cv_text = content.get("cv_text") or ""
    skills = " ".join(content.get("skills") or [])
    if recipe == LEGACY_V1:
        return ((" ".join(p for p in (title, cv_text, skills) if p), 1.0),)
    fields_first = " ".join(p for p in (title, skills, cv_text) if p)
    if not title:
        return ((fields_first, _FIELDS_WEIGHT), (fields_first, _TITLE_WEIGHT))
    return ((fields_first, _FIELDS_WEIGHT), (title, _TITLE_WEIGHT))


def encode_views(
    backend, rows: list[tuple[tuple[str, float], ...]]
) -> list[list[float]]:
    """Codifica vistas por lotes y devuelve un único vector normalizado por fila."""
    if not rows:
        return []
    widths = {len(row) for row in rows}
    if len(widths) != 1:
        raise ValueError("todas las filas de un lote deben usar la misma receta")
    width = widths.pop()
    if width == 1:
        out = backend.encode_batch([row[0][0] for row in rows])
    else:
        encoded = [
            backend.encode_batch([row[index][0] for row in rows])
            for index in range(width)
        ]
        if any(len(batch) != len(rows) for batch in encoded):
            raise ValueError("el backend devolvió un número inesperado de vectores")
        out = _combine_views(rows, encoded)
    # Contrato UNIVERSAL del backend (aplica también al camino width==1, que devolvía la respuesta
    # cruda sin validar): una fila → un vector NO vacío y FINITO. Un backend que devuelve menos
    # (o []) haría que el drenador viera 0 inserciones y tomara la cola por VACÍA con filas aún
    # pendientes; un NaN/Inf envenenaría la búsqueda ANN (P2 rev. externa integral).
    _validate_encoded(out, len(rows))
    return out


def _combine_views(
    rows: list[tuple[tuple[str, float], ...]], encoded: list[list[list[float]]]
) -> list[list[float]]:
    """Combinación ponderada + normalización de las vistas, en numpy.

    O-5 (auditoría de eficiencia 2026-08-27). La versión anterior recorría las
    384 componentes en Python. Banco de 200 filas x 384 dimensiones, mediana de
    20 repeticiones dentro del contenedor del core:

        Python puro : 133,06 ms por lote de 200
        numpy       :   9,98 ms por lote de 200   (13,3x)
        desviación máxima entre ambos resultados : 2,776e-17

    Es decir: el mismo número, con el error de redondeo de la coma flotante
    como única diferencia. `numpy` ya estaba instalado (lo arrastra
    sentence-transformers): no añade dependencia.

    Honestidad sobre el peso relativo: el paso dominante del drenado sigue
    siendo el forward pass del transformador. Esto vale un porcentaje bajo del
    total; se hace porque es barato, sin riesgo y numéricamente idéntico.

    Las dos comprobaciones explícitas se CONSERVAN con su mensaje: numpy las
    haría también, pero con un ValueError que habla de "inhomogeneous shape" en
    vez de decir qué pasó.
    """
    dim = len(encoded[0][0])
    if any(len(vector) != dim for batch in encoded for vector in batch):
        raise ValueError("las vistas codificadas tienen dimensiones distintas")
    # (vistas, filas, dim) x (filas, vistas) -> (filas, dim). El einsum empareja
    # peso y vista 1:1 por construcción del índice `w`, igual que hacía el
    # zip(strict=True) al que sustituye.
    vistas = np.asarray(encoded, dtype=np.float64)
    pesos = np.asarray([[view[1] for view in row] for row in rows], dtype=np.float64)
    combinados = np.einsum("wnd,nw->nd", vistas, pesos)
    normas = np.linalg.norm(combinados, axis=1)
    if not normas.all():
        raise ValueError("la combinación de vistas produjo un vector nulo")
    return (combinados / normas[:, None]).tolist()


def _validate_encoded(vectors: list[list[float]], expected_rows: int) -> None:
    """Cardinalidad (un vector por fila) + finitud de cada componente, fail-closed."""
    if len(vectors) != expected_rows:
        raise ValueError(
            f"el backend devolvió {len(vectors)} vectores para {expected_rows} filas"
        )
    for vector in vectors:
        if not vector or not all(math.isfinite(v) for v in vector):
            raise ValueError("el backend devolvió un vector vacío o con valores no finitos")
