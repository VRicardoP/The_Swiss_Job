# Punto 4 — exclusión y planificador nativo desplegados

Checkpoint 20-09-2026, 08:16 Europe/Madrid. **Infraestructura de cosecha nativa
desplegada y comprobada; cero fuentes nativas habilitadas. Punto 4 abierto.**

## Versión y pruebas

- `5425202`: exclusión global por scope, entrada manual y orden de locks; suite
  completa **1.516 passed**, 871,69 s.
- `6ba912e5ab7e50bbdbbe889c807a579e7206b792`: despacho independiente por scope y
  ventana, conservando cadencia seis horas; suite final **1.522 passed**, dos
  avisos previos, 858,62 s. Código inmóvil durante ambas suites, ejecutadas en serie.
- Logs locales: `/tmp/point4-global-scope-core-full.log` y
  `/tmp/point4-native-dispatch-core-full.log`; regresiones y límites en las actas
  [de exclusión](HARVEST_SCOPE_EXCLUSION_2026-09-20.md) y
  [de planificación](NATIVE_HARVEST_DISPATCH_2026-09-20.md).
- Construcción offline desde `git archive` del commit, sin árbol sucio ni nuevas
  dependencias. Archivo idéntico en ambos extremos:
  `d67310f271c991201dec0de088d9a02b9383e60e1c49d61c0312e59e3aa0006b`.
- Imagen `swissjob-core:point4-6ba912e`, digest
  `sha256:d3d4331fd25812cc716c015794d0892817da141f4d5e95d1772c4157e654cc1d`.
  Identidad OCI y fichero RELEASE corresponden al commit completo.

## Maniobra NAS

1. Configuración candidata privada: sólo referencias de imagen; comparación
   estructural con la anterior y CAS por contenido antes de instalar. No se
   tocaron credenciales, perfiles, feedback, políticas ni routing.
2. Ejecución candidata del dispatcher: **0 scopes, 0 despachos, 0 fetches**.
3. Pausa de consumidores del worker exacto `celery@7e825e5f59bd`, conservando
   mensajes y tareas. Inspección confirmó `active=[]`, `reserved=[]`,
   `scheduled=[]` y ninguna cola consumida antes del reemplazo.
4. Recreación de core API/worker/capture con margen de parada 2.400 s; operación
   completada sin esperar dicho límite. No hubo purga de colas ni kill manual.
   No se obtuvo un evento histórico `die` para certificar el exit code anterior:
   la evidencia disponible es drenaje explícito, recreación y aceptación posterior.
5. Nuevos procesos ejecutan el mismo digest, `RestartCount=0`; API y captura
   saludables. El worker nuevo `celery@f35c78b1ac55` consume las cinco colas core
   originales, sin tareas reservadas/programadas en la comprobación.

Artefactos/configuración privados exclusivamente NAS:
`/share/CACHEDEV1_DATA/Public/unification-e15-20260914/native-preactivation.6ba912e/`
y `native-release.GmZ8qV/`. Configuración core final:
`f0f28ff5b85be3e85f8df7feae75535d98c7ddb81e7239fac80e23cdeea7a49c`.
Core Alembic sigue `core0049`; este lote no contiene migraciones.

## Aceptación de lecturas y autoridades

Comprobación HTTP real del BFF, sin traducción ni escrituras de prueba:

| Perfil | Feed HTTP | Página / total | Guardados | Entrega de perfil |
|---|---|---|---|---|
| 1 | 200 | 20 / 1.800 | 0 | sin pendientes/errores |
| 2 | 200 | 20 / 1.799 | 18 | sin pendientes/errores |

`/health/feedback`: `writer=core`, `writes=enabled`. BFF sigue `39579d7`;
perfiles y feedback conservan sus autoridades activadas anteriormente. No se
usó el rollback de la importación de feedback ni se enviaron correos/candidaturas
de prueba. Registro: `/tmp/point4-native-live-reads.json`.

## Retirada pendiente y corrección descubierta

Los productores de ofertas legacy siguen intactos. El ensayo Remotive en copia
verificó identidad, canónica e idempotencia, pero la comparación del pipeline
completo detectó **5 altas que el filtro antiguo de títulos excluiría**. No
activar Remotive hasta preservar esa regla en la admisión nativa y repetir la
paridad. [Evidencia](REMOTIVE_COPY_HANDOVER_2026-09-20.md).

Siguen el traspaso por fuente, extracción escolar, búsquedas/avisos y desacoplar
la recuperación de embeddings/matching antes de retirar CDC/proyector. El
despacho nuevo NO da esas tareas por cerradas. Punto 5, cron y aceptación final
no se han adelantado, y el NO-GO de calidad no se modifica.

## Recuperación de este lote

Mientras no exista ningún scope nativo habilitado, puede restaurarse la imagen
core anterior `39579d7` con los mismos parámetros, drenando primero el worker.
La configuración anterior se conserva en `core.before.yml`. No volver a una
imagen anterior al fencing de perfiles/feedback. Tras habilitar fuentes, esta
reversión simple deja de bastar: detener/drenar nativos y demostrar preservación
de nuevas ofertas/decisiones antes de reactivar productores anteriores.
