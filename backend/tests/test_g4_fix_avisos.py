"""G4 — familia de los AVISOS: alerta de profesor de primaria.

- **P1-2**: `alert_tasks` guardaba la marca de agua ANTES del `email.send`. Un
  fallo de SMTP —evento ordinario: 421/429, corte de red, credencial rotada—
  retiraba los marcadores por-oferta (correcto) pero dejaba la marca ya
  avanzada, y la marca solo retrocede `NOTIFY_WATERMARK_LAG_MINUTES` (15 min).
  Con la cosecha diaria y la alerta cada 6 h, TODA oferta de la ventana tiene
  horas de antigüedad: caía bajo la marca nueva y no volvía a entrar en
  ninguna corrida. Pérdida permanente y silenciosa del lote ENTERO.
- **P1-4**: la forma epicena del francés administrativo suizo
  («Enseignant-e», «Enseignants-es», «Enseignant·e», «Enseignant(e)») no casa
  con los literales contiguos de `_PRIMARY_MARKERS`. El clasificador daba `H`,
  la alerta se quedaba muda. Hay una vacante REAL así en el corpus
  (`swiss_schools_nae | Enseignants-es Primaire | Collège Champittet | H`).
  La mordida es END-TO-END: `DataNormalizer.normalize` → categoría →
  `is_primary_teacher_job` → email realmente construido y enviado.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import text, update

from config import settings
from models.job import Job
from services.data_normalizer import DataNormalizer


class _FakeRedis:
    """Doble de Redis con el contrato de las marcas: get/set(nx,ex)/delete."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value if isinstance(value, bytes) else str(value).encode()
        return True

    def delete(self, key):
        self.store.pop(key, None)

    def close(self):
        pass


class _FakeEmail:
    is_available = True

    def __init__(self, fail: bool = False):
        self.sent: list[tuple] = []
        self.fail = fail

    def send(self, to, subject, text_body, html):
        if self.fail:
            raise RuntimeError("SMTP 421 servicio no disponible")
        self.sent.append((to, subject, text_body, html))


@asynccontextmanager
async def _session_of(db):
    yield db


def _raw_job(title: str, hash_: str) -> dict:
    """Oferta cruda tal como la emite el scraper de la watchlist de colegios."""
    return {
        "hash": hash_,
        "source": "swiss_schools_nae",
        "title": title,
        "company": "Collège Champittet",
        "url": f"https://example.test/job/{hash_}",
        "location": "Pully",
        "canton": "VD",
        "description": "Poste au sein d'un etablissement scolaire international.",
        "description_snippet": "Poste au sein d'un etablissement...",
        "remote": False,
        # Tags REALES de la watchlist de colegios (mismo literal que usa
        # tests/test_g3_fix_flancos_gemelos.py): son las que llevan la oferta
        # a la categoría H por el camino de ingesta.
        "tags": ["education", "international school", "nae_zurich"],
        "logo": None,
        "salary_min_chf": None,
        "salary_max_chf": None,
        "salary_original": None,
        "salary_currency": None,
        "salary_period": None,
        "language": None,
        "seniority": None,
        "contract_type": None,
        "employment_type": None,
    }


async def _persist_normalized(db, title: str, *, hours_old: float) -> str:
    """Normaliza por el camino REAL (categoría incluida) y persiste la fila."""
    job = DataNormalizer.normalize(_raw_job(title, uuid.uuid4().hex))
    db.add(
        Job(
            hash=job["hash"],
            source=job["source"],
            title=job["title"],
            company=job["company"],
            url=job["url"],
            canton=job["canton"],
            location=job["location"],
            tags=job["tags"],
            category=job["category"],
            is_active=True,
        )
    )
    await db.commit()
    await db.execute(
        update(Job)
        .where(Job.hash == job["hash"])
        .values(
            first_seen_at=datetime.now(timezone.utc) - timedelta(hours=hours_old),
            last_seen_at=text("NOW()"),
        )
    )
    await db.commit()
    return job["category"]


async def _run_alert(db, fake_redis, email):
    from tasks import alert_tasks

    with (
        patch("redis.from_url", lambda *a, **k: fake_redis),
        patch("database.task_session", new=lambda: _session_of(db)),
        patch("services.email_service.EmailService", lambda *a, **k: email),
    ):
        return await alert_tasks._detect_and_notify()


