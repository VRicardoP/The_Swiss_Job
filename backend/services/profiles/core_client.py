"""Transporte HTTP hacia el /v1 del núcleo para el perfil: una sola cosa.

Las ediciones de perfil son LOCALES y la entrega al núcleo va por instantánea
durable (`profile_sync`, `exclusions_sync`, `profile_erasure`): lo único que
esos tres necesitan de aquí es el cliente httpx ya autenticado.

DT-148 B3 (2026-10-21): este módulo tenía además `CoreProfile`, un adaptador de
LECTURA del perfil contra el núcleo (con `CoreProfileView`,
`_ProjectedContentDTO`, `_view` y una caché de ETag acotada). No lo usaba
producción: `profiles/seam.resolve_profiles` devuelve SIEMPRE `LocalProfile`,
así que el adaptador sólo lo instanciaban sus propios tests. Con la caché se
fue `clear_profile_cache`, cuyo único llamador —`profile_erasure.
clear_erased_caches`— existía para invalidarla: una función que vacía un diccionario
que nadie escribe no protege ningún dato, sólo parece hacerlo.
"""

import httpx

from config import settings


def default_client_factory() -> httpx.AsyncClient:
    """Cliente httpx contra el /v1 del core por la red interna de compose
    (plan §21: puerto dedicado, solo red interna, nunca ngrok)."""
    return httpx.AsyncClient(
        base_url=settings.CORE_API_BASE_URL,
        headers={"Authorization": f"Bearer {settings.CORE_CONSUMER_KEY}"},
        timeout=settings.CORE_HTTP_TIMEOUT_SECONDS,
    )
