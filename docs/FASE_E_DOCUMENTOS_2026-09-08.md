# Fase E — documentos: contrato y ejecución incremental

Resultados, commits e incidencia local:
[acta E.1](CIERRE_LOCAL_E1_2026-09-08.md) y
[continuación E.2/local](CIERRE_LOCAL_E2_2026-09-08.md) y
[alta atómica E.3](CIERRE_LOCAL_E3_2026-09-08.md) y
[avance E.4](AVANCE_E4_2026-09-08.md) y
[conservación E.5](AVANCE_E5_2026-09-08.md).
Decisión ratificada: conservar CV/carta al borrar la candidatura, hasta borrado
explícito o retención. E.5 implementa referencia/snapshot, autoridad de candidaturas
y biblioteca visible; sigue siendo código local, no un flip documental al core.

## Estado y autoridad

E.1 implementa el **almacén/API core de documentos terminados**, no la generación
LLM ni el PDF. Es un paso aditivo de E; **no cierra E ni la unificación completa**.
No se ha desplegado core0043 en el NAS, concedido sus scopes a credenciales vivas,
migrado documentos ni cambiado el routing. La operación sigue descrita en
[el acta de despliegue](CIERRE_DESPLEGADO_2026-09-08_CODEX.md).

Inventario de solo lectura del NAS al preparar E.1 (recontar bajo freeze antes de
migrar): SwissJob, 2 documentos; Portfolio, 0 documentos, 7 CV profiles,
6 colegios, 88 ofertas escolares y 0 candidaturas escolares. El cero de documentos
Portfolio NO autoriza retirar generación/PDF ni cambiar su comportamiento futuro.

| Estado | Documentos SwissJob | Documentos Portfolio | Colegios |
| --- | --- | --- | --- |
| Producción actual | Escritor/lector local | Escritor/lector local | Autoridad actual conservada |
| E.1 código | API core disponible tras upgrade, sin cliente conectado | Igual | Sin cambios |
| Flip futuro E | Core único, solo tras E.2–E.4 | Core único, solo tras E.2–E.4 | Corte separado |

Referencias rectoras: `CONTRATOS_FASE_A.md` §1 (FK de documento a oferta SET NULL),
`PLAN_UNIFICACION_JOBHUNTING.md` §5.4 (JSON/PDF), ADR §04/§07 (inmutabilidad/erase).
No se reescriben migraciones publicadas core0001–core0043.

## E.1: contrato implementado

Migración **core0043**, hija de core0042. `generated_documents` conserva contenido
UTF-8 exacto y hash SHA-256 calculado por el servidor, tipo `cv|cover_letter`,
idioma, contexto JSON, referencia local opaca, modelo y tiempo de generación.
Perfil obligatorio con FK restrictiva: borrar un perfil exige borrar explícitamente
su contenido personal. Borrar la revisión de una oferta solo anula la referencia;
no borra el documento ni su contexto. Trigger de inmutabilidad permite esa anulación,
pero no cambiar el contenido/identidad/metadata de un documento ya creado.

Cada generación es un nuevo UUID con `version=1`, `async_state=ready`.
`pdf_location` es de solo lectura y permanece NULL en E.1: no se promete PDF core.
La eliminación emite versión 2. No hay PUT/PATCH ni cola asíncrona simulada.

Base de las rutas: `/v1/profiles/{profile_id}/documents`.

| Operación | Contrato |
| --- | --- |
| POST colección | `documents:write`, Idempotency-Key obligatoria; 201 o replay; body distinto con misma key → 409 |
| POST `/batch` (E.3) | `documents:write`, key obligatoria; 1–2 documentos, tipos distintos; alta/eventos/recibo atómicos, 201 o replay completo; miembro borrado → 404 sin resucitar |
| GET colección | `documents:read`; keyset `(created_at,id)` descendente, máximo 20, filtro tipo/referencia local |
| GET `/{id}` | `documents:read`, ownership en JOIN, ETag/304 |
| DELETE `/{id}` | `documents:write`, If-Match si se suministra, idempotencia opcional, 204 |

Todos los accesos se acotan por perfil **y consumer**. Objeto ajeno/inexistente →
404; falta de scope → 403. El alta rechaza referencias de oferta inexistentes.
Validación previa a escritura: NUL/surrogates/no-finitos, campos desconocidos,
contenido >1.000.000 bytes y contexto JSONB >65.536 bytes, incluida la expansión
de notación exponencial que efectúa PostgreSQL. No se trunca contenido en silencio.

POST/DELETE bloquean perfil antes de recibo/documento, como el erase. El recibo
idempotente del POST contiene **solo el ID**, no otra copia del CV. Un replay tras
el borrado devuelve 404 y no resucita el contenido durante la vigencia del recibo
(TTL existente: 24 h). No se promete deduplicación de operaciones a perpetuidad:
la futura migración necesita su propio manifiesto durable de identidad origen→destino.

Documento y outbox se confirman juntos. Fallo del outbox revierte documento y recibo.
El erase del perfil elimina documentos, recibos de estas rutas y eventos sujetos
al perfil, sin tocar datos de otros consumers ni el corpus compartido.

### Evento nuevo: `document.changed`

- Agregado `document`, ID de documento; destino = consumer propietario del perfil.
- Versión 1 alta / versión 2 baja, clave natural `<document_id>:<version>`.
- Payload exclusivamente `{document_id, profile_id, version, deleted}`.
- Sin contenido, contexto, nombre ni CV en el evento; entrega con semántica
  at-least-once del outbox existente.
- **Antes de cualquier escritor core vivo**, ambos inbox afectados deben aceptar,
  deduplicar y confirmar el evento y ensayarse replay/ACK perdido. No vale declarar
  este evento soportado porque el outbox pueda insertarlo.
