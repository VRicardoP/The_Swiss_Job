"""Regresiones de la auditoría G1 — filtros de exclusión y análisis de patrones.

- P2-16: los patrones tag_contains se guardan en minúsculas pero el operador
  JSONB @> es exact-match case-sensitive y los tags se ingieren capitalizados
  («Informatik»): el análisis prometía «excluirá N jobs», el usuario aprobaba
  y el filtro no excluía nunca — fallo presentado como éxito.
- P3-23: el test de «patrón cubierto» estaba invertido (suprimía el patrón
  con más cobertura / dejaba pasar redundantes) y llevaba una tautología.
"""

import uuid

import pytest

from models.job import Job
from services.match_service import MatchService
from services.pattern_analysis_service import PatternAnalysisService


@pytest.mark.asyncio
class TestP216TagFilterCaseInsensitive:
    async def test_filtro_minusculas_excluye_tag_capitalizado(self, db_session):
        marker = uuid.uuid4().hex[:8]
        job_hash = f"g1p216-{marker}".ljust(32, "0")[:32]
        db_session.add(
            Job(
                hash=job_hash,
                source="test",
                title="Fachinformatiker Systemintegration",
                company="Acme",
                url=f"https://e.ch/tag-{marker}",
                is_active=True,
                tags=["Informatik", "IT"],  # capitalizado, como base_chmedia
                embedding=[0.1] * 384,
            )
        )
        await db_session.commit()

        svc = MatchService(db_session)
        probe_embedding = [0.1] * 384

        # Sin filtro: el job es candidato.
        sin_filtro = await svc._stage1_vector_search(probe_embedding, set(), [])
        assert job_hash in {job.hash for job, _ in sin_filtro}

        # Con el filtro aprobado (guardado en minúsculas): DEBE excluirlo.
        con_filtro = await svc._stage1_vector_search(
            probe_embedding,
            set(),
            [{"type": "tag_contains", "pattern": "informatik"}],
        )
        assert job_hash not in {job.hash for job, _ in con_filtro}, (
            "el filtro 'informatik' debe excluir el tag 'Informatik'"
        )

    async def test_job_sin_tags_no_se_excluye(self, db_session):
        marker = uuid.uuid4().hex[:8]
        job_hash = f"g1p216b-{marker}".ljust(32, "0")[:32]
        db_session.add(
            Job(
                hash=job_hash,
                source="test",
                title="Content Editor",
                company="Acme",
                url=f"https://e.ch/notags-{marker}",
                is_active=True,
                tags=[],
                embedding=[0.1] * 384,
            )
        )
        await db_session.commit()

        svc = MatchService(db_session)
        result = await svc._stage1_vector_search(
            [0.1] * 384, set(), [{"type": "tag_contains", "pattern": "informatik"}]
        )
        assert job_hash in {job.hash for job, _ in result}


class TestP323PatronCubierto:
    def _jobs(self):
        jobs = []
        for _ in range(5):
            jobs.append(
                {
                    "title": "Blockchain Engineer Lead",
                    "company": "Acme",
                    "tags": [],
                    "feedback": "thumbs_down",
                }
            )
        for _ in range(3):
            jobs.append(
                {
                    "title": "Blockchain Engineer",
                    "company": "Acme",
                    "tags": [],
                    "feedback": "thumbs_down",
                }
            )
        return jobs

    def test_ngramas_redundantes_se_suprimen(self):
        """G1/P3-23: 'blockchain engineer' es redundante si 'blockchain' ya
        fue sugerido — el test invertido lo dejaba pasar."""
        svc = PatternAnalysisService(db=None)
        jobs = self._jobs()
        suggestions = svc._analyze_title_patterns(jobs, jobs, min_rejected=3)
        patterns = [s["pattern"] for s in suggestions]

        assert "blockchain" in patterns
        # Ningún patrón sugerido debe ser superconjunto (en palabras) de otro.
        for i, p in enumerate(patterns):
            for q in patterns[:i] + patterns[i + 1 :]:
                assert not set(q.split()) < set(p.split()), (
                    f"'{p}' es redundante con '{q}' y no debió sugerirse"
                )
