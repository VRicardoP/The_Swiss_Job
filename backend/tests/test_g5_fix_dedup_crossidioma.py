"""G5/P2-2 — se RETIRA el dedup cross-idioma de `a63745c`.

`a63745c` cerraba G4/P3-6 saltándose la puerta léxica cuando los dos idiomas
declarados difieren. Pero la puerta léxica es —según el docstring del propio
módulo— el único aporte propio del camino semántico: el coseno «sigue midiendo
boilerplate» (cota de G3/P1-2). Al retirarla, el veredicto cross-idioma quedaba
en manos de ese coseno saturado, del cantón y de un salario que el mismo commit
acababa de debilitar.

Medido con el encoder real (`paraphrase-multilingual-MiniLM-L12-v2`) sobre
ofertas del MISMO municipio con su boilerplate:

    maestra de primaria (DE) vs contable de deudores (FR) ... 0.8220
    LA MISMA vacante en DE y en FR (duplicado real) ......... 0.8195

La separación está **INVERTIDA**: el par que NO es duplicado puntúa por encima
del que sí lo es. No hay ningún valor de `SEMANTIC_DEDUP_THRESHOLD` que sirva.

Y el fix tampoco lograba su objetivo: con el umbral por defecto (0.95) el
prefiltro SQL mata el par real a 0.8195 una capa antes. Sobre una muestra
aleatoria de 200 activas de producción, los pares cross-source cross-idioma son
0 a los umbrales 0.95 / 0.86 / 0.82 / 0.80.

El precio de equivocarse no es cosmético: `mark_duplicate` escribe
`duplicate_of` **y `is_active=False`**.

Este fichero fija el comportamiento RESTAURADO: la puerta léxica se aplica
SIEMPRE. La cota aceptada —el duplicado real cross-idioma no se recoge— se
fija también, a propósito, para que nadie la «arregle» sin leer esto.
"""

import pytest

from config import settings
from models.job import Job
from services.deduplicator import Deduplicator

_BOIL_DE = (
    "Die Stadt Musterhausen ist eine moderne Arbeitgeberin mit rund 900 "
    "Mitarbeitenden und bietet attraktive Anstellungsbedingungen, flexible "
    "Arbeitszeiten und gute Weiterbildungsmoeglichkeiten. "
)
_BOIL_FR = (
    "La Ville de Musterhausen est une employeuse moderne comptant environ 900 "
    "collaborateurs et offre des conditions d'engagement attractives, des "
    "horaires flexibles et de bonnes possibilites de formation continue. "
)
# Vector idéntico para los dos lados: el prefiltro SQL pasa SIEMPRE (distancia
# coseno 0). Así el test ejercita la PUERTA y no el prefiltro — que es donde
# `a63745c` quedaba desarmado.
_SAME_VECTOR = [0.1] * 384


def _vacancy(hash_, source, title, company, body, **kw) -> Job:
    return Job(
        hash=hash_.ljust(32, "0"),
        source=source,
        title=title,
        company=company,
        url=f"http://example.com/{hash_}",
        description=body,
        embedding=_SAME_VECTOR,
        is_active=True,
        **kw,
    )


