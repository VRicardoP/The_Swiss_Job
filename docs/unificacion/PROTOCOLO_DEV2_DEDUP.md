# Protocolo development-2 (TRACK R.2b) — fijado 2026-08-24 ANTES de muestrear

Objetivo: dataset de TUNING para el generador de candidatos cross-portal.
NO es oráculo del gate (el gate solo puntúa el holdout congelado).

- Corpus: BD LOCAL de desarrollo (5.039 vacantes vigentes, 25 fuentes) —
  corpus distinto del NAS a propósito: ni un byte del holdout.
- Muestreo MECÁNICO con setseed(0.240824), sin selección manual:
  - D2-A (30): cross-fuente, trgm(título) >= 0.5, CON solape de tokens
    significativos de empresa (norm.: lower, sin puntuación, sin sufijos
    legales ag/gmbh/mbh/est/ltd/inc/sa/kg/co; token len >= 3)
  - D2-B (15): cross-fuente, trgm(título) >= 0.5, SIN solape de empresa
  - D2-C (15): cross-fuente aleatorios (control)
- Exclusión mecánica: pares cuyo par de títulos normalizados (no ordenado)
  coincida con un par del holdout.
- ETIQUETADO: el AGENTE, aplicando el criterio RATIFICADO por el propietario
  (2026-08-24, §16.1 del estado): variantes de redacción de empresa/título =
  misma oferta; mismo texto en ciudades distintas = ofertas distintas
  (multi-ciudad). Cada etiqueta lleva razón de una línea. Conflicto declarado:
  el agente ha visto el holdout; mitigación = muestreo mecánico + criterio
  escrito + el gate solo puntúa el holdout.
- Uso: elegir el punto de operación del generador (umbral trgm, regla de
  empresa, guard de ubicación) SOLO con las métricas de este set.

## Enmienda v2 (2026-08-24, revisión Track R P1-4)

El muestreo original (dev2_sample.sql + paso python no versionado) no era
verificable. Sustituido por `dev2_sample_v2.sql`: consulta ÚNICA (tokenizer
pre-registrado en SQL, D2A/D2B por estratos separados, exclusión del holdout
embebida por pares de títulos normalizados, 30/15/15 exactos). Huella del
snapshot de corpus local: `5039:9eacf8942057dfc7346b203d5de837a4`. Diferencias re-etiquetadas por el mismo
criterio SIN ajustar la muestra (18 pares comunes conservan su etiqueta v1;
42 nuevos): v2 = 11 dup / 49 distinct. Punto de operación re-medido
(aprox. python sin tope de frecuencia = peor caso): TP 11/11, FP 3
(D2A-03 tokens genéricos de escuela — el tope por empresas del SQL real lo
filtra; D2A-25 lemon.io Project vs Product a trgm 0.70; D2A-30 multi-ciudad
mismo cantón — la componente compartida es el cantón). Se ACEPTA T=0.65
priorizando el recall que el gate suspende; los dos modos de FP quedan
declarados para la re-revisión.

## Nota post-re-confirmación (2026-08-24)

P1-B cerrado: el componente coincidente debe ser el PRIMERO (ciudad) en al
menos un lado — «Berlin, Germany» ≁ «Munich, Germany», «Schänis, St. Gallen»
≁ «Flums, St. Gallen» (el FP D2A-30 queda ELIMINADO), «Bulgaria, Romania» ~
«Greece, Bulgaria» se conserva (ratificado). Punto de operación re-medido en
dev-2 v2: TP 11/11, FP 2 (D2A-03 tokens genéricos de escuela — lo filtra el
tope por empresas del SQL real; D2A-25 lemon.io Project/Product a trgm 0.70,
ACEPTADO temporalmente en sombra por el revisor). Estrato positivo
independiente: SEGUIMIENTO obligatorio antes de auto-merge o nuevo tuning
(pronunciamiento del revisor).

## Development-3 (2026-08-24, FASE 2 — brazo intra + remoto comodín)

