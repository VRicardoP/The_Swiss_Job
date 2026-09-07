# Correcciones desplegadas y verificadas — 2026-09-08

## Estado vigente

**Correcciones desplegadas y confirmadas mediante canary en el NAS.**
Comprobación final: 08-09-2026, aproximadamente 00:42 Europe/Madrid.
Código: `4f5a366` + hotfix `d908ea2`. Este documento actualiza las fotografías
anteriores `ESTADO_FIXES_UNIFICACION_2026-09-07_CODEX.md`,
`CIERRE_LOCAL_FIXES_2026-09-07_CODEX.md` y
`ENSAYO_RESTORE_CORE_2026-09-07_CODEX.md`; no hay que reaplicar sus parches.

- **Producto utilizable:** ReactPortfolio responde healthy, con escrituras abiertas.
  Consultar ofertas y gestionar candidaturas no exige promover un modelo CE.
  Aplicar abre la oferta y registra el estado; el envío efectivo se completa en
  la web de la empresa. No se enviaron candidaturas reales en esta sesión.
- **C/D:** se conserva el funcionamiento sobre el core. No se repitió un cutover.
- **Unificación completa:** NO; documentos/colegios (E) y retirada legacy (F)
  siguen pendientes. No se han apagado sus escritores por dar este parche por cerrado.
- **GO de calidad:** NO certificado. No se promovió CE, abrió un holdout, cambió
  un umbral ni inició una racha. El número invalidado `0.5856/0.7438` no es evidencia.

## Qué quedó corregido

`4f5a366` contiene la invalidación de exclusiones mediante generación, entrega
durable/versionada BFF→core, reintentos independientes del scheduler de cosecha,
diagnóstico sin transformar un commit local en 500, aviso pending en UI incluso
tras la última baja, intento de recuperación para feed legítimamente vacío y
presupuesto CE con proceso terminable y publicación cache-only. También corrige
expectativas de migración y el registro desfasado de Jobicy, sin desactivar la fuente.

El primer canary de producción encontró DOS defectos introducidos en esa entrega:

1. **Idempotencia dependiente del orden regional:** comparar una lista ordenada
   por PostgreSQL con otra ordenada por Python producía 409 con la misma versión
   y las mismas siete reglas. `d908ea2` compara conjuntos. La regresión utiliza
   PostgreSQL real y collation ICU explícita; verifica que los órdenes difieren.
2. **Error tardío sobre un ACK confirmado:** una entrega duplicada fallida podía
   sobrescribir `last_error` después del ACK de la otra. El UPDATE del diagnóstico
   exige ahora `delivered_version < sent_version`, además de la versión vigente.
   Regresión con dos sesiones y barreras, sin depender de una pausa arbitraria.

Ambas reproducciones FALLARON antes de corregir, por 409 y diagnóstico residual,
respectivamente. Después pasan. No se perdieron reglas: incluso antes del hotfix
el estado era versión 1 / entregada 1; quedaba un diagnóstico obsoleto.

Se investigó y REFUTÓ que la causa fuera una collation distinta del restore:
NAS y copias usan `en_US.utf8`. El defecto era comparar órdenes de dos motores.
El canary inicial de repetición solo había cubierto conjuntos de cero/una regla;
por eso no refutaba este caso de varias reglas.

YAGNI orientó el hotfix a dos guardas locales y dos regresiones, reutilizando los
locks, versiones, sesiones y tooling existentes. No se añadieron dependencias.

## Backups y restore estricto autorizados

Tras la autorización expresa se copiaron AMBAS bases, solo al directorio LOCAL
privado `/tmp/swissjob-restore-20260907.q7GxPh/` (0700). Dumps 0600, `pg_dump -Fc -Z1`,
exit 0 antes de renombrar el fichero parcial. Contienen datos personales: no versionar.

| Base NAS | Fichero | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| swissjobhunter_r5_rehearsal | core.dump | 652241450 | 2196c9fd360db92fad3ccde1b7874168d439040075b7d1df537b1e5a1193d396 |
| swissjobhunter | bff.dump | 318378222 | 8c956ce4e79f9a257b7048b6249072d976ebe42ee94adfb67096f0dee324fd4d |

La primera base, pese a llamarse rehearsal, es el CORE VIVO servido a usuarios.
La segunda es el BFF productivo. No son intercambiables ni desechables en el NAS.

Restore local con `pg_restore --exit-on-error --single-transaction`, conservando
owners/ACL, sin suprimir errores. PostgreSQL `swissjob-restore-20260907`, red none,
sin puertos publicados; bases `core_copy` y `bff_copy`. APIs de canary comparten
solo ese namespace aislado, escuchando en loopback. Credencial de canary emitida
en la COPIA, nunca exportando la credencial productiva ni contraseñas de roles.

Objetos cotejados en `public` + `jobhunt`: definición/validación de constraints,
definición/valid/ready de índices, definición/enabled de triggers no internos y
tipo/propietario/ACL de relaciones:

