# Predeclaración — receta de scoring incremental por watermark (P7-b) + orden operativo

Sellada ANTES de implementar, medir o abrir nada. Ratificada por doble
valoración externa (2026-09-04): «D ya + incremental b/a, publicación cerrada
por watermark».

## Receta nueva (versión nueva de política; nada existente se muta)

Backend de scoring del cross-encoder en operación:

1. **Bootstrap frío EXTERNO** (equipo dev u otra máquina con CPU capaz):
   materializa TODOS los scores del corpus elegible a la caché absoluta
   append-only (match_evaluations vía el camino existente), idempotente y
   reanudable. Se usa: (a) en el arranque de la política y (b) ante CADA
   nueva revisión de perfil (una edición de CV invalida todas las consultas
   ⇒ evento de re-scoring externo DECLARADO, jamás cae en el incremental).
2. **Incremental en el NAS por generación (watermark)**:
   - capturar generación G;
   - materializar los misses del conjunto elegible DE G, por lotes
     reanudables e idempotentes, con presupuesto ≤ **60 min/ciclo**;
   - publicar (mover el feed) SOLO cuando los misses de G sean CERO — la
     evaluación canónica corre entonces con caché pura (0 inferencias);
   - ofertas posteriores a G quedan para G+1: fotografía SIEMPRE completa y
     consistente, sin inanición por cosecha continua;
   - un pico que exceda el presupuesto NO publica feed parcial: se sigue
     sirviendo la última fotografía válida con señal VISIBLE de retraso
     (backlog, antigüedad y generación publicada en salud/métricas).

## Gate P7-incremental (medir en el J1800 REAL antes del holdout)

- Peor caso incremental: pico de cosecha, reinicio a mitad (reanudable sin
  duplicar), acumulación de 3 días de misses.
- Presupuesto: ciclo incremental ≤ 60 min con margen declarado; RSS ≤ 6 GB;
  paridad ya probada (6e-06) — se re-verifica sobre una muestra en el import.
- Si NO pasa ⇒ el bloqueo de hardware persiste; sin renegociar tras medir.

## Orden operativo ratificado

1. **Fase D PRIMERO** (baseline cosine, no toca nada congelable): preflight y
   gate por capacidad, backup y rollback ENSAYADOS, migración perfil a
   perfil con comparación legacy/core antes de cada flip, canary con retorno
   inmediato. D completada y estabilizada ANTES del freeze.
2. P7-incremental en el NAS (arriba).
3. Freeze de código+receta+modelo+política+cohortes (cohorte FINAL: todos
   los perfiles de ambos consumers entran en la medición).
4. **Holdout una sola vez** (sigue VIRGEN hasta entonces).
5. Promoción transaccional por la autoridad única + rollback probado.
6. Racha 7×24 h sobre la configuración final; cualquier despliegue que
   afecte modelo, receta, cohortes, corpus o scoring REINICIA la racha.

## Candidata congelada a examen

RankNet n=436, huella `5aa3ee8ef3c3ff33aaa5e7e6a47d756b9607e610e9b399544897692eff81c710`,
dataset `25cdd3a9…`, juicios v10 `4877d4bf…` (dev 0.9872/0.8604, cobertura
100 %, v11=440). Umbral 0.60/0.60 inmutable; fórmula del gate inmutable.
