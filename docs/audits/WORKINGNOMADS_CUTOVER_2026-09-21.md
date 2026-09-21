# WorkingNomads — traspaso nativo aplicado

Fecha: 2026-09-21. Este acta sustituye el estado «corte pendiente» del
preflight del mismo día. No declara cerrado el punto 4 completo.

## Despliegue y autoridad

- Core API/worker/capture: `3d5d67ab9f8c3cc47d15dea32fe6077cae41ff06`,
  imagen `swissjob-core:point4-3d5d67a`, esquema `core0050`.
  Digest: `sha256:49785087bc16c7fa3ac115ef289438c3f1d68e4095df5a2fc09ee5ceaaa8baa0`.
- WorkingNomads deshabilitado en backend público y ambos workers legacy;
  acceso individual y registry completo verificados con tripwire, sin fetch.
  El worker R5 conserva también su exclusión previa de Jobicy.
- Workers anteriores drenados: salida 0, sin OOM, sin purgar colas.
  El drenaje core tardó unos 24 minutos por embeddings/matching del proyector;
  desacoplar ese postprocesado sigue pendiente, no se ocultó matando tareas.
- Activación después del drenaje y con CDC pendiente cero. Un único scope
  nativo habilitado: `49232928-5e43-4c3a-bb65-c1d1c4fa0e24`.
  Ventana de admisión de 7 días y filtro de títulos legacy conservado.

## Evidencia

- Paridad legacy/nativo sobre el mismo cuerpo público: 45 registros,
  mismos IDs y cero diferencias en los 12 campos comparados, incluidos
  idioma, cantón, seniority, contrato y salarios.
- Reparación del histórico: 99 examinados, 64 canónicas actualizadas;
  texto y hash de embedding intactos, segunda pasada idempotente.
  Ensayo previo en copia NAS: rollback exacto. Operador serializado mediante
  el lock existente de la fuente, sin añadir dependencia del proyector global.
- Lote nativo real: **40 ofertas, 1 página, status ok**, 17 admitidas nuevas
  por fecha y 23 refrescadas; 40 canónicas con idioma presente.
  `last_complete_at=2026-09-21 16:43:56.027399+00:00`, fallos consecutivos 0.
- Replay con la misma clave: **skipped, 0 ofertas**, sin segunda cosecha.
- Canary HTTP a través del BFF desplegado: **5 vacantes con fuente primaria
  nativa**, 200, `CoreCatalog`, títulos y enlaces conservados. Sólo lectura;
  no se crearon candidaturas ni avisos de prueba.
- Revalidación del feed BFF tras el corte: ambos perfiles por `CoreMatching`,
  página de 20 y totales 1800/1799; entrega del perfil sin pendientes ni error.
  Sólo lectura, sin modificar feedback ni notificaciones.
- Suite core del release: **1645 passed, 2 warnings, 907.32 s**.
  Pruebas dirigidas: 70 passed; contrato estático: 3 passed.

Los recibos privados permanecen en el NAS, bajo
`unification-e15-20260914/workingnomads-preactivation.3d5d67a/`:
guardas, drenaje, reparación, activación, lote y replay. No se exportaron
datos personales al ordenador. No se hizo git push ni se tocó `:prod`.

## Pausa solicitada y límites

Al terminar esta comprobación, detener el trabajo por orden del propietario.
No iniciar otra fuente, refactorización, campaña de calidad ni punto 5.
Los servicios operativos siguen funcionando; la pausa es del trabajo del agente.

Diez búsquedas ya tienen autoridad core; sigue pendiente observar su primera
entrega natural. Las demás fuentes, digest/búsquedas Portfolio, productores de
colegios y retirada final legacy siguen pendientes: **punto 4 abierto**.

La copia temporal aislada `swissjob-f-rehearsal.goIBte` conserva su caducidad
**21-09 a las 20:00 UTC / 22:00 Madrid**. No tiene borrado automático configurado;
la pausa no prorroga su retención. No confundirla con el corpus vivo ni con
los recibos operativos del traspaso.
