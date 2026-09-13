# E.15 — punto 3: colegios

Estado (14-09): implementación y ensayo privado completados; validación final y
despliegue pendientes. No se ha cambiado el routing ni ningún productor vivo.

## Evidencia previa al despliegue

- Restauración estricta de las tres bases, sin silenciar errores. Core: 315
  objetos de estructura idénticos; Portfolio: 77; SwissJob: 93. En cada BFF hay
  una diferencia de representación del cast de ARRAY en el CHECK de routing:
  equivalencia comprobada para los cinco modos, valores inválidos y NULL.
- Importación histórica: Portfolio 6 monitores / 92 ofertas; SwissJob 18
  monitores / 53 ofertas / 54 estados / 2 preferencias. Reejecución idempotente,
  rollback por contenido íntegro, incluido ensayo con altas/ediciones posteriores.
- La historia no fabrica vacantes: cero creaciones de corpus en el ensayo.
  SwissJob enlaza 35 ofertas; las restantes conservan cuarentena explícita.
- El CDC vivo lee la base R5, no la del productor SwissJob público. Por ello,
  solo las observaciones recién extraídas permiten publicar ofertas nuevas;
  si la entrega falla, el productor no confirma su cursor. La identidad escolar
  conserva el hash accionable del BFF sin inventar un listing CDC.
- Borrado de monitor con historia: 409; se permite desactivar, no destruir
  candidaturas. El país de una identidad escolar existente no se reasigna.
- Credenciales/copias/recibos de ensayo quedan exclusivamente en el directorio
  privado; no se incorporan al repositorio. No se envían correos reales en pruebas.

## Alcance comprobado

- SwissJob: 18 entradas de monitor para 17 colegios (Beau Soleil tiene dos
  endpoints); listado, vigilancia por perfil, borradores,
  estados de candidatura y calendario. La costura de candidaturas conserva todavía
  esas operaciones en `match_results`, incluso con el CRUD ordinario en core.
- Portfolio: catálogo CRUD, extracción periódica y manual, alertas SSE/email con
  reintento, resumen diario. `school_applications` existe sin escritor HTTP actual:
  se preserva el estado, no se añade una interfaz nueva.
- NAS, 13-09: 6 colegios Portfolio, 4 activos, 92 ofertas, 0 candidaturas escolares;
  2 perfiles SwissJob con vigilancia habilitada. Son inventario, no una prueba de corte.

## Invariantes de implementación y cierre

1. Core posee identidad escolar compartida; monitores/contactos/preferencias se
   aíslan por consumer y los borradores/candidaturas por perfil. Una preferencia de
   Portfolio no modifica la de SwissJob para el mismo colegio.
2. `school_job_details` extiende vacantes del corpus, no crea un corpus alternativo.
   Entrada que no permita una identidad válida queda en cuarentena visible, sin
   fabricar URL ni enlazarla a una oferta arbitraria.
3. Mantener la extracción y las alertas existentes durante el traspaso. Cada estado
   cambia de escritor una vez; en core_primary no hay fallback de escritura local.
   El apagado definitivo de productores generales corresponde al punto 4, no a éste.
4. API con scopes/ownership, validación, conflictos e idempotencia. No transmitir
   borradores/contactos de un consumer a otro. Ningún email ni candidatura real
   se envía para simular una prueba satisfactoria.
5. Migración sobre copia restaurada estrictamente, inventario material por PK,
   reejecución idempotente, rollback antes/después de altas y ediciones, FK y
   borrado coordinado comprobados. Migraciones publicadas no se reescriben.
6. Pruebas en serie; canary de ambos BFF sobre el servicio desplegado, incluidas
   lectura, edición, borrador/calendario, vínculo al corpus y alertas con transporte
   interceptado. El punto sólo se declara cerrado después de confirmar operación.

No se alteran políticas/rankers, holdouts, cron diferido ni funciones de exportación.
Se conserva `school_job_monitor_architecture.md`, que ya contenía cambios del usuario.
