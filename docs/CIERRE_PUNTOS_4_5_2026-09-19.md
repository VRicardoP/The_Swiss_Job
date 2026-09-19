# Cierre de puntos 4 y 5 — ejecución en curso

Autorización: petición del propietario del 19-09-2026. Orden obligatorio:
productores/retirada → rendimiento → cron/alarma → aceptación integral → entrega.
No confundir avances locales con cierre desplegado. No se cambia el gate de calidad.

## Preflight confirmado

- Base SwissJob `d9c8a0a`; cambios/documentos retirados anteriores preservados.
- NAS: E.15 sigue activo (core `1b8d910`, BFF/worker SwissJob `4e40ffe`, Portfolio
  `9f8c85a`). Inventario por SSH sin secretos. La única intervención NAS hasta
  este avance es detener el backend auxiliar R5 averiado (véase abajo).
- Core: 28 fuentes con scopes `legacy:*` deshabilitados para cosecha nativa;
  `school-observation` también es un scope de observaciones, no un harvester.
- El worker R5 registra 16 providers y 15 scrapers. La lista de registros no
  demuestra ejecución: salud de varios scrapers R5 data de agosto; los colegios
  tienen además el productor público adaptado en E.15. Comprobar ambos antes de
  retirar nada. Providers con ejecución reciente incluyen Arbeitnow, Remotive,
  WWR, Working Nomads, EU Remote Jobs, Jobspresso, TheHub, Jobgether, NAV,
  Ostjob, Zentraljob, PublicJobs, Zebis y GlobalJobs. Proz y RemoteCo reportan error.
- La imagen R5 no incluye Jobicy en su registro aunque HEAD sí: reconciliar
  inventario de código y operación, no habilitar fuentes restringidas por defecto.
- La lectura de matching aún exige respaldo `Job` local; feedback explícito e
  implícito siguen escribiendo `match_results`. Alta de candidaturas requiere MD5
  local (schema de 32 caracteres). Documentos sí admiten UUID core. Son dependencias
  reales a cerrar antes de retirar el corpus/productor legacy.
- PostgreSQL y Redis de DESARROLLO local arrancados para pruebas; no se arrancó
  un scheduler nuevo en NAS ni se habilitó ningún scope nativo allí.

## Avance local

Primer lote: productores JSON nativos Remotive, Working Nomads y Jobicy.
Conservan el objeto original; normalización posterior en el sink, id upstream
preferido y URL como fallback explícito (no garantía ante cambios de URL).
No activan fuentes. Jobicy necesita scopes por tag/geo para replicar sus consultas.
La paridad de filtrado/ventana/enriquecimiento con legacy está PENDIENTE: registrar
un adaptador y pasar sus pruebas no acredita cobertura ni autoriza el corte.

Pruebas antes de implementación: 21 fallan porque falta el nuevo adaptador/registro.
Después: 57 verdes (adaptadores nuevos + Arbeitnow); 71 verdes contra PostgreSQL
en BD desechable (runs, harvest y adaptadores). Contadores solapados, no sumarlos.


### Costura de feedback e identidades (preparación, NO activada)

- API core: feedback explícito e implícito con ownership, lock de perfil,
  idempotencia y eventos transaccionales; conserva puntero de evaluación y bookmarks.
- Guardados positivos: página/total de una sola sentencia, incluidos archivados
  y marcas sin evaluación. No se confunden con el feed activo ni con candidaturas.
- Resolución de referencias upstream en core: 404 ausente y 409 ambiguo, sin elegir
  una vacante por orden ni consultar el corpus local del BFF.
- Candidaturas BFF aceptan UUID; snapshot del catálogo y vínculo directo al core.
  Sin respaldo local, el feed conserva vacancy_id en vez de fabricar un MD5.
- CORE_FEEDBACK_ENABLED=False preserva el escritor previo durante el despliegue.
  La rama de corte no admite fallback local y exige routing autoritativo.
  FEEDBACK_WRITES_FROZEN + /health/feedback preparan el freeze verificable.
- PENDIENTE antes de activar: migración/rollback del estado vigente (incluidos clears,
  implícitos y referencias escolares en cuarentena), compatibilidad de enlaces antiguos,
  interacción de guardados con estado escolar, suite completa y ensayo NAS.
  Los interruptores nuevos NO están habilitados en ningún contenedor del NAS.