@pytest.mark.asyncio
class TestLaPuertaLexicaNoSeSaltaEntreIdiomas:
    async def test_dos_vacantes_SIN_RELACION_no_se_marcan_duplicadas(self, db_session):
        """El falso positivo medido: maestra (DE) vs contable (FR), 0.8220.

        Con el vector idéntico el prefiltro deja pasar el candidato, así que lo
        único que separa a estas dos vacantes es la puerta léxica.
        """
        maestra = _vacancy(
            "g5fpde",
            "publicjobs",
            "Primarlehrperson 60%",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="de",
        )
        contable = _vacancy(
            "g5fpfr",
            "schuljobs",
            "Comptable debiteurs 80%",
            "Ville de Musterhausen",
            _BOIL_FR * 3 + "Vous tenez la comptabilite des debiteurs.",
            language="fr",
        )
        db_session.add_all([maestra, contable])
        await db_session.commit()

        assert Deduplicator._title_overlap(maestra.title, contable.title) == 0.0

        for thr in (0.95, 0.86, 0.82, 0.80):
            assert (
                await Deduplicator.find_semantic_duplicates(
                    db_session, contable, threshold=thr
                )
                == []
            ), (
                f"con umbral {thr} un contable de deudores se marca duplicado "
                "de una maestra de primaria — y `mark_duplicate` pone "
                "is_active=False: la vacante real desaparece del catálogo"
            )

    async def test_la_cota_cross_idioma_esta_declarada_y_es_el_lado_seguro(
        self, db_session
    ):
        """El duplicado REAL DE/FR tampoco se recoge: cota aceptada, por escrito.

        Es el falso negativo que `a63745c` quería cerrar. Se prefiere a
        desactivar vacantes legítimas — un duplicado que sobrevive se ve en el
        catálogo; una oferta real desactivada, no.
        """
        de = _vacancy(
            "g5xlde",
            "publicjobs",
            "Primarlehrperson 60%",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="de",
        )
        fr = _vacancy(
            "g5xlfr",
            "schuljobs",
            "Enseignant-e primaire 60%",
            "Ville de Musterhausen",
            _BOIL_FR * 3 + "Vous enseignez a l'ecole primaire.",
            language="fr",
        )
        db_session.add_all([de, fr])
        await db_session.commit()

        assert await Deduplicator.find_semantic_duplicates(db_session, fr) == []

    async def test_el_mismo_idioma_sigue_deduplicando(self, db_session):
        """Guardarraíl: retirar la excepción no puede romper el camino que sí
        funciona (mismo idioma, solape léxico real)."""
        a = _vacancy(
            "g5samea",
            "publicjobs",
            "Primarlehrperson 60% Musterhausen",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="de",
        )
        b = _vacancy(
            "g5sameb",
            "schuljobs",
            "Primarlehrperson 60% Musterhausen",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="de",
        )
        db_session.add_all([a, b])
        await db_session.commit()

        assert await Deduplicator.find_semantic_duplicates(db_session, b) == [a.hash]

    async def test_el_idioma_ausente_o_en_otra_caja_ya_no_cambia_el_veredicto(
        self, db_session
    ):
        """`language` ya no participa: 'DE'/'de' o NULL dan el MISMO resultado.

        Antes, `job.language != row.language` se comparaba EN CRUDO, así que un
        productor que emitiera 'DE' convertía un par del mismo idioma en
        cross-idioma y saltaba la puerta léxica sin necesidad de tocar ningún
        umbral.
        """
        maestra = _vacancy(
            "g5cja",
            "publicjobs",
            "Primarlehrperson 60%",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="DE",
        )
        contable = _vacancy(
            "g5cjb",
            "schuljobs",
            "Comptable debiteurs 80%",
            "Ville de Musterhausen",
            _BOIL_FR * 3 + "Vous tenez la comptabilite des debiteurs.",
            language="de",
        )
        db_session.add_all([maestra, contable])
        await db_session.commit()

        assert (
            await Deduplicator.find_semantic_duplicates(
                db_session, contable, threshold=0.80
            )
            == []
        )


@pytest.mark.asyncio
class TestElMandoDeRemediacionSigueConectado:
    async def test_bajar_el_solape_minimo_sigue_siendo_el_mando_util(
        self, db_session, monkeypatch
    ):
        """`SEMANTIC_DEDUP_TITLE_OVERLAP_MIN` se conserva: es el mando que SÍ
        actúa sobre la puerta, y ahora es el único que la afecta."""
        a = _vacancy(
            "g5ova",
            "publicjobs",
            "Primarlehrperson Musterhausen Unterstufe",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="de",
        )
        b = _vacancy(
            "g5ovb",
            "schuljobs",
            "Primarlehrperson Oberstufe Winterthur Teilzeit Sofort",
            "Stadt Musterhausen",
            _BOIL_DE * 3 + "Sie unterrichten an der Primarschule.",
            language="de",
        )
        db_session.add_all([a, b])
        await db_session.commit()

        overlap = Deduplicator._title_overlap(a.title, b.title)
        assert 0.0 < overlap < 0.3, f"solape de apoyo fuera de rango: {overlap}"

        monkeypatch.setattr(settings, "SEMANTIC_DEDUP_TITLE_OVERLAP_MIN", 0.3)
        assert await Deduplicator.find_semantic_duplicates(db_session, b) == []

        monkeypatch.setattr(settings, "SEMANTIC_DEDUP_TITLE_OVERLAP_MIN", 0.1)
        assert await Deduplicator.find_semantic_duplicates(db_session, b) == [a.hash]