@pytest.fixture
def alerta_activa(monkeypatch):
    monkeypatch.setattr(settings, "TEACHER_ALERT_ENABLED", True)
    monkeypatch.setattr(settings, "TEACHER_ALERT_EMAIL", "aviso@example.com")


@pytest.mark.asyncio
class TestP12MarcaDespuesDelEnvio:
    async def test_un_fallo_de_smtp_no_pierde_el_lote_para_siempre(
        self, db_session, alerta_activa
    ):
        """La oferta nace 3 h antes: FUERA del lag de 15 min, que es el caso
        real (cosecha diaria, alerta cada 6 h)."""
        fake = _FakeRedis()
        categoria = await _persist_normalized(
            db_session, "Primarlehrperson 60%", hours_old=3
        )
        assert categoria == "H"

        # Corrida 1: SMTP caído. La excepción sube (la tarea reintenta).
        with pytest.raises(RuntimeError):
            await _run_alert(db_session, fake, _FakeEmail(fail=True))

        # Corrida 2 (retry inmediato) y corrida 3 (6 h después, misma marca):
        # la oferta DEBE seguir entrando.
        ok = _FakeEmail()
        segunda = await _run_alert(db_session, fake, ok)
        assert segunda["matched"] == 1, (
            "la marca avanzó pese al fallo de SMTP: la oferta cayó bajo la "
            "marca nueva y no vuelve a entrar en ninguna corrida"
        )
        assert len(ok.sent) == 1

        # Corrida 3: la marca ya avanzó (envío correcto), así que la oferta
        # sale de la ventana. Lo que NO puede pasar es un segundo email.
        tercera = await _run_alert(db_session, fake, ok)
        assert tercera["matched"] == 0, "el marcador por-oferta no evitó el reenvío"
        assert len(ok.sent) == 1

    async def test_sin_novedades_la_marca_avanza_igual(self, db_session, alerta_activa):
        """Con `fresh` vacío no hay envío que proteger: la marca debe avanzar
        (si no, la ventana crecería sin límite)."""
        from tasks.alert_tasks import _WATERMARK_KEY

        fake = _FakeRedis()
        result = await _run_alert(db_session, fake, _FakeEmail())

        assert result["status"] == "success"
        assert fake.get(_WATERMARK_KEY) is not None


@pytest.mark.asyncio
class TestP14FormaEpicenaFrancesa:
    @pytest.mark.parametrize(
        "title",
        [
            "Enseignants-es Primaire",  # la vacante REAL de Collège Champittet
            "ENSEIGNANT-E PRIMAIRE POUR UN REMPLACEMENT",
            "UN-E ENSEIGNANT-E PRIMAIRE",
            "Enseignant-e primaire",
            "Enseignant·e primaire",
            "Enseignant(e) primaire",
        ],
    )
    async def test_la_alerta_llega_hasta_el_email(
        self, db_session, alerta_activa, title
    ):
        """End-to-end: normalización real → categoría H → alerta → EMAIL.

        Los ciclos anteriores se detuvieron en la categoría; el defecto vivía
        en el segundo filtro (`is_primary_teacher_job`).
        """
        fake = _FakeRedis()
        categoria = await _persist_normalized(db_session, title, hours_old=2)
        assert categoria == "H", "el clasificador ya no da H: cambió otro flanco"

        email = _FakeEmail()
        result = await _run_alert(db_session, fake, email)

        assert result["matched"] == 1, (
            f"la forma epicena «{title}» deja MUDA la alerta de profesor de "
            "primaria — es el caso de uso central del propietario y la grafía "
            "dominante en la Suiza francófona"
        )
        assert len(email.sent) == 1
        assert title in email.sent[0][2]

    async def test_no_dispara_con_docencia_no_primaria(self, db_session, alerta_activa):
        """Cota del marcador compuesto: sin «primaire» no hay alerta."""
        fake = _FakeRedis()
        await _persist_normalized(
            db_session, "Enseignant-e de mathematiques au secondaire II", hours_old=2
        )
        result = await _run_alert(db_session, fake, _FakeEmail())
        assert result["matched"] == 0
