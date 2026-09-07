# Correcciones y unificación — estado comprobado 2026-09-07

> **Estado vigente:** [correcciones desplegadas y confirmadas el 08-09](CIERRE_DESPLEGADO_2026-09-08_CODEX.md).
> Lo que sigue es historial; no reaplicar los parches pendientes descritos aquí.

> Actualización posterior: parches aplicados y suites locales verdes.
> El estado vigente está en [CIERRE_LOCAL_FIXES_2026-09-07_CODEX.md](CIERRE_LOCAL_FIXES_2026-09-07_CODEX.md).
> Este documento conserva la situación anterior como historial.

## Veredicto de esta sesión

**ReactPortfolio utilizable; correcciones locales INCOMPLETAS, NO DESPLEGADAS.**
No se declara cierre de E/F ni GO de calidad. No se ha abierto un holdout,
cambiado una política ni modificado producción. HEAD sigue en `38c51ab`;
los cambios de esta sesión siguen sin commit. Este anexo distingue la
observación actual del informe de revisión anterior, que era solo local.

## Uso actual, comprobado en el NAS en lectura

- `portfolio_backend` responde 200 a `/health/deep`; `writes_frozen=False`.
- Routing observado: catalog/matching `core_read`, applications/saved_searches
  `core_primary`. Esto no equivale a afirmar que cada lectura canary provino
  del core: `core_read` conserva fallback.
- Se pueden consultar ofertas y gestionar candidaturas. El botón Aplicar abre
  la URL de la oferta y registra el estado; NO envía automáticamente un CV ni
  completa el formulario de la empresa. No se efectuó ninguna candidatura real
  ni una prueba de escritura autenticada en el navegador durante esta sesión.
- Core R5: release `38c51ab`, base `swissjobhunter_r5_rehearsal`, sin CE activo.
  BFF productivo: base `swissjobhunter`, endpoint interno `core-api:8000/v1`.
  Ambos destinos son distintos y un futuro ensayo/deploy debe respetarlo.

## Implementado localmente

1. **Invalidación de exclusiones:** nueva `core0042`, sin cambiar migraciones
   publicadas. Trigger de generación en cambios de reglas; declaración idéntica
   no reescribe el conjunto. El bloqueo del perfil precede a la invalidación.
2. **Entrega durable y versionada:** estado pendiente en la misma transacción
   que el filtro local, versión monotónica, receptor bajo lock que rechaza
   conflictos e ignora snapshots obsoletos. Reintento periódico y al arranque,
   sin mantener transacción durante HTTP; API expone `sync_status`.
   Migración BFF nueva `c4d8e2f60a17` sobre `b3c7d1a95e42`.
3. **Presupuesto CE:** primer microlote de un documento, lotes posteriores
   pequeños, deadline compartido con preparación/persistencia/publicación y
   proceso de inferencia terminable. Sin dependencias nuevas. El feed se publica
   únicamente cuando el cache-only está completo y revalidado.

YAGNI orientó estos cambios a reutilizar generación, locks y persistencia existentes;
se aisló únicamente la inferencia que necesita poder detenerse realmente.

## Verificación ejecutada, SIEMPRE en serie

| Comprobación | Resultado observado |
| --- | --- |
| Pruebas dirigidas core: repros anteriores, versión, proceso y CE | 31 passed |
| BFF: entrega durable + analytics | 12 passed |
| Suite core completa | 1066 passed, 3 failed; 754,67 s |
| Suite BFF completa | 2282 passed, 1 failed, 3 skipped, 4 xfailed; 258,83 s |
| Nueva sonda feed vacío/recuperación | 1 fallo reproducible, pendiente de fix |
| `git diff --check` | Sin errores |

Los tres fallos del core son expectativas de head `core0041` frente a `core0042`
en los ensayos de migración. Hay cinco aserciones a actualizar porque dos no se
alcanzan hasta superar la primera de su test. No se han cambiado todavía.

El fallo del BFF es `test_predecididas_no_solapan_con_el_codigo_vivo`: Jobicy
está reactivado en providers pero sigue en `SOURCES_DECIDED_IN_ADVANCE`.
Los archivos de ese test, registro y política no han cambiado respecto de HEAD:
es una incoherencia previa identificada por la suite, no una corrección aplicada.
No se ejecutó una segunda suite completa en un checkout de baseline.

Las pruebas SQL de CE mantienen sus motores simulados mediante una factoría
de test. `test_ce_runtime.py` verifica aparte procesos reales, cancelación,
recolección y validación de respuesta. Esto NO sustituye el benchmark del
modelo real ni del J1800. No hay medición nueva de hardware en esta sesión.

## Bloqueos de código pendientes, con siguiente acción exacta

### 1. Callback omitido en un resultado vacío válido

`matching.evaluate_profile` publica el feed vacío pero omite `on_evaluated`
si `evaluated == 0`. Después de excluir todas las ofertas queda señal de
recuperación encendida sin necesidad real de trabajo.

Reproducción: `docs/recheck_empty_recovery_20260907.py`. Se ejecutó contra
PostgreSQL desechable: el feed es `[]`, pero el perfil permanece en la señal.
Fix: invocar el callback también en ese éxito revalidado. Los caminos
`not_found`, `sin_vector` y deriva ya retornan antes; el registro del proyector
ya ignora generación NULL cuando no hay corpus. Conservar ambos contratos.

### 2. Cerrar el manejo de errores de entrega

