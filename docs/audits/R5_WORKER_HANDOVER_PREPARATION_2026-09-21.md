# Punto 4 — worker R5 preparado para el traspaso por fuente

**Desplegado, guard verificado; ninguna fuente transferida todavía.**

El worker R5 anterior (`8c82ff5ca175`) no soportaba el apagado por fuente.
Se sustituyó por la misma imagen ya comprobada en el worker público:
`swissjob-worker:point4-d89b6ee`, digest
`sha256:2859bfc2e958115e60259ac185b3d2edfbad46fe40722c6dc96f072c06cf0b74`.
No se cambió el disparador de seis horas ni su cola.

## Compatibilidad y ausencia de cosecha adicional

- Comparación de esquema de sólo lectura: R5 `b46e1230a901`, público
  `c57f2341b012`; sólo falta en R5 `profile_sync_state`. La imagen candidata
  declara head `b46e1230a901`: no requiere ni añade esa migración.
- Ocho variables explícitas de Compose coinciden con el contenedor anterior.
  Combinando también los defaults de imagen, cero diferencias ambientales.
- La imagen nueva registra Jobicy y la vieja no. Por ello se declara
  `LEGACY_DISABLED_PROVIDERS=["jobicy"]` en R5 para conservar su cobertura:
  25 providers anteriores, 15 scrapers. No se habilita una fuente por accidente.
- Proceso candidato leyó las 34 filas de salud en la base R5 sin mutar datos.
  Guard vivo posterior: `get_provider('jobicy') is None`, Remotive aún disponible,
  cero descargas de prueba. El worker público conserva Jobicy sin cambios.

## Maniobra y resultado

Pausa de consumidores del worker exacto `celery@3ad96726e750`; comprobación
`active=[]`, `reserved=[]`, `scheduled=[]`, sin colas consumidas. Sin purga ni
terminación manual de tareas. Reemplazo sólo del servicio `worker`, `--no-deps`,
margen de parada de 2.400 s. Nuevo worker `celery@11f92f59f23f`: tres colas
`default/scraping/ai`, sin tareas activas/reservadas/programadas en la comprobación;
imagen esperada, `running`, cero reinicios.

No se afirma una cosecha exitosa posterior: esa aceptación pertenece al siguiente
barrido y al corte específico. Core, frontend, backend público y trigger R5 no
se modificaron durante esta maniobra.

## Configuración operativa y evidencia

Directorio privado NAS:
`/share/CACHEDEV1_DATA/Public/unification-e15-20260914/r5-source-handover-20260921/`.

Configuración efectiva para posteriores operaciones del worker:
`worker.coverage-preserved.json`, SHA256
`292347584e88448cdc33b0e15e92afc20279f063debe2ac6d70b06b7664b7a7c`.
Se usa con proyecto `swissjob-r5` y selección explícita del servicio: **no ejecutar
un `up` global ni `--remove-orphans`**. El Compose antiguo bajo `/share/Public/swissjob`
no representa ya la imagen/guard de este worker; no usarlo para recrearlo.
Configuración y entorno quedan sólo en NAS, con permisos privados, no en git.

Logs locales agregados: `/tmp/point4-r5-config-parity-checked.log`,
`/tmp/point4-r5-candidate-validation.log`, `/tmp/point4-r5-candidate-readiness.json`,
`/tmp/point4-r5-drained.json`, `/tmp/point4-r5-worker-deployed.log` y
`/tmp/point4-r5-worker-after.json`.

Reversión de esta preparación: antes de transferir fuentes se puede reponer la
imagen anterior conservada y drenar de nuevo. Después de transferir una fuente,
NO usar esa imagen sin su guard: reabriría el segundo escritor. Mantener la
imagen corregida y seguir la reversión por fuente del runbook.
