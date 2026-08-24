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

## v2.1 — 2026-08-24 (ayuda de idioma, pre-etiquetado)

El propietario no lee alemán: hoja de APOYO en español (traducción de títulos
y ubicaciones, autoría del agente conflictuado — registrado; los originales de
la hoja v2 mandan y ningún par lleva recomendación de juicio):

- `2af61a5e84771c19175cf1dc747d2514c6011d3d13668b494377919d3eb29bb1`  HOJA_HOLDOUT_DEDUP_ES.md

## v3 — 2026-08-24 (development-2: tuning del TRACK R, post-freeze)

Set de TUNING (jamás gate): muestreo mecánico local (PROTOCOLO_DEV2_DEDUP.md,
setseed 0.240824) + etiquetas del AGENTE por criterio ratificado del
propietario (ESTADO §16.1) con razón por par. 60 pares: 9 dup / 51 distinct.
Punto de operación elegido SOLO con estas mediciones: léxico = token de
empresa + trgm >= 0.65 + ubicación v2 (9/9, 0 FP); el ANN>=0.95 daba 0/9.

- `e282afa2c02619c750a03bdcab491a504b627173c69492e7045d3580a92ebe8b`  holdout_artefactos_2026-08-23/dev2_etiquetado.csv
- `c36106a89e188e2a9703a6f01e86215c7b222b66788c31ad916a91bfa8c76577`  holdout_artefactos_2026-08-23/dev2_sample.sql
- `780f6bdfcadd99586dd66cecb02b56a48eed23aa85d4a2809abd93cbd4422c91`  PROTOCOLO_DEV2_DEDUP.md

## v3.1 — 2026-08-24 (dev-2 REPRODUCIBLE, revisión Track R P1-4)

- `ad77fa2fcabfee3fc59f0d11812544be0ec3a65157fc56a49cc9660d9335d04e`  holdout_artefactos_2026-08-23/dev2_sample_v2.sql
- `7ebc2c8d168f5e39322584edd0e69d47f6286a751a8345f98602aef2768a4d59`  holdout_artefactos_2026-08-23/dev2_etiquetado_v2.csv
- `d7c99fc10fdbd0865b7c3487d2975d3dcdf901cf13808d29be1e60472ebd5a88`  PROTOCOLO_DEV2_DEDUP.md (enmienda v2)

## v4 — 2026-08-24 (FASE 2: development-3, intra + remoto)

- `1b38c04925a029c2ad3211b069056e29aac7c4c7dc71f1c4306d75c1f8743a6d`  holdout_artefactos_2026-08-23/dev3_sample.sql
- `deb5e8a2956d5c606a3befc2b7051d38ca6b50ea8ed5ffa5c1c21ef0c1035a67`  holdout_artefactos_2026-08-23/dev3_etiquetado.csv
- `38d25aedb6e5e1dbb07b989513b4ce490ed8df9cf2c79ee318506d898be9444b`  PROTOCOLO_DEV2_DEDUP.md (nota dev-3)

## v5 — 2026-08-24 (fronteras conservadoras + dev-3 v2 productivo)

- `8ace5040de0973b746c9715581ce89fcb56df7cc6e77069b9695458a3e0e5af7`  holdout_artefactos_2026-08-23/dev3_sample_v2.sql
- `cd7023c4f2902b6e78b545bcb528d1fbc421fb906671d5ecf8af4538fa3a2c41`  holdout_artefactos_2026-08-23/dev3_v2_etiquetado.csv
- `6df295411da0baa23b162de0556425f9dd88863c65233a10e6ea358a80ac841a`  PROTOCOLO_DEV2_DEDUP.md

## v6 — 2026-08-24 (ronda 2: allowlist sin %, ^eks:, cplusplus/csharp, dev-3 v3)

- `f35d2f1aa0b6eacb07db683302e0078770413fd966eece431faef0f69e87f259`  holdout_artefactos_2026-08-23/dev3_sample_v3.sql
- `52a5dc5e137d163add44fcde65a6bf9d910074400f957608c8c2745aa50b7d43`  holdout_artefactos_2026-08-23/dev3_v3_salida.csv
- `f8b739dcba44cf6b1e3cc51f1c67aef9be9c08a4bf97ae16ef25683c79bb8814`  holdout_artefactos_2026-08-23/dev3_v3_etiquetado.csv
- `63f07060592432d8e4dc1255c72acc5cb704add7100004c846665b2aacd03e02`  PROTOCOLO_DEV2_DEDUP.md
