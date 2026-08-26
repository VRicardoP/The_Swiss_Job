"""G4 — familia de los SALARIOS (`services/data_normalizer.py`).

- **P2-1**: el `_CUR_TOK` OPCIONAL que introdujo `c20c0b8` convirtió en «rango
  válido» cualquier «<número de ruido> - <DIVISA><importe>». Como `re.search`
  es leftmost, el ruido secuestraba la cota baja: «Grade 6 - £30,000» se
  persistía con `salary_min_chf = 6`. Antes del commit ese patrón NO casaba
  (el `£` cortaba el segundo número) y el camino `single` encontraba el importe
  real. Productores vivos: `scrapers/tes.py` (portal de DOCENCIA) e
  `scrapers/irishjobs.py`, que vuelcan texto libre del portal. Agravante:
  `salary_*_chf` está en `_CONTENT_FIELDS` y se refresca en cada cosecha, así
  que un saneo SQL no basta.
- **P3-8**: `normalize_salary` hacía `currency.upper()` sin `strip()` ni mapa de
  símbolos, así que " EUR ", "chf ", "€", "$", "£" y "Fr." caían en el camino
  «divisa desconocida» y DESCARTABAN el importe entero.
"""

import pytest

from services.data_normalizer import DataNormalizer


def _chf(salary_original: str, currency=None) -> tuple:
    """Camino REAL de ingesta: normalize() completo, columnas persistidas."""
    job = DataNormalizer.normalize(
        {"salary_original": salary_original, "salary_currency": currency}
    )
    return job.get("salary_min_chf"), job.get("salary_max_chf")


class TestP21EscalasSalarialesNoSecuestranElRango:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            # Los 6 casos medidos por el auditor: el número de la ESCALA
            # entraba como salary_min_chf.
            ("Grade 6 - £30,000", (33600, 33600)),
            ("Band 5 - £28,000 to £34,000", (31360, 38080)),
            ("Scale 1 - EUR 55,000", (52800, 52800)),
            ("NJC Scale 6 - £27,334 - £29,777", (30614, 33350)),
            ("MPS/UPS 1 - £31,650", (35448, 35448)),
            ("Point 8 - CHF 90'000", (90000, 90000)),
        ],
    )
    def test_el_numero_de_escala_no_entra_como_salario(self, texto, esperado):
        assert _chf(texto) == esperado, (
            f"«{texto}» persiste el número de la escala como salario: dato "
            "corrompido en cada cosecha (salary_*_chf se refresca en el upsert)"
        )

    def test_la_escala_pierde_frente_al_importe_real(self):
        """Caso que sobrevivió a G1/G2/G3: el patrón con divisa en AMBOS
        extremos va primero, así que desempata a favor del importe."""
        assert _chf("Main Pay Scale 1 - 6 (£31,650 - £43,607)") == (35448, 48839)

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            # Controles: los tres fixes de G3 siguen en pie.
            ("£30,000 - £40,000", (33600, 44800)),
            ("CHF 80'000 - CHF 100'000 par an", (80000, 100000)),
            ("100 000 CHF", (100000, 100000)),
            ("3 500 CHF par mois", (3500, 3500)),
            # Y los rangos sin divisa siguen funcionando.
            ("80000-100000", (80000, 100000)),
            ("80k-100k", (80000, 100000)),
            ("80-100k", (80000, 100000)),
        ],
    )
    def test_los_controles_de_g1_g2_g3_siguen_bien(self, texto, esperado):
        assert _chf(texto) == esperado


class TestP38DivisaCruda:
    @pytest.mark.parametrize(
        "divisa,esperado",
        [
            (" EUR ", 48000),
            ("chf ", 50000),
            ("€", 48000),
            ("$", 44000),
            ("£", 56000),
            ("Fr.", 50000),
        ],
    )
    def test_la_divisa_con_ruido_no_descarta_el_importe(self, divisa, esperado):
        assert _chf("50000", divisa)[0] == esperado, (
            f"la divisa {divisa!r} cae en «desconocida» y pierde el importe "
            "entero — providers/jsearch.py pasa el valor crudo de la API"
        )

    def test_una_divisa_de_verdad_desconocida_sigue_descartando(self):
        """Cota del fix G3/P2-5: mejor sin salario que un importe ×100."""
        assert _chf("50000", "XYZ") == (None, None)
