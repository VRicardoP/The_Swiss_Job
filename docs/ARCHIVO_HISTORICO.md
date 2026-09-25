# Archivo histórico de la unificación

Limpieza del árbol de trabajo: 2026-09-17. Estos documentos se retiraron a la
papelera por ser informes de sesiones, revisiones o avances sustituidos. No se
reescribe su contenido ni se elimina el historial Git. Las referencias a estas
rutas en documentos antiguos apuntan a esas versiones históricas, no al estado
operativo actual.

## Documentación vigente conservada

- Programa de unificación (ESTADO_Y_HOJA_DE_RUTA, DEUDA_TECNICA, PLAN, BACKLOG,
  ADR, contratos, protocolos y runbooks): desde el 2026-09-25 en
  `SwissJob/docs/unificacion/`, ya no en la raíz de Public.
- SwissJob: DEPLOY_NAS, runbooks del core, contratos de documentos/colegios,
  borrado coordinado y actas de despliegue E.12/E.13/E.14/E.15.
- Se conservan instrucciones del repositorio, documentación técnica referenciada
  por código/tests y archivos con cambios del propietario.
- FASE_D_MIGRACION_2026-09-04 se conserva, y el 2026-09-25 se CONFIRMÓ que su
  manifiesto no tiene sustituto: guarda el estado previo de cada fila que tocó la
  migración, y eso no vive en ninguna base (el core tiene el estado posterior y el
  legacy es el origen). Está en `SwissJob/docs/unificacion/`.

## Recuperar o consultar

No hace falta restaurar una rama entera. Para consultar un documento concreto:

```bash
# El repositorio de Public YA NO EXISTE (retirado el 2026-09-25, tras mudar su
# documentación a SwissJob). Su historia completa vive en el bundle:
git clone /home/lothar/Public/tmp/public-git-final-20260925-1151.bundle /tmp/public-hist
git -C /tmp/public-hist show 2bcbcd916ba0d054bf47fa695f5a4e85a811da68:INFORME_SESION_2026-09-04_06.md
git -C /home/lothar/Public/SwissJob show d9c8a0ae60243216e54d3aa7adca5a8142eba2de:docs/CIERRE_LOCAL_E1_2026-09-08.md
```

Para recuperar una copia en el árbol de trabajo, utilizar `git restore --source
<commit> -- <ruta-exacta>` solo después de comprobar que esa ruta no contiene
cambios nuevos. También es posible restaurar desde la papelera.

## Public — commit 2bcbcd916ba0d054bf47fa695f5a4e85a811da68

- `ANEXO_INVENTARIO_FASE0_2026-09-04.md`
- `AUDITORIA_EXTERNA_R8_CUTOVER_2026-08-29.md`
- `AUDITORIA_EXTERNA_R8_VEREDICTO_2026-08-29.md`
- `PROMPT_REVISION_DIRIGIDA_FRONTERA_2026-08-29.md`
- `TRASPASO_2026-08-29.md`
- `INFORME_SESION_2026-09-04_06.md`
- `INFORME_FIXES_REVISION_2026-09-07.md`

## SwissJob — commit d9c8a0ae60243216e54d3aa7adca5a8142eba2de

- `CIERRE_MATCHING_GO_VERIFICADO_2026-09-04.md`
- `docs/CIERRE_LOCAL_E1_2026-09-08.md`
- `docs/CIERRE_LOCAL_E2_2026-09-08.md`
- `docs/CIERRE_LOCAL_E3_2026-09-08.md`
- `docs/AVANCE_E4_2026-09-08.md`
- `docs/AVANCE_E6_2026-09-08.md`
- `docs/AVANCE_E7_Y_MATCHING_OPERATIVO_2026-09-08.md`
- `docs/AVANCE_E8_DOCUMENTOS_Y_FUENTES_2026-09-09.md`
- `docs/AVANCE_E9_EXPORTACION_Y_RECUPERACION_2026-09-09.md`
- `docs/AVANCE_E11_CATALOGO_DOCUMENTOS_2026-09-09.md`
- `docs/REVISION_CIERRES_2026-09-07_CODEX.md`
- `docs/RECONFIRMACION_CIERRES_2026-09-07_CODEX.md`
- `docs/ESTADO_FIXES_UNIFICACION_2026-09-07_CODEX.md`
- `docs/CIERRE_LOCAL_FIXES_2026-09-07_CODEX.md`
- `docs/ENSAYO_RESTORE_CORE_2026-09-07_CODEX.md`

## Paquetes retirados en la limpieza anterior

Las carpetas DEV_ y los tres paquetes HOLDOUT_ETIQUETADO_2026-08-30,
EXAMEN_HOLDOUT_2026-09-06 y holdout_artefactos_2026-08-23 están en el BUNDLE de
Public citado arriba — 304 documentos que no existen en ningún otro sitio. Decir
«en el historial de Public» dejó de ser cierto el 2026-09-25. Los seis logs no versionados de DEV_P7B_INCREMENTAL
se conservaron únicamente en la papelera: no afirmar que están en Git.
Las etiquetas antiguas siguen siendo datos ya utilizados, no un nuevo examen
independiente por haberlas retirado del árbol de trabajo.
