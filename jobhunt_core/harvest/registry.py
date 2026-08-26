"""Alta en caliente de handlers de identidad/normalización, por NAMESPACE.

El registro de `harvest.normalize`/`harvest.identity` es memoria POR PROCESO:
lo puebla cada fuente con su propia alta idempotente (`register_handlers()` de
arbeitnow y de portfolio-import, `legacy_shadow.ensure_registered()` por fuente
sombra). Un proceso que no haya llegado nunca a esa fuente no sabe
normalizarla, y `normalize_offer` devuelve None por una causa que NO es
«contenido no normalizable».

Confundir ambas causas anula la canónica de una vacante VIVA (G3-A-P3-2), así
que aquí vive el ÚNICO sitio que sabe dar de alta el handler de una fuente
CUALQUIERA. Se importa siempre en LOCAL (el sink es dependencia de
`providers/` y de `import_portfolio`: importarlo arriba cerraría el ciclo).
"""

import logging

from jobhunt_core.harvest import normalize

logger = logging.getLogger(__name__)


def _altas_exact_match() -> dict:
    """{nombre de fuente: alta idempotente}. Enumeradas, no adivinadas: son
    las únicas fuentes exact-match del core (el resto son `legacy:*`, que
    comparten el handler genérico de la sombra). Imports LOCALES."""
    from jobhunt_core import import_portfolio
    from jobhunt_core.harvest.providers import arbeitnow

    return {
        arbeitnow.SOURCE_NAME: arbeitnow.register_handlers,
        import_portfolio.PORTFOLIO_IMPORT_SOURCE: import_portfolio.register_handlers,
    }


def ensure_handler(source_name: str) -> bool:
    """Deja registrado en ESTE proceso el handler de `source_name` si se sabe
    cómo; devuelve si el proceso puede ya normalizar esa fuente.

    G4-P2-4: la versión anterior vivía en el sink y solo cubría `legacy:*`, así
    que `portfolio-import` —la fuente de las vacantes a las que el usuario se
    apuntó o marcó— seguía sin handler en el worker (ningún módulo del
    `celery_app.conf.include` la alcanza) y la canónica de una vacante VIVA se
    anulaba: fuera de /v1, del matching y del dedup. La condición no depende ya
    del PREFIJO: cada namespace se resuelve por su propia vía de alta."""
    if normalize.has_normalizer(source_name):
        return True
    # Import local: `providers` importa el sink, que importa esto.
    from jobhunt_core.harvest.providers import legacy_shadow

    if source_name.startswith(legacy_shadow.LEGACY_PREFIX):
        legacy_shadow.ensure_registered(source_name)  # handler GENÉRICO sombra
        return True
    alta = _altas_exact_match().get(source_name)
    if alta is None:
        return False
    try:
        alta()
    except Exception:
        # G5-N-3: el alta importa un módulo (hoy `import_portfolio`) DENTRO de
        # la transacción de reparación del sink. Donde el camino anterior solo
        # LOGUEABA, un ImportError o un efecto de import abortaría la cosecha
        # entera. Fail-soft: sin normalizador la canónica queda NULL, que es el
        # contrato ya escrito para este caso (A-06), no una excepción que
        # tumbe el run.
        logger.exception(
            "registry: el alta en caliente del handler de %r ha fallado — "
            "la fuente queda sin normalizador en este proceso", source_name
        )
        return False
    return normalize.has_normalizer(source_name)