Muestreo mecánico local (`dev3_sample.sql`, setseed 0.250824): 18 pares
INTRA (misma fuente, misma empresa, hash distinto, trgm normalizado
0.72–1.0) + 12 REMOTO-vs-concreto cross. Etiquetas del agente por criterio
ratificado (`dev3_etiquetado.csv`). Resultado:
- INTRA: 2 dup / 16 distinct. El brazo intra (trgm_norm >= 0.90 + ubicación
  compatible) da TP 2/2, FP 0 — los 16 distinct caen por el umbral o por la
  regla multi-ciudad (11 taxtalente con la ciudad en el propio título).
  Limitación declarada: la muestra no contiene un distinct de MISMA ciudad
  con trgm >= 0.9.
- REMOTO: 0 dup / 12 distinct. El comodín unilateral no añade NINGÚN FP en
  la muestra (el único caso de misma empresa, rimini street, cae por trgm
  0.567 < 0.65). Su beneficio se apoya en dev-2 (D2A-45) — CAMBIA el caso
  «remote↔Berlin=no» fijado por el revisor en su ronda 1: expuesto a su
  ratificación.
- Normalización de títulos: convierte en idénticos los pares multi-ciudad
  con la ciudad entre paréntesis (E-INTRA-03) — la regla de ubicación es la
  que separa; validado en la muestra.

## Development-3 v2 (2026-08-24, revisión FASE 2 P1-4 — predicado PRODUCTIVO)

`dev3_sample_v2.sql`: firma tokenizada real, título normalizado real, regla de
ubicación real; exclusión del holdout por par no ordenado; huella de corpus
5039:9eacf894…; UUIDs en la salida. 30 pares (IPOS 10 / IHARD 8 / XPOS 10 /
XVETO 2 — corrección ronda 2: son 30, no 28 — déficit XVETO registrado). Etiquetas del agente
(`dev3_v2_etiquetado.csv`). MEDICIÓN POR VÍA (umbrales SIN mover tras ver
resultados — se reporta tal cual):
- INTRA @0.90: TP 9, FP 1, FN 1 en banda dura. El FP (IPOS-03) revela que el
  allowlist de PORCENTAJES borra semántica real (55% vs 27% = dos plazas); el
  FN (IHARD-02) es un prefijo "Eks:" a 0.895. Ambos expuestos al revisor.
- CROSS @0.65: TP 9, FP 1 (la clase lemon.io Project/Product, ya aceptada en
  sombra). Veto de ubicación: 2/2 correctos.

## Development-3 v3 (2026-08-24, ronda 2 P1-3 — las TRES vías + trazabilidad)

`dev3_sample_v3.sql`: unión intra / cross-firma / cross-token (con frec y
maxfreq=50 reales) con precedencia determinista; salida VERSIONADA con via,
UUIDs y campos (`dev3_v3_salida.csv`); etiquetas CON va/vb
(`dev3_v3_etiquetado.csv`). Huella 5039:9eacf894…. 34 pares (TPOS con
déficit 6/8, VETO 6). MEDICIÓN POR VÍA (umbrales sin mover):
- intra@0.90: TP 8, FP 0. VERIFICADO el cierre de la ronda: ^eks: captura
  el ex-FN (IPOS-03) y el % conservado convierte el ex-FP de pensums en TN
  (IHARD-01). FN en banda dura: 3 (reformulaciones a 0.77–0.86; el umbral
  no se toca por pronunciamiento previo).
- cross-firma@0.65: TP 7, FP 1 (lemon.io, clase aceptada en sombra).
- cross-token@0.65: TP 5, FP 1 — **HALLAZGO NUEVO expuesto: TPOS-06, dos
  empresas DISTINTAS unidas por el token genérico 'health'** (clipboard
  health vs tandem health). Decisión al revisor: ¿maxfreq más bajo, filtro
  de tokens por longitud/IDF, o aceptar?
- veto ubicación: 6/6 correctos (multi-ciudad).
