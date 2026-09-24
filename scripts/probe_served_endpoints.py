"""Sonda de los endpoints SERVIDOS, con afirmaciones que pueden fallar de verdad.

Historia de por qué es así, para que nadie la ablande:

1. El canario original leía la clave `jobs` donde la respuesta trae `data`:
   habría aprobado un 200 indebidamente vacío.
2. Su sustituta tenía cinco controles negativos… que fallaban TODOS por la clave
   equivocada, antes de llegar a la guarda que decían probar. Una mutación lo
   demostró: quitando las guardas de cardinalidad y de total, `self_test()`
   seguía verde.

De ahí las dos reglas de esta versión:

- **Cada rechazo lleva un MOTIVO nombrado** (`ProbeFailure.reason`) y cada
  control negativo exige ESE motivo. Borrar una guarda deja de producir su
  motivo ⇒ rompe su propio control, que es lo que se le pedía.
- **Cada control negativo parte de un cuerpo VÁLIDO y rompe una sola
  propiedad.** Hay además controles POSITIVOS: el cuerpo válido debe pasar, o
  las guardas serían simplemente demasiado estrictas y no probarían nada.

Sólo lectura: firma un token efímero en memoria para un usuario EXISTENTE, hace
GETs, nunca imprime el token ni datos personales, no crea ni escribe nada.
"""

import asyncio
import json
import statistics
import sys
import time

import httpx
from sqlalchemy import text

from database import async_session


def pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((p / 100) * len(xs) + 0.5)) - 1))
    return xs[k]


class ProbeFailure(AssertionError):
    """Rechazo con motivo nombrado.

    El motivo NO es decorativo: es lo que permite que un control negativo
    pruebe su condición y no otra. Sin él, cualquier excepción anterior tapa
    la guarda que se quería ejercitar."""

    def __init__(self, reason: str, message: str):
        super().__init__(f"[{reason}] {message}")
        self.reason = reason


def _page_items(body, *, kind):
    """Parte común: el cuerpo es un objeto y `data` es una lista."""
    if not isinstance(body, dict):
        raise ProbeFailure("not_object", f"{kind}: el cuerpo no es un objeto")
    if "data" not in body:
        raise ProbeFailure(
            "no_data_key", f"{kind}: sin clave 'data'; hay {sorted(body)[:6]}"
        )
    data = body["data"]
    if not isinstance(data, list):
        raise ProbeFailure("data_not_list", f"{kind}: 'data' no es una lista")
    return data


def _identities(items, *, key, kind):
    """Presencia y unicidad de la identidad. Lo que la docstring prometía y
    no comprobaba: `data=[{}]*20` pasaba como página buena."""
    ids = []
    for item in items:
        value = item.get(key) if isinstance(item, dict) else None
        if not value or not isinstance(value, str):
            raise ProbeFailure(
                "item_without_identity", f"{kind}: un elemento no trae '{key}'"
            )
        ids.append(value)
    if len(set(ids)) != len(ids):
        raise ProbeFailure(
            "duplicate_identities",
            f"{kind}: {len(ids) - len(set(ids))} identidades repetidas",
        )
    return ids


def check_catalog(body, *, min_items):
    """La página de catálogo trae `data` con ofertas reales y un total sano."""
    data = _page_items(body, kind="catálogo")
    if len(data) < min_items:
        raise ProbeFailure(
            "too_few_items",
            f"catálogo: se esperaban >= {min_items} ofertas, hay {len(data)}",
        )
    for offer in data:
        if not isinstance(offer, dict) or not offer.get("title"):
            raise ProbeFailure(
                "item_without_title", "catálogo: una oferta no trae título"
            )
    _identities(data, key="hash", kind="catálogo")
    total = body.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        raise ProbeFailure(
            "bad_total", f"catálogo: total no es un entero positivo: {total!r}"
        )
    if total < len(data):
        raise ProbeFailure(
            "total_below_page",
            f"catálogo: total {total} menor que la página que transporta",
        )
    return len(data), total


def check_matches(body, *, expected_len, expected_total=None):
    """La página del feed trae sus elementos, un total coherente e ids únicos."""
    items = _page_items(body, kind="feed")
    if len(items) != expected_len:
        raise ProbeFailure(
            "wrong_cardinality",
            f"feed: se esperaban {expected_len} elementos, hay {len(items)}",
        )
    _identities(items, key="job_hash", kind="feed")
    total = body.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or total < len(items):
        raise ProbeFailure(
            "bad_total", f"feed: total {total!r} incoherente con {len(items)} elementos"
        )
    if expected_total is not None and total != expected_total:
        raise ProbeFailure(
            "total_mismatch", f"feed: total {total} != esperado {expected_total}"
        )
    return len(items), total