Primera suite completa del core: 1.241 verdes (860,97 s). BFF completo:
2.410 verdes, 3 omitidos y 4 xfail (297,63 s). No son la aceptación de la
versión final: después se añadieron las regresiones NUL (3 verdes en referencias),
la persistencia escolar y la eliminación del respaldo local en otras rutas.
Segunda suite core: 1.246 verdes. Tercera: **1.257 verdes, 862,76 s**,
incluida la migración incremental core0047→0048 y el downgrade protegido.
Después: 9 verdes de importación/reversión de feedback y su CLI; no forman
parte de esa suite completa anterior. BFF completo actualizado: **2.422 verdes,
3 omitidos, 4 xfail, 5 avisos; 284,14 s**. Todas las suites en serie.
El primer intento de suite core se interrumpió en colección por faltar scripts
en el montaje; se corrigió el montaje antes de verificar, sin alterar producción.
No sumar contadores solapados ni presentar esas cifras como aceptación integral.

### Estado durable real descubierto (consulta de solo lectura, 19-09)

- SwissJob público: 2 perfiles vinculados; 10 búsquedas guardadas; 1.205 avisos;
  0 candidaturas ordinarias; 0 borradores locales y 0 estados locales avanzados.
- Hay 18 marcas escolares (explícitas o implícitas): 2 ofertas vinculadas al
  corpus y 16 en cuarentena sin vacancy_id; estas últimas incluyen 2 positivas.
  Todas conservan observación escolar en core. No se pueden omitir ni inventar
  vacantes para migrarlas.
- Preparada core0048 (NO publicada/desplegada): feedback e implícitos pertenecen
  al estado escolar existente. Conserva borrador/contexto/status; idempotencia y
  ownership bajo lock de perfil. Guardados incluyen cuarentenas con su identidad
  escolar, no UUID de vacante ficticio. Los rechazos siguen filtrándose si la
  observación se vincula posteriormente; las ediciones vinculadas se sincronizan
  transaccionalmente con el estado canónico.
- La reversión E.15 antigua falla cerrada si ya hay marcas bajo autoridad core:
  no conoce el rollback coordinado de feedback. Este último sigue PENDIENTE antes
  de activar el flag. El downgrade tampoco elimina marcas, incluidos clears.
- La captura R5 lee `swissjobhunter_r5_rehearsal`; el BFF público escribe en
  `swissjobhunter`. Comparación de solo lectura: los 2 perfiles públicos y sus
  revisiones core coinciden hoy en title/cv_text/skills/languages/locations/
  experience_years/salary_min/salary_max/remote_pref. Esta igualdad NO acredita
  propagación de una edición futura: hace falta cerrar su escritor/sincronización
  antes de retirar CDC. No se imprimieron valores personales ni credenciales.
- Core conserva 54 estados escolares y 10 búsquedas bajo `swissjob-shadow`,
  además del perfil `portfolio`. No confundir tenant real con nombre del BFF.

### Verificación adicional y traspaso de feedback (todavía NO activado)

- Hay 21 filas locales con feedback explícito o implícito: 18 escolares y 3
  generales. Las 21 tienen alguna vacante por URL; eso no convierte en enlazadas
  las 16 observaciones escolares en cuarentena. Sus identidades se preservan.
- Contraste entre las dos bases vivas, sólo lectura y salida agregada: 51 marcas
  canónicas existentes coinciden con su origen local; 0 alias con decisiones
  contradictorias. No acredita por sí solo un snapshot congelado para migrar.
- Cuatro regresiones de enlace tardío fueron rojas antes del fix: la decisión
  explícita más reciente (incluido clear) manda aunque el vínculo escolar llegue
  después. Lecturas y selección de candidatos usan el mismo predicado.
- Otra regresión fue roja: el feed nativo general no debe convertir un UUID
  conocido en un MD5 potencialmente ambiguo. Corregido; colegios conservan la
  identidad necesaria para sus comandos. Rama antigua intacta con flag OFF.
