"""G4 — familia del DEDUP SEMÁNTICO (`services/deduplicator.py`).

- **P3-6**: la segunda condición del veredicto es puramente LÉXICA
  (`_title_overlap`), y `_TITLE_OVERLAP_MIN` era una constante de módulo. Eso
  dejaba `SEMANTIC_DEDUP_THRESHOLD` sin efecto como mando de remediación:
  bajar el umbral del coseno no servía de nada porque la puerta léxica seguía
  rechazando. Medido con el encoder real: 20/20 pares de la MISMA vacante en
  DE↔FR/IT/EN rechazados, 15 de ellos con solape exactamente 0.000.
- **P3-7**: las puertas de cantón y salario rechazaron 0 pares en el barrido de
  6 umbrales. `_cantons_conflict` comparaba el string crudo (`'zh'` vs `'ZH'`
  → conflicto inventado) y el salario decidía sobre el campo MENOS fiable del
  corpus (88 filas activas con `salary_min_chf < 20000`, 598 con salario y
  `salary_period NULL`: mensuales guardados como anuales).
"""

import pytest

from config import settings
from models.job import Job
from services.deduplicator import Deduplicator

# Solo las clases con acceso a BD son async; las puertas puras son síncronas.

_BOILERPLATE = (
    "Die Stadt Musterhausen ist eine moderne Arbeitgeberin mit rund 900 "
    "Mitarbeitenden und bietet attraktive Anstellungsbedingungen. "
)
_SAME_VECTOR = [0.1] * 384


def _vacancy(hash_, source, title, body, **kw) -> Job:
    return Job(
        hash=hash_.ljust(32, "0"),
        source=source,
        title=title,
        company="Stadt Musterhausen",
        url=f"http://example.com/{hash_}",
        description=_BOILERPLATE * 3 + body,
        embedding=_SAME_VECTOR,
        is_active=True,
        **kw,
    )


@pytest.mark.asyncio
class TestP36LaPuertaLexicaEsConfigurableYNoBloqueaEntreIdiomas:
    async def test_la_misma_vacante_en_dos_idiomas_ya_no_se_rechaza(self, db_session):
        """DE↔FR de la MISMA vacante: solape léxico 0.000, coseno alto."""
        canonical = _vacancy(
            "g4dedlangde",
            "publicjobs",
            "Primarlehrperson 60%",
            "Unterricht in der Primarschule.",
            language="de",
        )
        candidate = _vacancy(
            "g4dedlangfr",
            "schuljobs",
            "Enseignant-e primaire 60%",
            "Enseignement au degre primaire.",
            language="fr",
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        assert (
            Deduplicator._title_overlap(canonical.title, candidate.title)
            < settings.SEMANTIC_DEDUP_TITLE_OVERLAP_MIN
        )
        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == [
            canonical.hash
        ], (
            "la puerta léxica rechaza el par cross-idioma: es el único aporte "
            "propio del camino semántico y entre idiomas no mide nada"
        )

    async def test_el_mismo_idioma_sigue_exigiendo_lexico_comun(self, db_session):
        """No-regresión de G3/P1-2: los gemelos de boilerplate no se marcan."""
        canonical = _vacancy(
            "g4dedsamede1",
            "publicjobs",
            "Sachbearbeiter Finanzbuchhaltung 80-100%",
            "Fuehrung der Finanzbuchhaltung.",
            language="de",
        )
        candidate = _vacancy(
            "g4dedsamede2",
            "schuljobs",
            "Gaertner Gruenflaechenunterhalt 100%",
            "Pflege der Gruenflaechen.",
            language="de",
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == []

    async def test_el_umbral_lexico_es_un_setting(self, db_session, monkeypatch):
        """Bajar `SEMANTIC_DEDUP_THRESHOLD` no servía de nada mientras la
        puerta léxica fuera una constante de módulo."""
        canonical = _vacancy(
            "g4dedovl1",
            "publicjobs",
            "Primarlehrperson 60%",
            "Unterricht in der Primarschule.",
            language="de",
        )
        candidate = _vacancy(
            "g4dedovl2",
            "schuljobs",
            "Lehrperson Primarstufe 60%",
            "Unterricht in der Primarschule.",
            language="de",
        )
        db_session.add_all([canonical, candidate])
        await db_session.commit()

        # Solape 0.25 < 0.3 por defecto → rechazado.
        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == []

        monkeypatch.setattr(settings, "SEMANTIC_DEDUP_TITLE_OVERLAP_MIN", 0.2)
        assert await Deduplicator.find_semantic_duplicates(db_session, candidate) == [
            canonical.hash
        ]


class TestP37CantonYSalario:
    def test_el_canton_se_compara_en_mayusculas(self):
        assert Deduplicator._cantons_conflict("zh", "ZH") is False, (
            "los 8 scrapers que escriben `canton` a mano no pasan por "
            "extract_canton: un 'zh' inventaba un conflicto y vetaba en silencio"
        )
        assert Deduplicator._cantons_conflict(" ZH ", "ZH") is False
        assert Deduplicator._cantons_conflict("ZH", "GE") is True
        assert Deduplicator._cantons_conflict(None, "GE") is False

    def test_el_salario_no_veta_con_periodos_distintos_o_nulos(self):
        mensual = Job(salary_min_chf=3500, salary_max_chf=4000, salary_period="monthly")
        anual = Job(salary_min_chf=80000, salary_max_chf=95000, salary_period="yearly")
        sin_periodo = Job(salary_min_chf=80000, salary_max_chf=95000)

        assert Deduplicator._salaries_conflict(mensual, anual) is False, (
            "un 3.500/mes contra un 80.000/año no es un conflicto, es otra unidad"
        )
        assert Deduplicator._salaries_conflict(sin_periodo, anual) is False, (
            "598 filas activas tienen salario y `salary_period NULL`"
        )

    def test_el_salario_sigue_vetando_con_el_mismo_periodo(self):
        """No-regresión de G3/P1-2: comparables y disjuntos ⇒ vacantes distintas."""
        bajo = Job(salary_min_chf=60000, salary_max_chf=70000, salary_period="yearly")
        alto = Job(salary_min_chf=110000, salary_max_chf=130000, salary_period="yearly")
        assert Deduplicator._salaries_conflict(bajo, alto) is True
