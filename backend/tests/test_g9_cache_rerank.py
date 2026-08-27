"""G9 — la caché de re-ranking pasa de no acertar NUNCA a acertar casi siempre.

La clave de antes hasheaba el prompt del LOTE entero, así que llevaba el
índice del lote, el número de lotes y el orden exacto del pool — y ese orden lo
mueve `recency_score`, una función escalonada del tiempo. Evidencia medida en
producción antes del cambio: CERO claves `groq:rerank:*` vivas en Redis.

Estos tests CUENTAN las llamadas al modelo (`stub_llm_boundary` registra cada
llamada saliente). Con la clave anterior, el primero de ellos falla.
"""

import pytest

from services.groq_service import GroqService
from tests.llm_stub import jobs_in_prompt

_PROFILE = "Bilingual editor with 8 years of localisation experience."
_SKILLS = ["editing", "localisation"]


class _FakeRedis:
    """Redis en memoria con la superficie que usa la caché de veredictos."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def set(self, key, value, ex=None):
        self.store[key] = value.encode() if isinstance(value, str) else value


def _candidates(n: int, *, title: str = "Content Editor") -> list[dict]:
    return [
        {
            "title": f"{title} {i}",
            "company": f"Acme {i}",
            "description": f"Editorial work, position {i}.",
            "tags": ["editing"],
            "location": "Zurich",
            "remote": False,
            "language": "en",
            "contract_type": "",
        }
        for i in range(n)
    ]


def _ofertas_evaluadas(calls: list[dict]) -> int:
    """Cuántas ofertas se le enseñaron al modelo en total."""
    return sum(len(jobs_in_prompt(c["user"])) for c in calls)


@pytest.fixture
def svc():
    return GroqService(redis_client=_FakeRedis())


async def _rerank(service: GroqService, candidates: list[dict]) -> list[dict]:
    return await service.rerank_jobs(
        profile_text=_PROFILE, profile_skills=_SKILLS, candidates=candidates
    )


class TestLaCacheAcierta:
    async def test_la_segunda_corrida_no_llama_al_modelo(self, svc, stub_llm_boundary):
        candidates = _candidates(15)

        results = await _rerank(svc, candidates)
        assert len(results) == 15
        assert len(stub_llm_boundary) == 2, "15 ofertas / lote de 10 = 2 llamadas"

        stub_llm_boundary.clear()
        repetidos = await _rerank(svc, candidates)

        assert stub_llm_boundary == [], (
            "el corpus no ha cambiado: la segunda corrida no debe llamar al LLM"
        )
        assert [r["score"] for r in repetidos] == [r["score"] for r in results]

    async def test_el_orden_del_pool_no_invalida_la_cache(self, svc, stub_llm_boundary):
        candidates = _candidates(15)
        await _rerank(svc, candidates)
        stub_llm_boundary.clear()

        await _rerank(svc, list(reversed(candidates)))

        assert stub_llm_boundary == [], (
            "reordenar el pool no cambia ningún veredicto y no debe invalidar nada"
        )

    async def test_solo_se_reevalua_la_oferta_nueva(self, svc, stub_llm_boundary):
        candidates = _candidates(15)
        await _rerank(svc, candidates)
        stub_llm_boundary.clear()

        nueva = {**_candidates(1, title="Localisation Manager")[0], "company": "New Co"}
        await _rerank(svc, [nueva, *candidates])

        assert len(stub_llm_boundary) == 1
        assert _ofertas_evaluadas(stub_llm_boundary) == 1, (
            "solo la oferta nueva debe llegar al modelo"
        )


class TestLaCacheNoSirveLoQueNoCorresponde:
    async def test_cambiar_la_oferta_invalida_su_veredicto(
        self, svc, stub_llm_boundary
    ):
        candidates = _candidates(3)
        await _rerank(svc, candidates)
        stub_llm_boundary.clear()

        tocada = [{**candidates[0], "description": "Otra cosa."}, *candidates[1:]]
        await _rerank(svc, tocada)

        assert _ofertas_evaluadas(stub_llm_boundary) == 1

    async def test_cambiar_el_perfil_invalida_los_veredictos(
        self, svc, stub_llm_boundary
    ):
        candidates = _candidates(3)
        await _rerank(svc, candidates)
        stub_llm_boundary.clear()

        await svc.rerank_jobs(
            profile_text="Backend engineer, Python and PostgreSQL.",
            profile_skills=_SKILLS,
            candidates=candidates,
        )

        assert _ofertas_evaluadas(stub_llm_boundary) == 3, (
            "otro candidato: los tres veredictos hay que rehacerlos"
        )

    async def test_cambiar_el_modelo_invalida_los_veredictos(
        self, svc, stub_llm_boundary, monkeypatch
    ):
        candidates = _candidates(3)
        await _rerank(svc, candidates)
        stub_llm_boundary.clear()

        monkeypatch.setattr(
            "services.groq_service.settings.GROQ_RERANK_MODEL", "otro/modelo"
        )
        await _rerank(svc, candidates)

        assert _ofertas_evaluadas(stub_llm_boundary) == 3, (
            "un modelo distinto no puede heredar los veredictos del anterior"
        )
