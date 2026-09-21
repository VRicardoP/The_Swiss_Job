# Punto 4 — sustitución de CDC para los perfiles públicos

Estado actualizado 21-09-2026: **canal desplegado, habilitado y confirmado**
según el [acta de entrega](audits/PROFILE_DEPLOYMENT_NAS_2026-09-20.md).
Ambos perfiles mantenían entrega sin pendientes ni errores en el
[canary posterior](audits/WORKINGNOMADS_CUTOVER_2026-09-21.md).
No cierra el punto 4 ni autoriza apagar la captura de ofertas todavía necesaria.
El contrato y las verificaciones de preparación siguientes se conservan como
historial; sus versiones/imágenes no sustituyen el checkpoint operativo actual.

## Problema que resuelve

El BFF público edita `swissjobhunter`; CDC R5 captura una base distinta. Que dos
perfiles coincidan una vez no demuestra entrega de una edición futura. El escritor
de entradas de CV sigue siendo el BFF (CRUD y tareas de análisis conservadas),
como el CV del Portfolio. El motor core recibe una proyección durable: no se
mantiene el motor de matching/corpus legacy como segunda autoridad.

## Contrato

- Migración BFF `c57f2341b012`, posterior a `b46e1230a901`: `profile_sync_state`,
  una fila por usuario. Inserciones/ediciones de `user_profiles` y actividad de
  `users` la actualizan en la MISMA transacción mediante triggers. Captura también
  los workers de CV, sin confiar en un dispatch posterior al commit.
- Cada escritor actualiza sólo su parte del snapshot: perfil o actividad. El
  contador compartido serializa ambas; una edición concurrente no restaura la
  actividad antigua ni el CV antiguo. Cambios de embedding/weights/colegios no
  generan entregas de contenido que no cambió. Se conserva siempre el último
  snapshot pendiente, no una cola ilimitada de CVs duplicados.
- `CORE_PROFILE_SYNC_ENABLED=false` por defecto. OFF captura, pero no envía.
  ON entrega periódicamente fuera de la transacción, independientemente de la
  cosecha. Sin credencial/vínculo no hay HTTP; queda diagnóstico pendiente.
- `GET /api/v1/profile/sync-status`: sólo el usuario autenticado, sin CV ni
  secretos. Versiones, pendiente, último intento y error saneado. No constituye
  prueba de entrega si el flag está apagado.
- Core `core0049`: `PUT /v1/profiles/{pid}/source-snapshot`, profiles:write y
  ownership, snapshot completo de nueve campos + actividad + versión positiva.
  Falta de campo/extra/rango inválido no se interpreta como una edición parcial.
  Los `target_roles` configurados sólo en core se preservan.
- Primera entrega aceptada transfiere la autoridad del perfil. Versión menor
  sólo acusa la vigente; misma versión con distinto cuerpo da 409. Un ACK viejo
  no borra una edición nueva del outbox. Versión core adelantada tras un restore
  exige conciliación: no se fuerza el contador ni se da por entregada.
- CDC de contenido y borrado de usuarios deja de poder modificar ese perfil,
  bajo el mismo lock; el borrado coordinado explícito por API sigue disponible.
  La actividad para el proyector y las métricas usa la fuente nueva, no la copia
  R5. Su consulta permanece única y de coste constante por número de perfiles.
- Vaciado explícito admite una revisión sin texto, retira punteros del feed y
  no la embebe. Desactivación retira también el feed; nunca borra guardados,
  feedback o historial. La publicación de matching revalida actividad bajo lock.
  Reactivar una revisión idéntica rearma la recuperación. También se invalidan
  los intentos al cambiar de revisión: A→vacío/B→A debe reconstruir el feed,
  aunque A ya tenga una evaluación histórica y el corpus no haya cambiado.

## Verificación y estado de entrega

Diez regresiones core rojas antes del endpoint; cinco del outbox rojas contra el
backend d89b6ee. Pruebas HTTP/PG, rollback de transacción, ACK atrasado, caída de
red, desactivación durante inferencia sin cambiar revisión, reactivación, permisos,
CDC antiguo, vaciado, preservación de guardados y roles. Contención comprobada
por `pg_blocking_pids`, no deducida de sleeps.

Core: primera completa 1 fallo / 1.496 verdes por una consulta adicional en
actividad; se fusionó en una sola sentencia sin debilitar la regresión. Repetición:
**1.498 verdes**. Después se reprodujo en rojo el retorno A→vacío→A y se corrigió;
16 dirigidas verdes y completa final **1.499 passed**, commit `bac5e78`.
BFF: **2.459 passed, 3 skipped, 4 xfailed**, commit `a93dcbc`.
Una fixture inicial usaba `username`, que no es columna de User; se corrigió a
los campos reales. Contadores solapados: no sumarlos.

Migraciones y entrega/reintento/vaciado/recuperación ensayados sobre las dos
copias privadas del NAS; ver [evidencia y límites](audits/PROFILE_DELIVERY_NAS_2026-09-20.md).

## Secuencia operativa (desplegada y comprobada el 20-09)

Estado vivo y evidencia: [acta de despliegue](audits/PROFILE_DEPLOYMENT_NAS_2026-09-20.md).
Entrega 2/2, contenido/versión/ownership coincidentes; recuperación natural del feed
con cero punteros antiguos y canary BFF de 20 ofertas por perfil. Esto sólo cierra
el canal de perfiles, no la retirada de los demás productores.

1. Hecho: ambas suites, revisión de deltas, commits e imágenes limpios.
2. Hecho en copia privada NAS: restore estricto/equivalencia, upgrade de ambos esquemas,
   seed de perfiles preexistentes, entrega, edición/reintento/reordenación y vaciado
   sintéticos. Comparar contenido completo y retención del estado asociado; no
   editar el CV real del propietario para simular pruebas.
3. Antes de habilitar entrega, actualizar y comprobar TODOS los procesos core
   capaces de proyectar/publicar (incluido capture/proyector); ningún proceso
   viejo debe quedar escribiendo tras transferir autoridad. La instalación del
   esquema no protege por sí sola contra ejecutar un binario viejo.
4. Migrar fuente con entrega OFF, comprobar backfill/cola, vínculos y scope real
   de la credencial. No asumir que existe enrollment automático: un perfil nuevo
   sin vínculo queda explícitamente pendiente, igual que el contrato actual.
5. Activar la entrega con la imagen/contrato compatibles, drenar y verificar los
   dos perfiles reales por contenido y versión, sin imprimir sus datos. Comprobar
   proyector, frescura del feed y status servido; no apagar CDC de ofertas aquí.
6. Reversión de binarios tras transferencia requiere una imagen compatible con
   este contrato, preservando autoridad, versiones y todos los cambios posteriores.
   NO restaurar E.15 ni borrar columnas/outbox para forzar un downgrade. Ambos
   downgrades fallan cerrados si queda autoridad/estado que conciliar. El ensayo
   con vaciado y restauración mediante una versión nueva está verificado en copia;
   los dos downgrades se negaron a descartar el estado. Véase el acta del ensayo.

No hay permiso implícito para modificar políticas, el holdout ni enviar correos
o candidaturas reales. La retirada de productores de ofertas/colegios, búsquedas,
avisos y el retorno seguro de feedback son los otros trabajos pendientes de punto 4.
