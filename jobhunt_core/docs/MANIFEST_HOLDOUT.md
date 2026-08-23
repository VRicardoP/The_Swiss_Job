# MANIFEST — pre-registro del holdout dedup (auditoría Nº2, IMPORTANTE 2)

> v1 (2026-08-23): protocolo (con enmiendas 1ª y 2ª), contrato enmendado,
> hoja ciega SIN enriquecer aún y artefactos del muestreo. Tras enriquecer
> la hoja (URL + texto completo, MISMA muestra) se añadirá la v2 con su
> hash; el freeze en BD (`labeled_dedup_cohorts.manifest`) llevará estos
> mismos SHA-256. Cualquier cambio posterior a un fichero listado es una
> NUEVA versión que debe explicarse — el hash lo delata.

Rutas relativas a `/home/lothar/Public/` (revisión solo-código, MENOR 1:
los basenames sin ruta eran ambiguos e irreproducibles literalmente).

## v1 — SHA-256

- `eee2f6beaea2c233b51156be1788784855a2f2965a3846e1a504bc430277ccd7`  PROTOCOLO_HOLDOUT_DEDUP.md
- `dc582209c336ddea08165504160bbb94e80f816f6b73bcebd3465f083a7c862a`  CONTRATOS_FASE_B.md
- `6d18ad80fadbf5179d0dada7b8aed20ca80cb8f2812379b838527429c4d2b228`  HOJA_HOLDOUT_DEDUP.md
- `6dd645b8dd2e3087a92629be2df07f8c966281d0f8994ad75fa45c8c98a8de03`  holdout_artefactos_2026-08-23/holdout_sample.sql
- `33059493bcab13b8f5701c24c04fd1e04405b6c65bbdb88e9accfbfee6dd1ec1`  holdout_artefactos_2026-08-23/holdout_pares.csv
- `64e54800f507aaeefa44a55a7f92e3991c77658f81e2b3e7a1b8f76d3192aa41`  holdout_artefactos_2026-08-23/holdout_map.csv
- `21e82395b6a882b0ac2b62d39a85616d1d0c55a16ca05ea54383c0708483081a`  holdout_artefactos_2026-08-23/incoherentes.sql
- `dcc08fe295d4f490084eb7951e74f054d9ffa51fa462a3a54f217fee6ef7e4c5`  holdout_artefactos_2026-08-23/incoherentes17.txt
- `f49fa1bbc2cdc431f6643d13debefc0d59c7f1f9a1293fae0d5306add4c2a83e`  holdout_artefactos_2026-08-23/incoherentes_map.csv
- `01e11efe48ee0c7ce4aacb781e30429d47859730f30c663a5e6df4471562fb13`  holdout_artefactos_2026-08-23/solape.sql

## v1.1 — 2026-08-23 (cambio explicado)

`PROTOCOLO_HOLDOUT_DEDUP.md` actualizado ANTES del etiquetado con el aviso
del margen de `unsure` (máx. 8 de 58 — señalado por la revisión solo-código
como aviso al operador). Nuevo hash del protocolo; el resto no cambia:

- `c8f6f2ea0470d6c0250f56a1bbc1bdffe903a6f03d26617381a29e83abce606e`  PROTOCOLO_HOLDOUT_DEDUP.md

## v2 — 2026-08-23 (hoja ENRIQUECIDA, pre-etiquetado)

La hoja incorpora URL y descripción completa de ambos lados (IMPORTANTE 3
de la auditoría Nº2) SIN re-muestrear: mismos UUID que la v1
(`holdout_map.csv`, hash intacto). Extraída del NAS tras el deploy de
`7a5cf6f` (core0025+core0026 aplicadas). ESTOS son los hashes que llevará
el manifest del freeze en BD (`labeled_dedup_cohorts.manifest`):

- `01977dc77ffb41cff76bd0b754d1ff64311cafc3da250e72b9acadcd3093c0a4`  HOJA_HOLDOUT_DEDUP.md (v2 enriquecida)
- `ca5ed2de1a360fb1d2c2bcf9bf72cc829da71fbc94da254e33d9b89f897c205c`  holdout_artefactos_2026-08-23/holdout_enriquecer.sql
- `a70f7a1866008a909a1550c9425aed3704aceb48c6ba2c9182ecd165dede9175`  holdout_artefactos_2026-08-23/hoja_enriquecida_raw.txt