| Copia | Constraints | Índices | Triggers | Relaciones | Sin validar |
| --- | ---: | ---: | ---: | ---: | ---: |
| Core | 182 | 145 | 34 | 70 | 0 |
| BFF | 167 | 140 | 13 | 67 | 0 |

Core: JSON estructural idéntico. BFF: cuatro CHECK mostraban otra representación
de casts de arrays; se recreó cada expresión original en tablas temporales y
PostgreSQL produjo exactamente la definición restaurada. No se ignoraron diferencias.
El resto de objetos coincidía. Evidencia privada: `structure.sql`, JSON estructurales
y `check_restore_casts.py` en el directorio anterior.

Round-trip de ESQUEMA comprobado: core0041↔core0042 y BFF b3c7d1a95e42↔c4d8e2f60a17.
El downgrade BFF rechaza un pending real; solo se completó después de obtener ACK
auténtico. No se inventó un delivered_version ni se retrocedió la generación.
Esto no afirma igualdad byte a byte de toda la base después de los canaries:
estos realizaron evaluaciones y cambios controlados en las copias.

Canary con rutas BFF autenticadas y HTTP real al core: seed entregado, alta,
reevaluación y exclusión en feed, baja con core inaccesible → 204 + pending,
reintento en otro proceso sin nueva edición, ACK y oferta visible tras reevaluar.
Además: entrega vieja no resucita bajas, conflicto de versión → 409 y payload
antiguo sin versión rechazado. Tras downgrade se comprobó el cliente antiguo.

Se repitió upgrade con las imágenes d908ea2 y el canary multirregla: dos ACK 200,
siete reglas preservadas, cero pending/errores, ambos feeds BFF en 200 con lector
CoreMatching puro. Control negativo local: 2112 ofertas vivas casan reglas,
cero violaciones en el feed. Al terminar, copias en core0042/BFF c4d8e2f60a17;
API y PostgreSQL del ensayo DETENIDOS, datos y dumps conservados.
`/tmp` no sustituye una política de backup permanente: acordar destino/retención
antes de retirar originales en F.

## Pruebas ejecutadas en serie, sobre imágenes sin montar código

| Imagen / prueba | Resultado |
| --- | --- |
| 4f5a366: suite BFF | 2288 passed, 3 skipped, 4 xfailed; 303,39 s |
| 4f5a366: suite core | 1072 passed, 1 skipped; 978,43 s |
| 4f5a366: caso de release desconocida, aparte | 1 passed |
| d908ea2: regresiones/foco | Core 2 passed; BFF 11 passed |
| d908ea2: suite core completa | 1073 passed, 1 skipped; 802,03 s |
| d908ea2: caso de release desconocida, aparte | 1 passed; 1,09 s |
| d908ea2: última suite BFF completa | 2289 passed, 3 skipped, 4 xfailed; 317,25 s |

El skip core es `test_release_desconocida_es_error_en_operacion`: la imagen tiene
release identificable. Se ejecutó aparte con `RELEASE_SHA=unknown` SOLO en el
contenedor de test, nunca en producción. Los tres skips BFF son E2E A.SEAM sin
core-api accesible desde ese entorno; no se cuentan como pasados. Se complementan
con los canaries HTTP anteriores, no se presentan como ejecuciones de esos tests.

Persisten avisos de deprecación, caché pytest no escribible y coroutines de mocks
legacy sin await. No hubo fallos inesperados en estas suites. Frontend: 7 pruebas,
build/lint y nginx comprobados en la entrega 4f5a366; el hotfix no cambia frontend.
Esto es evidencia de regresión, no una garantía de ausencia absoluta de bugs.

## Despliegue y confirmación en operación

Builds desde `git archive` del commit, sin incorporar cambios ajenos. BFF conserva
dependencias de una imagen base existente: inventario de nombres/versiones Python
comparado con el BFF NAS, CERO diferencias. No se desplegó el build convencional
que había vuelto a resolver dependencias transitivas. Transferencia de imágenes
desde LOCAL; no se ejecutó `docker save` en el NAS.

Primera entrega coordinada 4f5a366: escritores BFF pausados durante seed y migración;
core0042 y BFF c4d8e2f60a17 aplicados mediante Alembic, sin bootstrap de políticas.
Luego API ready, BFF/frontend saludables y arranque worker/captura.
Hotfix d908ea2: solo imágenes, SIN DDL nuevo; worker verificado idle antes de empezar,
API lista antes del BFF y BFF saludable antes de recrear worker/captura.

| Componente vivo | Estado final |
| --- | --- |
| Core API / worker / capture R5 | d908ea2 comprobado en los tres procesos; API/capture healthy; worker responde pong |
| Core BD | core0042; `/v1/ready` ready + authoritative=true |
| BFF SwissJob | imagen d908ea2, healthy; BD c4d8e2f60a17 |
| Frontend SwissJob | imagen 4f5a366, healthy; proxy `/api/v1/health` → healthy |
| ReactPortfolio | `/health/deep` → 200 healthy, writes open, schedulers armed; sin cambio de imagen/config |

