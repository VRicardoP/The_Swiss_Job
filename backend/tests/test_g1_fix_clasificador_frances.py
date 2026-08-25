"""Regresión de la auditoría G1 — P2-9: artículos «un»/«uno» robaban a la
categoría H la penalización de docencia.

El .strip() de la compilación borraba el espacio protector de "un " y el
patrón \bun\b (IGNORECASE) casaba el artículo francés: con escritura
inclusiva suiza («Un-e enseignant-e primaire»), F se evalúa antes que H y la
vacante salía F (×1.0) en vez de H (×0.15) — y teacher_alert (detección por
H) se la perdía. Ídem «uno» italiano. Los acrónimos ahora solo casan como
token aislado EN MAYÚSCULAS (UN/UNO/WHO/ILO).
"""

from services.job_classifier import classify_job


class TestP29DocenciaFrancofona:
    def test_escritura_inclusiva_francesa_es_docencia(self):
        """La forma exacta de la sonda G1."""
        assert classify_job("Un-e enseignant-e primaire (80-100%)", []) == "H"

    def test_articulo_frances_no_es_onu(self):
        assert classify_job("Un enseignant primaire à Genève", []) == "H"

    def test_articulo_italiano_no_es_onu(self):
        # "Uno" italiano no debe caer en F (organismos internacionales).
        assert classify_job("Uno stagista amministrativo", []) != "F"

    def test_acronimo_mayusculas_sigue_siendo_f(self):
        """El camino legítimo de los acrónimos no se pierde."""
        assert classify_job("UN Programme Officer", []) == "F"
        assert classify_job("UNO Praktikum Genf", []) == "F"
        assert classify_job("WHO Health Policy Advisor", []) == "F"
        assert classify_job("ILO Research Assistant", []) == "F"
