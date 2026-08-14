"""Tests de parse_published_at — un caso por formato REAL del inventario 2A.

Todos los resultados no-None deben ser timezone-aware y en UTC. La función
nunca lanza: basura, None y fechas fuera de cordura devuelven None.
"""

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from utils.dates import parse_published_at

UTC = timezone.utc


class TestParsePublishedAtFormats:
    """Formatos reales del inventario, uno por familia."""

    def test_iso_with_offset(self):
        # ostjob/zentraljob: dateFirstPublished
        result = parse_published_at("2026-08-14T07:00:06.318+02:00")
        assert result == datetime(2026, 8, 14, 5, 0, 6, 318000, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_iso_with_z(self):
        # publicjobs: publicFrom
        result = parse_published_at("2026-08-14T00:00:00Z")
        assert result == datetime(2026, 8, 14, 0, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_iso_without_timezone_assumes_utc(self):
        # remotive: publication_date — sin zona ⇒ se asume UTC
        result = parse_published_at("2026-08-12T06:36:49")
        assert result == datetime(2026, 8, 12, 6, 36, 49, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_date_only_is_midnight_utc(self):
        # schuljobs: datePosted del JSON-LD
        result = parse_published_at("2026-08-10")
        assert result == datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_epoch_seconds_int(self):
        # arbeitnow: created_at
        result = parse_published_at(1786683627)
        assert result == datetime(2026, 8, 14, 5, 0, 27, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_epoch_seconds_float(self):
        # json.loads produce float si el feed emite decimales
        result = parse_published_at(1786683627.0)
        assert result == datetime(2026, 8, 14, 5, 0, 27, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_epoch_seconds_str(self):
        # himalayas: pubDate llega como str en algunos payloads
        result = parse_published_at("1786683627")
        assert result == datetime(2026, 8, 14, 5, 0, 27, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_rfc822_rss(self):
        # weworkremotely/zebis/globaljobs: pubDate RFC822
        result = parse_published_at("Thu, 13 Aug 2026 00:00:00 +0000")
        assert result == datetime(2026, 8, 13, 0, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC


class TestParsePublishedAtGarbage:
    """Entradas no utilizables ⇒ None, sin lanzar jamás."""

    def test_none(self):
        assert parse_published_at(None) is None

    def test_empty_string(self):
        assert parse_published_at("") is None

    def test_garbage_string(self):
        assert parse_published_at("no es una fecha") is None

    def test_relative_text_is_not_parsed(self):
        # swiss_schools_isp (Workday): texto relativo en buckets, NO una fecha
        assert parse_published_at("Posted 30+ Days Ago") is None

    def test_non_ascii_digits_rejected(self):
        # I2-r2: dígitos devanagari — isdigit() los da por buenos y float()
        # también los convierte, fabricando una fecha "plausible" (2009-02-13)
        # a partir de basura. Solo dígitos ASCII pueden ser un epoch.
        assert parse_published_at("१२३४५६७८९०") is None


class TestEpochStringLengthGuard:
    """H1: solo un todo-dígitos de 9–11 dígitos es plausible como epoch en
    segundos; un ISO compacto o un año suelto deben seguir hacia los formatos
    de fecha en lugar de comerse como epoch fuera de rango."""

    def test_compact_iso_string_not_eaten_as_epoch(self):
        # "20260810" es ISO8601 compacto, no un epoch de 8 dígitos.
        result = parse_published_at("20260810")
        assert result == datetime(2026, 8, 10, 0, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_bare_year_not_eaten_as_epoch(self, caplog):
        # "2026" no es fecha parseable (⇒ None), pero NO debe pasar por el
        # camino del epoch: eso emitía un warning engañoso de "fuera de rango".
        with caplog.at_level(logging.WARNING, logger="utils.dates"):
            assert parse_published_at("2026") is None
        assert not any("fuera de rango" in r.message for r in caplog.records)

    def test_ten_digit_epoch_string_still_works(self):
        # El guard de longitud no debe romper el epoch legítimo de 10 dígitos.
        result = parse_published_at("1786683627")
        assert result == datetime(2026, 8, 14, 5, 0, 27, tzinfo=UTC)
        assert result.tzinfo == UTC


class TestDecimalAndLowercaseZ:
    """H2: Decimal (parsers de JSON) y 'z' minúscula (feeds reales) no deben
    degradar a None; bool y la basura siguen rechazándose."""

    def test_decimal_epoch(self):
        result = parse_published_at(Decimal("1786683627"))
        assert result == datetime(2026, 8, 14, 5, 0, 27, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_iso_with_lowercase_z(self):
        result = parse_published_at("2026-08-14T10:00:00z")
        assert result == datetime(2026, 8, 14, 10, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_bool_still_rejected(self):
        # bool es subclase de int: aceptar Decimal no debe colarlo.
        assert parse_published_at(True) is None
        assert parse_published_at(False) is None

    def test_garbage_still_rejected(self):
        assert parse_published_at("zzz") is None
        assert parse_published_at(Decimal("NaN")) is None


class TestNumericGarbageNeverRaises:
    """I1-r2: la rama numérica no puede lanzar JAMÁS — json.loads entrega ints
    de precisión arbitraria y hay parsers que producen Decimal señalizante; un
    item corrupto no debe tumbar el fetch entero de la fuente (el cinturón de
    job_service no captura OverflowError). Contrato del docstring: nunca lanza.
    Los tres casos reproducidos en la ronda 2, ⇒ None sin excepción."""

    def test_huge_int_returns_none(self):
        # float(10**400) → OverflowError: int too large to convert to float
        assert parse_published_at(10**400) is None

    def test_huge_negative_int_returns_none(self):
        assert parse_published_at(-(10**400)) is None

    def test_signaling_nan_decimal_returns_none(self):
        # float(Decimal("sNaN")) → ValueError: cannot convert signaling NaN
        assert parse_published_at(Decimal("sNaN")) is None


class TestParsePublishedAtSanity:
    """Cordura: fuera de [2000-01-01, ahora + 2 días] ⇒ None."""

    def test_year_1970_rejected(self):
        assert parse_published_at("1970-01-01") is None

    def test_epoch_zero_rejected(self):
        assert parse_published_at(0) is None

    def test_ten_days_in_future_rejected(self):
        future = datetime.now(UTC) + timedelta(days=10)
        assert parse_published_at(future.isoformat()) is None

    def test_epoch_milliseconds_rejected(self):
        # Epoch en ms interpretado como segundos daría un año absurdo.
        assert parse_published_at(1786683627000) is None

    def test_one_day_in_future_allowed(self):
        # Margen de +2 días: absorbe husos y publicaciones programadas.
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        result = parse_published_at(tomorrow.isoformat())
        assert result is not None
        assert result.tzinfo == UTC