Hashes de código dentro de los procesos iguales al commit probado:
API core `e6a03cbdd5c02fdd106812217a0d72eb95fc79b9534cbde3342183fd7a3b655b`;
entrega BFF `beb80db20bedde263cc40c25a0afb8e1e5ea37d8db1231eae2c2aa654173d7af`.

Canary NAS: dos PUT con el MISMO snapshot ya confirmado → 200, ACK exacto y las
siete reglas intactas. Solo entonces se limpió el diagnóstico obsoleto, condicionado
a que versión vigente y entregada siguieran siendo la comprobada. No se editaron
preferencias ni se fabricó un ACK. Consulta posterior: pending=0, last_error=0.

Ambos perfiles: lector CoreMatching puro, ruta BFF 200, 100 ítems de API core
comprobados por perfil. Control negativo sobre TODO el feed servible: **2131 ofertas
vivas casan las reglas y CERO violaciones**. No es solo un conteo de evaluaciones
históricas. Políticas activas: dos, hash de IDs sin cambios
`68e0df392b1141101917ba63648a5dfb` (baseline canónica; hybrid v4 sombra).

Todos los contenedores sustituidos tienen restart=0; revisión de sus logs desde
arranque sin marcadores ERROR/CRITICAL/Traceback. La CLI Docker emite un aviso
preexistente de permisos de config.json del usuario; las operaciones terminan bien.
La observación final dura minutos: NO se presenta como 24 horas ni siete ciclos.

## Reversión y preservación

Se conservan en el NAS `swissjob-core:rollback-before-d908ea2` y
`swissjob-backend:rollback-before-d908ea2` (4f5a366), además de las tres imágenes
`rollback-before-4f5a366`. No se borraron imágenes, fuentes, volúmenes ni backups.

Para revertir SOLO d908ea2 ante una regresión confirmada: retaggear sus referencias
de rollback a core:r5-cycle y backend:prod y recrear los mismos servicios con los
compose existentes, API ready → BFF healthy → worker/captura; verificar feed/ACK.
No necesita downgrade ni restore de datos. Esa reversión reintroduce los dos bugs
de idempotencia conocidos: medida de contingencia, no cierre alternativo.

Revertir TODA 4f5a366 exige pausa coordinada de escritores, drain/reconciliación real
de pending, downgrades ensayados y ambas imágenes antiguas compatibles. Nunca
restaurar indiscriminadamente la base core compartida sobre escrituras nuevas.
Procedimiento ejecutado y sondas quedan en el directorio privado del ensayo.

## Siguiente secuencia y deuda abierta

1. **E por vertical**, no un flip global: inventario actualizado de campos/escritores,
   contrato de autoridad, esquema/API con ownership e idempotencia, migración con
   manifiesto, checksums y rollback, canary y después apagar su escritor anterior.
   Se confirmó que core aún NO tiene API/almacén de documentos/colegios. SwissJob
   expone DocumentsPort para almacenar/listar/borrar; escuelas usa configuración
   estática. Portfolio conserva generated_documents ligado a application_id y
   CRUD/scrape de schools/school_jobs. No confundir estas identidades/autoridades.
2. **Documentos:** preservar generación/descarga/PDF, idioma y metadata; goldens y
   pruebas cross-tenant/GDPR antes de retirar WeasyPrint/local. Una tabla vacía no
   justifica borrar la capacidad de generar documentos futuros.
3. **F fuente a fuente:** demostrar paridad y salud, luego apagar el escritor viejo;
   drenar CDC/outboxes y comprobar replay. No desactivar globalmente schedulers de
   Portfolio: hoy también atienden colegios que pueden ser fuentes únicas.
4. **Retirada final:** backup/restore final, ventana estable y retención explícita;
   después expand/contract. No hay autorización nueva de retención o borrado. Los
   cambios ajenos en ReactPortfolio y school_job_monitor_architecture.md se conservan.
5. **Calidad independiente:** benchmark NAS del runtime CE cambiado (frío/pico/CV,
   corte y reanudación, paridad y presupuesto) antes de activar; nuevo examen
   independiente, nunca entrenamiento/evaluación solapados ni cambiar la barra.
   Si procede promoción, los 7×24 h son reales; no certifican E/F retrospectivamente.
6. **Higiene no bloqueante:** avisos de tests y reproducibilidad de dependencias
   transitivas se abordan aparte con evidencia; no actualizar bibliotecas durante
   un hotfix de entrega. Consolidar los diarios históricos mediante enlaces al acta
   vigente, sin convertir sus cifras pasadas en estado actual.

No existe un plazo honesto de «todo terminado» deducible de estas suites: E requiere
desarrollo/migración por capacidad (referencia del plan 3–7 días, no compromiso), F
depende de fuentes y retención, y una racha elegible impone 168 horas si se exige.
El uso actual del producto no espera a esos tres hitos.
