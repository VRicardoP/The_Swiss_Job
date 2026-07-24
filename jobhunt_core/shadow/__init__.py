"""Fase B (Sombra) — captura CDC, set etiquetado, seeds y medición.

El legacy sigue de escritor autoritativo; el core recibe sus deltas por el
slot lógico (capture.py, B-01), los procesa con SU pipeline y se mide contra
el set etiquetado (labels.py, B-03) — el sistema viejo NO es el oráculo.
"""