Si la operación local ya confirmó y fallan HTTP y la persistencia del diagnóstico,
el segundo fallo no debe transformar la respuesta en 500. El pending ya es durable.
Añadir regresión de ambos fallos y proteger la escritura del diagnóstico.
Si un restore local deja una versión inferior a la del core, mostrar
`core_version_ahead` sin dar por entregado el snapshot ni esconder el conflicto.
Probar que el aviso de una entrega antigua NO pisa una versión local posterior.

### 3. Expectativas de migración y registro Jobicy

Actualizar solo el head esperado y sacar Jobicy de la lista de fuentes todavía
deshabilitadas, NO de la cosecha viva ni de su política. No borrar aserciones.

Parches puntuales preparados, **NO APLICADOS**:

- `docs/FIXES_PENDIENTES_2026-09-07.patch` (callback, comentario, head y errores).
- `docs/FIX_REGISTRO_JOBICY_2026-09-07.patch` (registro).

La herramienta `apply_patch` falla al leer para Update File con
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`, incluso invocada
por terminal escalada. La revisión automática rechaza sobrescribir archivos
completos con Add File, también tras verificar el diff de una sola línea.
No se eludió ese rechazo: el candidato permanece separado en
`/tmp/swissjob-fixes-20260907.R855Y9/matching_candidate.py`.
Restablecer el editor de parches puntuales o resolver expresamente la autorización
de sustitución verificada antes de aplicar. No desplegar el árbol actual.

## Secuencia para retomar, sin repetir toda la investigación

1. Revisar `git status` y preservar `school_job_monitor_architecture.md` del usuario.
   ReactPortfolio/backend también tiene cambios ajenos: NO incluirlos en la release.
2. Aplicar los parches mínimos con editor operativo; incorporar la sonda de feed
   vacío a la suite y añadir pruebas de fallo de diagnóstico/ACK adelantado.
3. Hacer visible el estado pendiente en la UI de filtros, también tras borrar la
   última regla; refrescarlo mientras haya pending. Hoy solo está en la API.
4. Revisar todos los consumidores del nuevo campo obligatorio `version` antes
   de desplegar. Reejecutar pruebas dirigidas y suites completas en serie;
   conservar mordidas de los defectos originales, no reinterpretar rojos.
5. Backup y restore **estricto** de ambas bases en entorno desechable, con
   `--exit-on-error`, paridad de constraints/índices/triggers y definiciones.
   Ensayar upgrade/downgrade y reconciliación, incluida baja pendiente y versión.
   Nada de suprimir stderr ni aceptar un restore incompleto.
6. Commit solo de los cambios propios verificados y construir desde ese commit
   limpio. No usar el árbol con cambios ajenos como fuente de una imagen `:prod`.
7. Coordinar expansión core/BFF: el receptor nuevo requiere versión. Pausar
   escritores BFF durante el seed para que no quede una edición del BFF viejo
   posterior al snapshot inicial sin evento durable. Diseñar rollback compatible;
   el downgrade BFF impide perder entregas pendientes por diseño.
8. Canary de alta/baja y llegada desordenada sobre perfil de prueba autorizado:
   observar feed servido, pending y recuperación; después verificar perfiles
   reales SIN cambiar sus preferencias ni enviar candidaturas.
9. Mantener baseline y feed operativo; CE sigue sin promocionar. Para activarlo:
   benchmark real frío/pico/CV/reinicio dentro de presupuesto y examen nuevo
   independiente conforme al protocolo vigente. No reutilizar el número invalidado
   `0.5856/0.7438` como certificación ni cambiar umbrales.

## Unificación restante y deuda actual

- **C/D:** uso del core ya operativo; esta sesión no repite ni certifica un
  cutover nuevo. Falta desplegar y confirmar las correcciones anteriores.
- **E:** documentos y colegios todavía locales. Conteo leído en Portfolio:
  generated_documents=0, schools=6, school_jobs=88, school_applications=0,
  cv_profiles=7. Cero documentos actuales NO prueba que pueda retirarse el
  generador. Inventariar APIs/escritores, portar por capacidad con ownership,
  golden PDFs, manifiesto/rollback, GDPR y canary antes de apagar su lado local.
- **F:** Portfolio informa schedulers `armed`; su funnel de cosecha depende
  del global encendido, que también gobierna servicios escolares. No apagarlo
  globalmente sin inventario y paridad: podría eliminar fuentes únicas. SwissJob
  legacy/CDC pueden seguir siendo necesarios hasta portar sus fuentes.
- **Retirada final:** faltan paridad de fuentes, drenaje, restore final, ventana
  estable y retención ratificada. Se preguntó por retención; no se recibió todavía
  una decisión en esta sesión. No borrar tablas, volúmenes, usuarios ni archivos
  históricos por inferir que ya no hacen falta.
- **Calidad:** NO-GO por ausencia de examen válido nuevo; independiente del uso
  actual baseline. No prometer un sistema sin fallos: usar regresiones, canary,
  observabilidad y rollback probado para detectar y contener los que aparezcan.

Los plazos de E/F requieren desarrollo y observación reales; no son otro nombre
para aplicar estos parches. La racha de calidad, si el protocolo la exige al
promover, necesita 168 h reales elegibles y no certifica retrospectivamente E/F.

## Limpieza y preservación

Solo se retiraron dos placeholders de CERO bytes creados por mounts diagnósticos
en `jobhunt_core/tests/`; las reproducciones completas se conservan en `docs/`.
No se borraron datos del proyecto, copias, imágenes ni volúmenes. La sesión no
deja un despliegue parcial ni procesos de pytest en ejecución al cerrar este acta.
