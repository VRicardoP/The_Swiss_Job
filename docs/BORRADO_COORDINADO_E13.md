# Punto 1 — borrado coordinado y restauraciones

Fecha: 2026-09-13. Estado: implementación y verificación en curso; NO cierre de backups.

## Qué protege el código

- La petición de borrado del BFF se guarda en `public.profile_erasures` en la misma
  transacción que elimina la cuenta. No depende de una FK a la cuenta eliminada.
- El drenado se ejecuta independientemente de la cosecha. Solo acepta un recibo
  explícito del core; un 404, caída o respuesta inválida sigue pendiente.
- El core conserva `profile_erasure_receipts` por consumidor e identidad. El borrado
  del grafo y su recibo se confirman juntos. Un consumidor ajeno recibe 404.
- El alta normal y el INSERT directo se serializan con la marca de borrado. El CDC
  descarta identidades borradas sin abortar los demás perfiles del lote.
- En el consumer `swissjob-shadow` también se limpia el texto personal del staging;
  las capturas posteriores se redactan. No se borran ofertas compartidas.
- La fila de generación de supresiones rechaza snapshots REPEATABLE READ anteriores
  al borrado. Los bloqueos NOWAIT evitan invertir los locks de perfil/staging;
  una transacción en conflicto se revierte y su petición sigue pendiente.
- Los informes de migración Portfolio pierden los diagnósticos materiales del sujeto,
  no los de otros usuarios. Se conserva procedencia por PK y se invalida la antigua
  atestación/rollback; no se borra corpus ni se reutiliza un verde anterior al borrado.
- Cada proceso BFF invalida las cachés del sujeto. Una respuesta HTTP que estaba
  en vuelo no puede volver a publicar una representación invalidada.
- Una cuenta aún sin vínculo core también puede declararse borrada antes de que
  llegue su primera proyección CDC.
- Las copias SwissJob consultan los recibos por HTTP autenticado, borran sus datos
  propios y confirman después de commit. Se conserva el mecanismo de contraseña
  del endpoint público; no se añade ninguna descarga/exportación integral.

## Topología que requiere dos confirmaciones

La aplicación pública usa `swissjobhunter`; la captura lee
`swissjobhunter_r5_rehearsal`. No se debe suponer que borrar en la primera genera
un evento CDC en la segunda.

Configurar con credencial del consumer `swissjob-shadow` y scope `profiles:write`:

- Aplicación pública: `CORE_ERASURE_REPLICA_ID=swissjob-live`, dentro de su BFF.
- Copia CDC: `CORE_ERASURE_REPLICA_ID=swissjob-cdc`, proceso dedicado
  `python -m services.profile_erasure --watch` con la imagen BFF nueva, SIN arrancar
  su `main.py`, puerto HTTP, cosecha ni sincronización de preferencias/exclusiones.
  Mantener el backend fuente existente. Aplicar primero el esquema BFF de solicitudes.

La copia fuente estaba en `b3c7d1a95e42`, no en el esquema de la aplicación
pública. El upgrade ensayado crea también una entrega de exclusiones pendiente.
No poner en marcha otro sincronizador que pudiera competir con el escritor
público. El downgrade antiguo rechaza esa cola: conservar el esquema compatible;
no marcarla entregada para forzar un verde ni descartar sus datos.

No habilitar este reconciliador en Portfolio: no comparte la identidad UUID de
cuenta de SwissJob. El DELETE core por perfil es multi-consumer, pero no acredita
haber borrado una copia local de Portfolio. Ese borrado requiere su procedimiento
propio y no se da por resuelto mediante un ack de SwissJob.

Las confirmaciones están en `jobhunt.profile_erasure_acks`. La ausencia de una
confirmación se considera pendiente, nunca éxito global. Los recibos y peticiones
NO se eliminan con una limpieza periódica. El downgrade rechaza destruirlos si
hay solicitudes reales. Una reversión de aplicación debe conservar estos datos
y la protección de la migración, no deshacer el esquema a ciegas.

## Restauración: barrera antes de abrir tráfico

1. Mantener ingress, workers y CDC del destino detenidos/aislados.
2. Obtener de la instancia actual el inventario de borrados y guardarlo FUERA del
   dump antiguo, en un directorio privado. No imprimirlo ni incorporarlo a Git.
   `python -m jobhunt_core.erasure_restore snapshot /directorio-privado/borrados.json`
3. Restaurar con `pg_restore --exit-on-error`, sin suprimir stderr. Verificar
   constraints, índices, triggers y FKs validadas; aplicar migraciones vigentes.
4. En la BD core restaurada, ejecutar primero el ensayo y después la aplicación:
   `python -m jobhunt_core.erasure_restore reconcile /directorio-privado/borrados.json`
   `python -m jobhunt_core.erasure_restore reconcile /directorio-privado/borrados.json --apply`
   Un artefacto inválido o una identidad contradictoria aborta; no abrir tráfico.
5. En CADA BFF SwissJob restaurado, con su base, credencial core y réplica correctas:
   `python -m services.profile_erasure`
   El comando debe terminar con éxito antes de arrancar ingress y workers.
6. Verificar ambas confirmaciones, ausencia del grafo personal y del texto del
   staging, conservación del otro usuario y del corpus, y reintento idempotente.
