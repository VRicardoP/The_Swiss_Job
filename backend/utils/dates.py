"""Parseo de la fecha de publicación que exponen los portales (V.1 / ADR-10).

PROHIBICIÓN central del ticket 2A: `published_at` SOLO puede salir del dato
del portal — nunca de `first_seen_at`, `last_seen_at` ni `datetime.now()`.
Si la fuente no expone la fecha, el valor correcto es None.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime

logger = logging.getLogger(__name__)

# Cordura: nada anterior al 2000-01-01 (epochs mal escalados, fechas corruptas)
# ni posterior a ahora + 2 días (epoch en milisegundos interpretado como
# segundos, relojes locos del portal; el margen absorbe husos y publicaciones
# programadas).
_MIN_PUBLISHED_AT = datetime(2000, 1, 1, tzinfo=timezone.utc)
_MAX_FUTURE_MARGIN = timedelta(days=2)


def parse_published_at(value) -> datetime | None:
    """Convierte la fecha de publicación cruda del portal a datetime UTC.

    Formatos cubiertos (todos reales, del inventario de fuentes 2026-08-14):
    - ISO8601 con offset (``2026-08-14T07:00:06.318+02:00``), con ``Z`` y SIN
      zona (``2026-08-12T06:36:49``). Sin zona ⇒ se ASUME UTC.
    - Solo fecha (``2026-08-10``) ⇒ medianoche UTC.
    - Epoch en segundos (``1786683627``), como int, float, Decimal o str.
    - RFC822 de RSS (``Thu, 13 Aug 2026 00:00:00 +0000``).

    Nunca lanza: None, cadena vacía o basura devuelven None — esta función no
    puede tumbar una cosecha. Fechas fuera del rango de cordura
    [2000-01-01, ahora + 2 días] ⇒ None con un warning de una línea.
    El resultado es SIEMPRE timezone-aware en UTC (o None).
    """
    dt = _parse_raw(value)
    if dt is None:
        return None

    # Normalizar a UTC; sin zona ⇒ asumir UTC (documentado arriba).
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    if dt < _MIN_PUBLISHED_AT or dt > datetime.now(timezone.utc) + _MAX_FUTURE_MARGIN:
        logger.warning("published_at fuera de rango de cordura, descartado: %r", value)
        return None
    return dt


def _parse_raw(value) -> datetime | None:
    """Prueba cada formato conocido en orden; None si ninguno encaja."""
    if isinstance(value, bool):  # bool es subclase de int: no es una fecha
        return None
    # Decimal incluido: algunos parsers de JSON devuelven Decimal para números.
    if isinstance(value, (int, float, Decimal)):
        # I1-r2: el propio float() puede lanzar — json.loads entrega ints de
        # precisión arbitraria (OverflowError con 10**400) y Decimal("sNaN")
        # da ValueError; InvalidOperation por si un Decimal exótico la emite.
        # La conversión va bajo la misma protección que fromtimestamp: basura
        # numérica ⇒ None, nunca una excepción que tumbe la cosecha.
        try:
            return _from_epoch(float(value))
        except (ValueError, OverflowError, InvalidOperation):
            return None
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    # Epoch como string ("1786683627") — antes que ISO para no ambiguar, pero
    # un todo-dígitos SOLO es plausible como epoch en segundos con 9–11
    # dígitos (~1973–2286, margen de sobra): un ISO compacto ("20260810") o un
    # año suelto ("2026") también son todo-dígitos y deben seguir hacia los
    # formatos de fecha, no descartarse aquí con un warning engañoso.
    # I2-r2: solo ASCII puede ser un epoch — isdigit() Y float() aceptan
    # dígitos devanagari/arábigo-índicos y fabricarían una fecha creíble a
    # partir de basura; el guard cubre el intento entero (también el float()),
    # y lo no-ASCII sigue hacia ISO/RFC822, que lo rechazan.
    if text.isascii() and (not text.isdigit() or 9 <= len(text) <= 11):
        try:
            return _from_epoch(float(text))
        except ValueError:
            pass

    # ISO8601: offset, Z, sin zona o solo fecha (fromisoformat de Python 3.11+).
    # Hay feeds que emiten la 'z' final en minúscula y CPython la rechaza:
    # normalizarla a mayúscula antes de intentar el parseo.
    iso_text = text[:-1] + "Z" if text.endswith("z") else text
    try:
        return datetime.fromisoformat(iso_text)
    except ValueError:
        pass

    # RFC822 de los feeds RSS.
    try:
        return parsedate_to_datetime(text)
    except (ValueError, TypeError):
        pass

    return None


def _from_epoch(value: float) -> datetime | None:
    """Epoch en segundos → datetime UTC; valores absurdos (ms, inf, nan) → None."""
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
