"""G9 — el prefiltro del dedup semántico pasa por el índice HNSW.

El prefiltro de antes ponía la distancia coseno en el `WHERE` y ordenaba por
antigüedad, así que `ix_jobs_embedding_hnsw` (20 MB) no se usaba NUNCA:
`idx_scan = 2` de por vida. Ahora los vecinos salen del índice y el resto de
exclusiones se aplican después.

Lo que hay que proteger no es la velocidad, es el veredicto: `mark_duplicate`
escribe `duplicate_of` **y `is_active=False`**, o sea que equivocarse aquí
DESACTIVA una vacante real. Estos tests fijan las dos propiedades de las que
depende la equivalencia:

1. Con el radio holgado, los dos caminos devuelven EXACTAMENTE lo mismo.
2. Si el radio satura los vecinos pedidos, el camino rápido se descarta y se
   usa el barrido exacto — porque con el conjunto truncado el prefiltro por
   antigüedad podría elegir otro canónico.

Contra el corpus real (8.162 candidatos, los dos caminos uno detrás de otro)
la comprobación (1) dio 0 diferencias, ninguna entrada saturó los 500 vecinos
y el racimo más grande dentro del radio fue de 43.
"""

import pytest
from sqlalchemy import select

from models.job import Job
from services.deduplicator import Deduplicator

_MAX_DISTANCE = 0.05

# Vectores construidos para tener distancias coseno CONOCIDas contra `_BASE`:
# el señuelo cae a ~0,0005 y el canónico a ~0,040 — los dos dentro del radio,
# pero el canónico MÁS LEJOS, que es lo que lo deja fuera de un prefiltro por
# cercanía truncado.
_BASE = [1.0] + [0.0] * 383
_CERCA = [1.0, 0.03] + [0.0] * 382
_LEJOS = [1.0, 0.29] + [0.0] * 382
# Fuera del radio: sin al menos una fila así, TODAS las que devuelve el
# prefiltro caerían dentro y el código —correctamente— se iría al barrido
# exacto, con lo que el camino rápido no se probaría nunca.
_FUERA = [1.0, 1.0] + [0.0] * 382

_DESCRIPCION = "Beschreibung der Stelle in der Gemeinde Musterhausen. " * 5


def _oferta(marca: str, source: str, title: str, embedding: list, dia: int) -> Job:
    from datetime import datetime, timezone

    return Job(
        hash=marca.ljust(32, "0")[:32],
        source=source,
        title=title,
        company="Gemeinde Musterhausen",
        url=f"https://example.com/g9-hnsw/{marca}",
        description=_DESCRIPCION,
        embedding=embedding,
        is_active=True,
        first_seen_at=datetime(2026, 1, dia, tzinfo=timezone.utc),
    )


async def _sembrar(db) -> Job:
    """Entrada + 1 canónico legítimo (lejos y antiguo) + 5 señuelos (cerca)."""
    entrada = _oferta("g9hnswIN", "portal_a", "Primarlehrperson Musterhausen", _BASE, 9)
    canonico = _oferta(
        "g9hnswCANON",
        "portal_b",
        "Primarlehrperson Musterhausen 80%",
        _LEJOS,
        1,  # el MÁS ANTIGUO: es el canónico que exige el orden por antigüedad
    )
    db.add(entrada)
    db.add(canonico)
    for i in range(5):
        db.add(
            _oferta(
                f"g9hnswDECOY{i}",
                "portal_c",
                f"Gaertner Gruenflaechenunterhalt {i}",
                _CERCA,
                2 + i,
            )
        )
    db.add(_oferta("g9hnswFUERA", "portal_d", "Nada que ver", _FUERA, 8))
    await db.commit()
    return entrada


async def _recargar(db, entrada: Job) -> Job:
    return await db.scalar(select(Job).where(Job.hash == entrada.hash))


class TestLosDosCaminosCoinciden:
    async def test_mismo_conjunto_de_candidatos_con_radio_holgado(self, db_session):
        entrada = await _recargar(db_session, await _sembrar(db_session))

        rapido = await Deduplicator._oldest_candidates(
            db_session, entrada, _MAX_DISTANCE
        )
        exacto = await Deduplicator._oldest_candidates_exact(
            db_session, entrada, _MAX_DISTANCE
        )

        assert [r.hash for r in rapido] == [r.hash for r in exacto]
        assert [r.hash for r in exacto], "el escenario debe producir candidatos"

    async def test_el_veredicto_es_el_canonico_mas_antiguo(self, db_session):
        entrada = await _recargar(db_session, await _sembrar(db_session))

        assert await Deduplicator.find_semantic_duplicates(db_session, entrada) == [
            "g9hnswCANON".ljust(32, "0")[:32]
        ]


class TestSaturacionCaeAlBarridoExacto:
    async def test_con_los_vecinos_truncados_sigue_saliendo_el_mismo_canonico(
        self, db_session, monkeypatch
    ):
        """Los 3 vecinos más cercanos son los 3 señuelos, y el canónico se
        queda fuera. Sin la caída al barrido exacto, el prefiltro devolvería
        solo señuelos, la puerta léxica los rechazaría y el duplicado real
        pasaría desapercibido."""
        entrada = await _recargar(db_session, await _sembrar(db_session))
        monkeypatch.setattr(
            "services.deduplicator._SEMANTIC_HNSW_NEIGHBOURS", 3, raising=True
        )

        candidatos = await Deduplicator._oldest_candidates(
            db_session, entrada, _MAX_DISTANCE
        )
        veredicto = await Deduplicator.find_semantic_duplicates(db_session, entrada)

        canonico = "g9hnswCANON".ljust(32, "0")[:32]
        assert canonico in [r.hash for r in candidatos], (
            "el prefiltro truncado no puede perder al canónico"
        )
        assert veredicto == [canonico]

    async def test_sin_saturacion_no_se_toca_el_barrido_exacto(
        self, db_session, monkeypatch
    ):
        """Guarda del guardián: con el tope holgado, el camino rápido resuelve
        solo. Si no fuera así, el test anterior pasaría por el motivo
        equivocado."""
        entrada = await _recargar(db_session, await _sembrar(db_session))
        llamadas = []
        exacto = Deduplicator._oldest_candidates_exact

        async def _espia(db, job, max_distance):
            llamadas.append(job.hash)
            return await exacto(db, job, max_distance)

        monkeypatch.setattr(
            Deduplicator, "_oldest_candidates_exact", staticmethod(_espia)
        )

        await Deduplicator._oldest_candidates(db_session, entrada, _MAX_DISTANCE)

        assert llamadas == []


@pytest.mark.parametrize(
    ("vector", "dentro"), [(_CERCA, True), (_LEJOS, True), (_FUERA, False)]
)
async def test_las_distancias_del_escenario_son_las_que_se_supone(
    db_session, vector, dentro
):
    """Si estas distancias no fueran las esperadas, los tests de arriba
    pasarían en vacío o por el motivo equivocado."""
    db_session.add(_oferta("g9hnswPROBE", "portal_z", "Sonda", vector, 1))
    await db_session.commit()

    distancia = await db_session.scalar(
        select(Job.embedding.cosine_distance(_BASE)).where(
            Job.hash == "g9hnswPROBE".ljust(32, "0")[:32]
        )
    )
    assert (0.0 < distancia < _MAX_DISTANCE) is dentro