def fingerprint(body):
    """Identidad + orden + score de una respuesta, para comparar repeticiones.

    Un endpoint que devuelve la misma CANTIDAD con otro contenido pasaría los
    controles de arriba; esto es lo que hace refutable la equivalencia."""
    return [
        (i.get("job_hash"), round(float(i.get("score_final") or 0.0), 6))
        for i in body["data"]
    ]


# --- Cuerpos VÁLIDOS de referencia. Cada control negativo nace de una copia de
# uno de ellos con UNA sola propiedad rota.
_CATALOGO_OK = {
    "data": [{"hash": f"h{i}", "title": f"t{i}"} for i in range(20)],
    "total": 46489,
}
_FEED_OK = {
    "data": [{"job_hash": f"j{i}", "score_final": 50.0 + i} for i in range(20)],
    "total": 1800,
}


def _roto(base, **cambios):
    cuerpo = {k: (list(v) if isinstance(v, list) else v) for k, v in base.items()}
    cuerpo.update(cambios)
    return cuerpo


def self_test():
    """Controles POSITIVOS y NEGATIVOS, cada negativo con su motivo exigido."""
    # Positivos: el cuerpo válido pasa. Sin esto, una guarda de más convertiría
    # la sonda en un rechazo universal que también "detecta" todos los negativos.
    n_pos, tot_pos = check_catalog(_CATALOGO_OK, min_items=20)
    assert (n_pos, tot_pos) == (20, 46489), "el catálogo válido no pasó"
    n_feed, tot_feed = check_matches(_FEED_OK, expected_len=20, expected_total=1800)
    assert (n_feed, tot_feed) == (20, 1800), "el feed válido no pasó"

    negativos = [
        # (nombre, motivo EXIGIDO, cuerpo con UNA propiedad rota)
        (
            "catálogo: el cuerpo no es un objeto",
            "not_object",
            lambda: check_catalog([{"hash": "h", "title": "t"}], min_items=1),
        ),
        (
            "catálogo: 'data' no es una lista",
            "data_not_list",
            lambda: check_catalog(_roto(_CATALOGO_OK, data={"0": "h0"}), min_items=20),
        ),
        (
            "catálogo: oferta sin título",
            "item_without_title",
            lambda: check_catalog(
                _roto(_CATALOGO_OK, data=[{"hash": f"h{i}"} for i in range(20)]),
                min_items=20,
            ),
        ),
        (
            "catálogo: total no positivo",
            "bad_total",
            lambda: check_catalog(_roto(_CATALOGO_OK, total=0), min_items=20),
        ),
        (
            "catálogo: 200 vacío indebido",
            "too_few_items",
            lambda: check_catalog(_roto(_CATALOGO_OK, data=[]), min_items=20),
        ),
        (
            "catálogo: clave equivocada",
            "no_data_key",
            lambda: check_catalog(
                {"jobs": _CATALOGO_OK["data"], "total": 46489}, min_items=20
            ),
        ),
        (
            "catálogo: oferta sin identidad",
            "item_without_identity",
            lambda: check_catalog(
                _roto(_CATALOGO_OK, data=[{"title": "t"}] * 20), min_items=20
            ),
        ),
        (
            "catálogo: total menor que la página",
            "total_below_page",
            lambda: check_catalog(_roto(_CATALOGO_OK, total=3), min_items=20),
        ),
        (
            "feed: clave equivocada",
            "no_data_key",
            lambda: check_matches(
                {"results": _FEED_OK["data"], "total": 1800}, expected_len=20
            ),
        ),
        (
            "feed: cardinalidad distinta",
            "wrong_cardinality",
            lambda: check_matches(
                _roto(_FEED_OK, data=_FEED_OK["data"][:1]), expected_len=20
            ),
        ),
        (
            "feed: total incoherente",
            "bad_total",
            lambda: check_matches(_roto(_FEED_OK, total=3), expected_len=20),
        ),
        (
            "feed: total distinto del esperado",
            "total_mismatch",
            lambda: check_matches(_FEED_OK, expected_len=20, expected_total=1799),
        ),
        (
            "feed: elemento sin identidad",
            "item_without_identity",
            lambda: check_matches(_roto(_FEED_OK, data=[{}] * 20), expected_len=20),
        ),
        (
            "feed: identidades repetidas",
            "duplicate_identities",
            lambda: check_matches(
                _roto(_FEED_OK, data=[{"job_hash": "j0"}] * 20), expected_len=20
            ),
        ),
    ]
    for nombre, motivo, fn in negativos:
        try:
            fn()
        except ProbeFailure as exc:
            if exc.reason != motivo:
                raise SystemExit(
                    f"CONTROL NEGATIVO POR LA CAUSA EQUIVOCADA: {nombre}; "
                    f"esperado '{motivo}', obtenido '{exc.reason}'"
                )
            continue
        raise SystemExit(f"CONTROL NEGATIVO NO DETECTADO: {nombre}")
    print(
        json.dumps(
            {
                "controles_positivos": 2,
                "controles_negativos": len(negativos),
                "cada_uno_por_su_motivo": True,
            }
        ),
        flush=True,
    )