7. Solo entonces abrir tráfico. Si no se dispone de inventario actual e íntegro,
   el restore NO está habilitado para servir cuentas: reconstruir los borrados
   antes de levantarlo. El checksum detecta alteraciones; no sustituye su custodia.

Credenciales solo por el entorno existente. Los comandos emiten conteos/huellas,
no datos personales. El inventario requiere conservación independiente de los
backups de datos: restaurar también una copia antigua de los recibos no protege.

## Decisión de retención — 2026-09-13

El propietario delegó la elección más eficiente y recomendable. Se elige retención
limitada y restauración saneada, sustituyendo para este despliegue el requisito
no implementado de un KMS y claves por perfil. No se denomina crypto-shredding ni
se afirma que un backup histórico legible esté borrado.

- Backups: máximo siete días desde su creación; una copia nueva no reinicia el
  plazo del contenido antiguo. Reemplazo periódico y verificación obligatorios.
- Copias temporales de ensayo: máximo 48 horas. Una reserva concreta para rollback
  debe estar identificada, justificada y tener caducidad explícita, nunca indefinida.
- Ante fallo del backup nuevo se preserva el último restaurable y se declara
  INCUMPLIMIENTO de retención; conservarlo no permite dar el borrado por cerrado.
- El inventario mínimo de borrados se custodia por separado de los dumps antiguos.
  No contiene CV, contraseña ni contenido de candidaturas. Debe sobrevivir mientras
  exista una vía de replay/restauración que pudiera reintroducir al sujeto.
- Restauración siempre aislada, saneada con ese inventario vigente y comprobada
  antes de abrir tráfico. No se restaura primero para sanear después en producción.
- Expiración por registro explícito de ruta, tamaño, huella y fecha; nunca borrado
  recursivo por descubrimiento. El modo predeterminado es ensayo sin eliminación.
- Responsable operativo: propietario del NAS; la ejecución y las alarmas deben quedar
  automatizadas y verificadas antes de declarar implantada esta política.

Pendiente de verificación operativa: inventario completo NAS/ordenador, calendario,
caducidades, retirada comprobada y restauración. También se requiere el procedimiento
propio de borrado local de Portfolio; sus identidades no son las de SwissJob.

`backup_erasure=not_confirmed` permanece deliberadamente visible. No se presenta
un borrado local o dos acks como certificación del borrado de backups.

## Verificación local y límites del 2026-09-13

- Core: suite completa **1160 passed, 1 skipped** (897 s), en serie. Incluye
  erasure owned/cross-tenant, rollback, restauración saneada, captura tardía y
  snapshots REPEATABLE READ antiguos. Después, la última ampliación de raíces
  temporales de retención pasa sus **6 pruebas dirigidas** (0,19 s); no se presenta
  esa comprobación aislada como una nueva ejecución de la suite completa.
- BFF: pasada final **2384 passed, 3 skipped, 4 xfailed** (321 s), incluida la
  invalidación del sujeto sin interrumpir el HTTP de otro perfil. Advertencias
  deprecadas/corrutinas del legacy registradas; no se afirma ausencia absoluta
  de defectos por tener una suite verde.
- Copia privada real: core0045 → core0046, borrado de un perfil con datos, bloqueo
  del re-alta, rollback y huellas iguales en las tablas personales comprobadas y
  staging. El número de vacantes no cambia. Se devuelve core a core0045, sin recibos
  confirmados en la copia.
- BFF de esa copia: b3c7d1a95e42 → b46e1230a901 correcto. El downgrade completo
  rechaza **una exclusión pendiente** creada por el upgrade antiguo; revierte y
  conserva b46e1230a901. No se falsea su entrega ni se elimina para pasar la prueba.
- NAS, comprobación de identidades sin publicar datos personales: dos perfiles
  swissjob-shadow; ninguno sin cuenta en la aplicación pública ni en el origen CDC.
- La retirada periódica de backups y el procedimiento local de Portfolio NO están
  confirmados. El CV/chatbot de Portfolio son globales: falta fijar si el borrado
  de la cuenta propietaria los retira o preserva. No inferir autorización para
  retirar una publicación pública al borrar datos privados.
- NO se ha desplegado core0046 ni el nuevo BFF/reconciliador de borrados. El punto
  completo sigue abierto; los tests verdes no sustituyen ese despliegue/ensayo.

## Incidencia operativa encontrada durante el preflight

Portfolio en NAS estaba detenido desde 2026-09-10 con `Address already in use`:
su IP fija secundaria coincidía con la de swissjob-worker-r5. El 2026-09-13 se
reconectó únicamente esa red con asignación dinámica y se arrancó la MISMA imagen
`portfolio-backend:e10-715c347`. Se conserva la red primaria/alias `backend`,
el puerto, variables, volúmenes y datos.

Verificado HTTP 200 en /health interno, /health mediante el túnel público y
consulta autenticada de su perfil core (sin imprimir credenciales/contenido).
La sonda /health/deep exige admin; un 401 anónimo no es un fallo de la aplicación
ni acredita sus comprobaciones internas.

Prevención: en una recreación NO convertir las IP asignadas dinámicamente del
`docker inspect` en `IPAMConfig.IPv4Address`. Usar DNS/alias y conservar únicamente
las IP estáticas deliberadamente reservadas/justificadas. Verificar servicios
esperados también con `docker ps -a`, no solo la lista de los que ya están activos.