- SET NULL por purga del corpus no emite una nueva generación; los consumidores
  no deben tratar la referencia opcional de oferta como contenido inmutable ni
  mantenerla como FK autoritativa local. El contenido y su hash permanecen intactos.

### Reversión de esquema

Downgrade core0043→core0042 se niega si hay documentos **o eventos document.changed**.
Toma un lock exclusivo NOWAIT ANTES de comprobar vacío: no cabe un INSERT entre
la comprobación y el DROP. Freeze/drenaje operativo siguen siendo obligatorios.
No borra datos para permitir una reversión. El BFF anterior conserva sus rutas
locales; el CORE debe mantener una imagen compatible con core0043 mientras haya
datos nuevos: `/v1/ready` exige igualdad exacta de head, por lo que volver a una
imagen core0042 dejando esquema core0043 NO es una reversión saludable.
Los rollbacks antiguos tampoco deben poder borrar perfiles con documentos nuevos.
Si hubo escrituras nuevas, primero reconciliar contenido y entregas con el
procedimiento del corte E; nunca restaurar toda la base core compartida.

## E.2–E.4: siguientes pasos bloqueantes del flip

1. **Adaptadores y contrato BFF.** Clientes E.2 implementados y probados con HTTP
   real, aún SIN vincular al routing vivo; ver acta E.2. No importar
   `jobhunt_core` desde legacy. Resolver el perfil por consumer+identidad local;
   mantener fuente/referencia local y snapshot de oferta sin depender de una FK
   legacy en core. SwissJob usa job_hash; Portfolio application_id y user_id entero:
   no son el UUID del perfil core ni identidades intercambiables.
2. **Un único escritor por estado.** E.2 corrigió el canary SwissJob:
   create/delete son exclusivamente locales. No activar el adaptador vinculado
   antes de migrar el estado y ensayar el corte.
   Un timeout tras commit core NUNCA permite escribir también local. En canary,
   escrituras locales; tras corte confirmado, escrituras core sin fallback local.
   Probar cada modo, indisponibilidad tras commit y consulta de documentos históricos.
3. **Comportamiento completo.** E.3 añade alta atómica de CV+carta y método de
   lote Portfolio, verificados en PG y HTTP reales, todavía sin cablear al router.
   Falta conservar el UUID de operación y el body generado de forma recuperable
   antes del envío; no repetir el LLM como retry. Preservar idioma/campos y
   mantener generación fuera de locks.
   Fijar goldens de JSON/PDF y descarga; migrar también cleanup de retención, no
   solo routers HTTP. La caché Redis ya se versiona por inputs y confirma el UUID
   en el almacén (E.4, aún no desplegado); probarla de nuevo durante el corte con
   la autoridad core y la política de conservación ratificada.
4. **Migración ensayada sobre copia fiel.** Restore con exit-on-error y cotejo de
   constraints/índices/triggers; freeze y drenaje de TODOS los escritores de esta
   capacidad. Manifestar PK origen/destino, ownership, fecha original, idioma,
   metadata, hash exacto y relaciones, sin imprimir el CV. La API de alta corriente
   NO basta para preservar UUID/created_at históricos: usar migrador explícito,
   idempotente y auditable, probado también con fixtures Portfolio no vacías.
5. **Canary y rollback.** Ida/vuelta sin pérdida, reanudación tras corte, replay de
   eventos, límites, cliente sin credenciales y tenant ajeno; POST/listado/PDF/delete
   mediante rutas BFF reales. Activar por consumer/perfil únicamente después de
   reconciliar. Conservar escritor anterior apagado pero recuperable; rollback
   debe incorporar cualquier escritura nueva, no esconderla tras lectura local.
6. **Colegios por separado.** Inventariar configuración estática SwissJob frente
   al CRUD y scraping Portfolio; elegir autoridad y preservar contacto, alertas,
   ofertas y candidaturas. No apagar schedulers globales mientras exista una fuente
   escolar única. Después, y solo con paridad demostrada, preparar F fuente a fuente.

## Deuda y límites explícitos

- Pendientes de E: vinculación operativa de adaptadores, inbox, generación/PDF, limpieza por retención,
  migrador y reversión con datos reales, escuelas y cutover. Ninguno se cierra
  con una tabla o con una suite verde del almacén.
- El contexto admite datos personales: no loguearlo. El cifrado por perfil y la
  destrucción criptográfica en backups de `DEUDA_TECNICA.md` §2.10 siguen abiertos;
  el borrado SQL y de recibos no los implementa. Resolver su requisito operativo
  antes del cierre E/GDPR, sin afirmar que backups ya están saneados.
- El inbox Portfolio preexistente quedó integrado en checkpoint separado 26a3374;
  no está activado en NAS. Preservar el cambio ajeno de
  `school_job_monitor_architecture.md`; no empaquetarlo en un deploy.
- Incidencia **local** anterior a E.1: runtime ya alineado a d908ea2/core0042
  después de backup/restore estricto; API/capture healthy, worker responde,
  3.000 cambios drenados y staging pendiente 0. Quedan cuatro ofertas activas
  locales sin listing core (TOAST omitido sin imagen previa): reconciliación
  dirigida pendiente, claves/evidencia en acta E.2. No es un fallo del NAS.
- GO de calidad independiente pendiente de examen nuevo; E.1 no cambia modelos,
  umbrales, holdouts ni la racha. Una racha exigida de siete días necesita 168 horas
  reales después de cumplir sus precondiciones, no empieza por desplegar esta API.

YAGNI: se reutilizan auth, idempotencia, outbox, cursores y transacciones existentes;
sin dependencias nuevas, motor PDF duplicado ni abstracción de almacenamiento futura.