async def token_for(user_id):
    """Bearer efímero para un usuario EXISTENTE. Ni se imprime ni se guarda."""
    from core.security import create_access_token

    return create_access_token(user_id)


async def main():
    self_test()
    async with async_session() as db:
        user = (
            await db.execute(
                text("SELECT user_id FROM jobhunt_profile_map ORDER BY user_id LIMIT 1")
            )
        ).scalar()
    bearer = await token_for(user)
    headers = {"Authorization": f"Bearer {bearer}"}
    base = "http://127.0.0.1:8000/api/v1"
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20

    async with httpx.AsyncClient(timeout=180) as c:
        # --- catálogo, sin auth
        # La PRIMERA peticion de cada ruta tras recrear el contenedor es la
        # muestra FRIA y tiene su propio presupuesto (<= 5 s). Se informa
        # aparte: promediarla con las calientes esconderia justo el caso que
        # una cache en proceso no puede cubrir. Tiene que ser LA PRIMERA de
        # verdad — cronometrar la segunda la daria por fria sin serlo.
        t = time.perf_counter()
        r = await c.get(f"{base}/jobs/search", params={"limit": 20})
        frio = time.perf_counter() - t
        if r.status_code != 200:
            raise ProbeFailure("http_status", f"catálogo devolvió {r.status_code}")
        n_items, total = check_catalog(r.json(), min_items=20)
        ms = []
        for _ in range(n):
            t = time.perf_counter()
            rr = await c.get(f"{base}/jobs/search", params={"limit": 20})
            ms.append(time.perf_counter() - t)
            check_catalog(rr.json(), min_items=20)
            await asyncio.sleep(0.3)
        print(
            json.dumps(
                {
                    "endpoint": "/jobs/search",
                    "n": len(ms),
                    "items": n_items,
                    "total": total,
                    "frio_s": round(frio, 3),
                    "p50_s": round(statistics.median(ms), 3),
                    "p95_s": round(pct(ms, 95), 3),
                    "max_s": round(max(ms), 3),
                    "min_s": round(min(ms), 3),
                }
            ),
            flush=True,
        )

        # --- feed servido, autenticado y extremo a extremo.
        # `translate=true` NO se mide aqui: llama a un LLM externo y la
        # predeclaracion §4 prohibe llamar a proveedores facturables en estas
        # pruebas. Ademas la pantalla principal no lo usa —`useMatchResultsPage`
        # es codigo muerto—, asi que no es un recorrido prioritario. Queda
        # EXPRESAMENTE PENDIENTE, con presupuesto propio por decidir.
        #
        # `limit=3000, translate=false` NO es una exportacion excepcional: es lo
        # que pide MatchPage (frontend/src/pages/MatchPage.jsx:40) en cada carga
        # de la pantalla principal, y por eso se mide como lectura ordinaria.
        for limite, translate in ((20, False), (3000, False)):
            params = {"limit": limite, "offset": 0, "translate": str(translate).lower()}
            # Muestra FRIA: la PRIMERA de esta forma tras recrear el
            # contenedor, con la cache del recorrido vacia.
            t = time.perf_counter()
            r = await c.get(f"{base}/match/results", params=params, headers=headers)
            frio = time.perf_counter() - t
            if r.status_code != 200:
                raise ProbeFailure(
                    "http_status",
                    f"match/results devolvió {r.status_code}: {r.text[:200]}",
                )
            primero = r.json()
            esperados = min(limite, len(primero.get("data") or []))
            n_items, total = check_matches(primero, expected_len=esperados)
            huella = fingerprint(primero)
            ms = []
            # El recorrido de 3000 mueve mucha respuesta: menos repeticiones.
            repeticiones = n if limite <= 100 else max(3, n // 4)
            for _ in range(repeticiones):
                t0 = time.perf_counter()
                rr = await c.get(
                    f"{base}/match/results", params=params, headers=headers
                )
                ms.append(time.perf_counter() - t0)
                if rr.status_code != 200:
                    raise ProbeFailure(
                        "http_status", f"match/results devolvió {rr.status_code}"
                    )
                cuerpo = rr.json()
                check_matches(cuerpo, expected_len=esperados, expected_total=total)
                if fingerprint(cuerpo) != huella:
                    raise ProbeFailure(
                        "unstable_page",
                        "feed: misma petición, distintos ids/orden/scores",
                    )
                await asyncio.sleep(0.3)
            print(
                json.dumps(
                    {
                        "endpoint": "/match/results",
                        "limit": limite,
                        "translate": translate,
                        "n": len(ms),
                        "items": n_items,
                        "total": total,
                        "frio_s": round(frio, 3),
                        "p50_s": round(statistics.median(ms), 3),
                        "p95_s": round(pct(ms, 95), 3),
                        "max_s": round(max(ms), 3),
                        "min_s": round(min(ms), 3),
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    asyncio.run(main())
