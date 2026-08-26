"""G6/P2-1 — el patrón «divisa a la derecha» ganaba por prioridad, no por posición.

`5c29374` (G5/P3-5) encadenó tres patrones por prioridad global. Pero
`re.search` barre TODO el texto: si el patrón nuevo casaba en la posición 23 y el
clásico en la 0, ganaba el de la 23 y el clásico **nunca llegaba a probarse**.
Dos formas de corrupción, ambas de la familia que este proyecto lleva cinco
ciclos cerrando:

1. el sueldo MENSUAL entre paréntesis desplazaba al ANUAL — ~12x menos, y es el
   patrón normal de los anuncios CH-FR;
2. un bonus o una referencia con divisa detrás del rango real lo secuestraba y
   dejaba el rango INVERTIDO (`salary_min_chf` 5.600 > `salary_max_chf` 2.240),
   que ninguna cota posterior corregía.

Alcance sobre el corpus real: 0 de 637 valores distintos. Es regresión de un fix
del ciclo anterior, en el subsistema con el historial de corrupción más largo del
proyecto, y con productores vivos (`tes`, `irishjobs`, texto libre).
"""

import pytest

from services.data_normalizer import DataNormalizer


class TestGanaElMatchMasALaIzquierda:
    """El rango que el anuncio enuncia como suyo; lo de después es glosa."""

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            # El anual sin divisa, el mensual con divisa entre paréntesis.
            ("90000 - 110000 par an (7500 - CHF 9200 par mois)", (90000.0, 110000.0)),
            (
                "Salaire annuel 90 000 - 110 000, soit 7 500 - CHF 9 200 par mois",
                (90000.0, 110000.0),
            ),
            # Un bonus con divisa detrás del rango real.
            ("80'000 - 100'000 (bonus 5,000 - GBP 2,000)", (80000.0, 100000.0)),
        ],
    )
    def test_el_rango_de_la_izquierda_no_lo_desplaza_el_de_la_derecha(
        self, texto, esperado
    ):
        lo, hi, _ = DataNormalizer._parse_salary_string(texto)
        assert (lo, hi) == esperado

    def test_el_patron_de_divisa_a_la_derecha_sigue_funcionando_cuando_es_el_unico(
        self,
    ):
        """G5/P3-5 no se revierte: sin rango clásico a la izquierda, gana él."""
        assert DataNormalizer._parse_salary_string("30,000 - £40,000") == (
            30000.0,
            40000.0,
            "GBP",
        )

    def test_la_guarda_de_escala_salarial_de_G4_sigue_en_pie(self):
        """«Grade 6 - £30,000» no puede dar `salary_min_chf = 6`."""
        lo, hi, _ = DataNormalizer._parse_salary_string("Grade 6 - £30,000")
        assert (lo, hi) == (30000.0, 30000.0)


class TestElRangoNuncaQuedaInvertido:
    """`compute_salary_match` no puede recibir un intervalo imposible."""

    def test_el_bonus_ya_no_produce_min_mayor_que_max(self):
        job = DataNormalizer.normalize_salary(
            {"salary_original": "80'000 - 100'000 (bonus 5,000 - GBP 2,000)"}
        )
        assert job["salary_min_chf"] <= job["salary_max_chf"]

    def test_los_extremos_se_ordenan_venga_de_donde_venga(self):
        """Forma REAL del corpus: '€100,000 - €00 per annum' parsea invertido."""
        job = DataNormalizer.normalize_salary(
            {"salary_original": "€100,000 - €00 per annum"}
        )
        minimo, maximo = job.get("salary_min_chf"), job.get("salary_max_chf")
        assert not (minimo and maximo and minimo > maximo)
        # El importe bueno sobrevive como MÁXIMO, no se pierde.
        assert maximo == 96000

    def test_un_rango_ya_ordenado_no_se_toca(self):
        job = DataNormalizer.normalize_salary(
            {"salary_original": "80'000 - 100'000 CHF"}
        )
        assert (job["salary_min_chf"], job["salary_max_chf"]) == (80000, 100000)
