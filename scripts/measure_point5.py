#!/usr/bin/env python3
"""Punto 5 — matriz de aceptación: mide los recorridos prioritarios del BFF.

Se ejecuta DENTRO del contenedor `backend` (necesita `config` y `core.security`
para firmar el token, y habla con el servidor por loopback).

El acta del punto 5 (§10.2) exige tres cosas que este script hace explícitas:

- **≥100 muestras** por recorrido. Con n=20 el p95 ES el máximo y un solo pico
  decide el veredicto.
- **Frío y caliente separados.** El frío no es un adorno: la caché del recorrido
  del feed vive EN PROCESO, así que sólo es frío la primera petición tras
  reiniciar el BFF. Por eso `--frio` sólo tiene sentido justo después de un
  `docker compose restart backend`; medir sólo en caliente esconde el coste real
  de la primera carga.
- **Veredicto por escenario**, contra el presupuesto predeclarado y no contra
  una mejora porcentual.

Uso:
  python scripts/measure_point5.py --usuario <uuid> --muestras 100
  python scripts/measure_point5.py --usuario <uuid> --frio     # tras reiniciar
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid

import httpx

BASE = "http://localhost:8000/api/v1"

# Presupuesto PREDECLARADO (no se renegocia al ver el resultado).
P95_LECTURA_HABITUAL = 2.0
UTIL_EN_PANTALLA = 3.0

# `limit=3000` es lo que pide MatchPage en CADA entrada a la pantalla principal:
# es la lectura habitual, no una exportación. Bajarlo rompería las categorías y
# sus contadores, la Watchlist, el top score y el recuento de matches >= 70.
RECORRIDOS = {
    "pantalla-principal": {
        "ruta": "/match/results",
        "params": {"limit": 3000, "offset": 0, "translate": "false"},
        "presupuesto": UTIL_EN_PANTALLA,
        "nota": "lectura habitual: el lote completo alimenta 5 agregados",
    },
    "pagina-20": {
        "ruta": "/match/results",
        "params": {"limit": 20, "offset": 0, "translate": "false"},
        "presupuesto": P95_LECTURA_HABITUAL,
        "nota": "una página de tarjetas",
    },
    "guardados": {
        "ruta": "/match/saved",
        "params": {"limit": 50},
        "presupuesto": P95_LECTURA_HABITUAL,
        "nota": "estado del usuario, no el feed",
    },
    "catalogo": {
        "ruta": "/jobs/search",
        "params": {"limit": 20},
        "presupuesto": P95_LECTURA_HABITUAL,
        "nota": "búsqueda del catálogo",
    },
}


def percentil(ordenados: list[float], q: float) -> float:
    """Percentil por rango más cercano; con n>=100 no hace falta interpolar."""
    if not ordenados:
        return float("nan")
    idx = min(len(ordenados) - 1, max(0, round(q * (len(ordenados) - 1))))
    return ordenados[idx]


async def _una(cliente: httpx.AsyncClient, recorrido: dict, cabeceras: dict):
    inicio = time.perf_counter()
    respuesta = await cliente.get(
        recorrido["ruta"], params=recorrido["params"], headers=cabeceras
    )
    return time.perf_counter() - inicio, respuesta


async def medir(nombre: str, muestras: int, cabeceras: dict, calentar: bool) -> dict:
    recorrido = RECORRIDOS[nombre]
    tiempos: list[float] = []
    estados: set[int] = set()
    elementos = None
    async with httpx.AsyncClient(base_url=BASE, timeout=600) as cliente:
        if calentar:
            await _una(cliente, recorrido, cabeceras)
        for _ in range(muestras):
            dt, respuesta = await _una(cliente, recorrido, cabeceras)
            tiempos.append(dt)
            estados.add(respuesta.status_code)
            if elementos is None and respuesta.status_code == 200:
                cuerpo = respuesta.json()
                elementos = len(
                    cuerpo.get("data", cuerpo if isinstance(cuerpo, list) else [])
                )
    tiempos.sort()
    return {
        "recorrido": nombre,
        "nota": recorrido["nota"],
        "n": len(tiempos),
        "estados": sorted(estados),
        "elementos": elementos,
        "p50": round(statistics.median(tiempos), 3),
        "p95": round(percentil(tiempos, 0.95), 3),
        "p99": round(percentil(tiempos, 0.99), 3),
        "min": round(tiempos[0], 3),
        "max": round(tiempos[-1], 3),
        "presupuesto": recorrido["presupuesto"],
        "cumple": tiempos and percentil(tiempos, 0.95) <= recorrido["presupuesto"],
    }


async def principal() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--usuario", required=True)
    parser.add_argument("--muestras", type=int, default=100)
    parser.add_argument(
        "--frio",
        action="store_true",
        help="una sola muestra, sin calentar: úsalo JUSTO tras reiniciar el BFF",
    )
    parser.add_argument("--recorridos", default=",".join(RECORRIDOS))
    parser.add_argument("--json", help="fichero donde volcar el resultado")
    args = parser.parse_args()

    from core.security import create_access_token

    cabeceras = {
        "Authorization": "Bearer " + create_access_token(uuid.UUID(args.usuario))
    }
    nombres = [n for n in args.recorridos.split(",") if n in RECORRIDOS]
    muestras = 1 if args.frio else args.muestras

    filas = []
    for nombre in nombres:
        fila = await medir(nombre, muestras, cabeceras, calentar=not args.frio)
        fila["fase"] = "frio" if args.frio else "caliente"
        filas.append(fila)
        veredicto = "CUMPLE" if fila["cumple"] else "NO CUMPLE"
        print(
            f"  {fila['fase']:<8} {nombre:<20} n={fila['n']:<4} "
            f"p50={fila['p50']:>7.3f} p95={fila['p95']:>7.3f} "
            f"max={fila['max']:>7.3f} (<= {fila['presupuesto']}s) {veredicto}"
            f"  [{fila['estados']}, {fila['elementos']} elementos]"
        )
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(filas, f, indent=2, ensure_ascii=False)
    return 0 if all(f["cumple"] for f in filas) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
