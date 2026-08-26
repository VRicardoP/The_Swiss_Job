"""Regresión de la auditoría G2 — P3-2: el clasificador y los títulos en MAYÚSCULAS.

El fix G1/P2-9 separó el artículo «un/uno» del organismo (UN, UNO, WHO, ILO)
exigiendo que el acrónimo aparezca en MAYÚSCULAS. Pero un título francófono
escrito entero en mayúsculas («ENSEIGNANT-E PRIMAIRE POUR UN REMPLACEMENT»,
forma frecuente en los boards suizos) vuelve a contener el token UN y se
clasificaba F en vez de H: se perdía la penalización de docencia y, con ella,
la teacher_alert. Ídem «UNO» italiano.
"""

import pytest

from services.job_classifier import classify_job


class TestP32ClasificadorMayusculas:
    @pytest.mark.parametrize(
        "title",
        [
            "Un-e enseignant-e primaire",
            "ENSEIGNANT-E PRIMAIRE POUR UN REMPLACEMENT",
            "UN-E ENSEIGNANT-E PRIMAIRE",
            "UNO INSTRUCTOR PER LA SCUOLA ELEMENTARE",
        ],
    )
    def test_el_articulo_no_secuestra_la_docencia(self, title):
        """El artículo francés/italiano no puede ganarle a la categoría H."""
        assert classify_job(title=title, tags=[]) == "H"

    @pytest.mark.parametrize(
        "title",
        [
            "UN Volunteer Programme Officer",
            "ILO Senior Economist",
        ],
    )
    def test_el_organismo_en_titulo_mixto_sigue_siendo_f(self, title):
        """No-regresión de G1/P2-9: el acrónimo real sigue clasificando F."""
        assert classify_job(title=title, tags=[]) == "F"

    def test_el_acronimo_en_minusculas_sigue_sin_casar(self):
        """No-regresión: «un» minúscula nunca fue el organismo."""
        assert classify_job(title="Un poste de developpeur backend", tags=[]) != "F"
