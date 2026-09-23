"""JobTitleLanguage — idioma DERIVADO de un título, resuelto una vez.

Por qué existe (punto 5, 2026-09-22/23). El router deducía el idioma de CADA
oferta servida para el indicador de la UI: 50,1 ms por título y 1.800 ofertas
por petición ≈ 90 s. Memoizarlo en el proceso quitó el coste repetido pero no
la PRIMERA carga, que volvía a pagarlo entera tras cada arranque o expulsión.

Aquí el trabajo se hace UNA vez por título distinto y sobrevive a los
reinicios. El título es la clave porque la detección depende sólo de él: en el
feed medido había 1.544 títulos distintos para 1.800 ofertas, y los que se
repiten entre ofertas o entre perfiles se resuelven una sola vez para siempre.

**Los tres estados son explícitos, y esa es la parte que importa:**

| Estado | Fila | `language` |
|---|---|---|
| Nunca visto | ausente | — |
| Visto, pendiente de resolver | presente | `NULL` |
| Resuelto: el detector no decidió | presente | `''` |
| Resuelto | presente | `'de'`, `'fr'`… |

`''` NO es lo mismo que `NULL`: significa «ya se intentó y la respuesta es
desconocida», y evita volver a detectarlo eternamente. Sin esa distinción, un
título indecidible sería trabajo repetido en cada pasada.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base

# Cota de la clave: un título más largo que esto no existe en el corpus y el
# truncado mantiene el índice btree lejos de su límite de tamaño.
TITLE_MAX_LEN = 500


class JobTitleLanguage(Base):
    """Idioma derivado de un título de oferta. Ver el docstring del módulo."""

    __tablename__ = "job_title_languages"

    title: Mapped[str] = mapped_column(String(TITLE_MAX_LEN), primary_key=True)
    # NULL = pendiente; '' = resuelto como DESCONOCIDO; 'de'/'fr'/... = resuelto.
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