- `feedback_cutover plan/apply/revert`: plan previo privado y sellado, comprobación
  del origen completo bajo locks, importación atómica, read-back e idempotencia.
  Conserva clears y multiplicidad de implícitos, se niega a elegir entre alias
  contradictorios y no borra un cambio posterior del usuario. Ida/vuelta de
  feedback escolar preserva exactamente borrador/status/corpus en PostgreSQL.
  **revert es PRE-ACTIVACIÓN**: sigue pendiente el retorno coordinado de cambios
  creados después del corte. No presentar este comando como sustituto de ese trabajo.
- Copia del esquema core restaurada con `pg_restore --exit-on-error`, propietarios
  y ACL, en PostgreSQL 16.14 separado, sin red ni puertos publicados. Huellas
  estructurales idénticas al NAS: 201 restricciones, 126 índices, 38 triggers y
  378 columnas; 0 restricciones sin validar. core0048 aplica limpia en la copia.
- El control de permisos bloqueó la copia completa de `swissjobhunter`: la
  autorización explícita anterior nombraba `proyecto`. Solicitada autorización
  específica para datos personales/hashes, uso privado aislado y retirada en
  48 h. No se ha intentado eludir el bloqueo ni se ha obtenido ese dump.
  El ensayo entre origen y destino reales sigue PENDIENTE de esa autorización.
- El dump core está registrado como temporal (48 h); el clúster privado debe
  retirarse también al concluir el ensayo. No se ha aplicado core0048 en
  producción ni cambiado ningún flag de escritor.

### Incidencia operativa descubierta en el preflight

`swissjob-backend-r5`: 1.169 reinicios observados, imagen 8c82ff5ca175 sin la
migración publicada b46e1230a901 que su BD ya tiene. No llegaba a servir HTTP.
Se detuvo SOLO ese contenedor con docker stop (conservado, sin borrar datos o
cambiar esquema). Worker R5, disparador de cosecha, core y BFF público siguen
activos. Proxy público apunta a swissjob-backend:8000 y /api/v1/health devolvió
healthy después de la intervención. Reanudar ese auxiliar exige alinear imagen
y esquema antes; docker start por sí solo reabriría el bucle. Esta contención
operativa NO acredita la retirada de todos los productores ni el cierre del punto 4.
## Lista de cierre (todos pendientes salvo avance indicado)

1. Inventario por fuente y escritor/horario efectivo, con estado activo, sustituido,
   retirado por decisión o pendiente. Incluye productores de colegios de ambas apps.
2. Resolver UUID nativos en lectura, candidaturas y feedback; migrar el estado
   pendiente bajo freeze y ensayar rollback. Ninguna copia ficticia de Job para
   maquillar la retirada. Mantener compatibilidad de enlaces MD5 históricos.
3. Portar extracción/normalización con raw original, identidad/cursor/health,
   compliance y paridad; canary fuente por fuente antes de apagar su escritor viejo.
4. Comprobar mantenimiento, digest, perfiles, filtros, CDC, colas y tareas manuales
   antes de retirar procesos. Drenar y probar replay; no borrar slots activos.
5. Backup/restore estricto; conservación legacy en solo lectura durante el plazo
   ratificado. Se ha preguntado 7/14/30 días sin detener la implementación.
   No fijar el inicio de la ventana antes del último corte real.
6. Corregir A18-01..04 de DEUDA §0.A antes de aceptación; medir A18-05 y conciliar
   configuración A18-06. Presupuestos de rendimiento explícitos antes de medir.
7. Programar retención con scripts existentes y todos sus registros; probar éxito,
   fallo y alarma real sin borrar objetivos no inventariados.
8. Aceptación por ambos frontends/proxy, ofertas, candidaturas, documentos, colegios
   y recuperación. Pruebas con datos sintéticos etiquetados; nunca enviar solicitudes
   de empleo o correos a terceros como simple prueba.
9. Documentación final, deuda residual, versión y evidencia de imágenes/configuración.
   No push ni publicación de backend privado por inferencia de «versión de entrega».

## Invariantes

Un escritor por capacidad/fuente; ningún fallback silencioso; políticas/modelos
intactos; raw primero; replay idempotente; error no equivale a vacío; pruebas en
serie; migraciones nuevas sin reescribir publicadas; restore con errores fatales;
reproducción roja antes de fix y verde después; verificación del servicio servido,
no solo de filas o etiquetas Docker. El holdout independiente sigue siendo una
campaña separada y no se fabricará un GO de calidad para cerrar infraestructura.
