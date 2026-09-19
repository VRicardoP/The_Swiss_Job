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
- Preparada core0048 (versionada en 3db1bec, NO desplegada): feedback e implícitos pertenecen
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

### Admisión y cuatro RSS nativos (LOCAL, 19-09)

- Portado el parser de fechas ADR-10 sin dependencia del backend. Parámetro de
  scope `admission_window_days` explícito y semántico: cambiarlo reinicia cursor;
  nunca se envía al portal. En el corte se debe declarar 7 para estas fuentes.
  Ausencia conserva el comportamiento anterior de los scopes ya existentes;
  no es una autorización para activar nuevas fuentes sin su política.
- La ventana sólo rechaza ALTAS. Refresca identidades nativas conocidas y URLs
  EXACTAS de la misma fuente legacy: ni otra fuente ni un fragmento parecido
  dan por conocida una oferta. Fecha ausente/corrupta no se inventa. Un feed
  no vacío sin ninguna fecha válida queda `partial/admission_missing_dates`,
  incluso si permite refrescar conocidas. Contadores y cursor son atómicos
  con la persistencia y quedan fuera del cursor entregado al provider.
- Regresiones iniciales: 9 rojas antes de implementar. Después: **69 verdes**
  incluyendo fechas, runner previo, fuente/URL exacta, cambio concurrente de
  política, cutoff inclusivo, configuración inválida y rollback del sink.
- WWR/EU Remote Jobs/Jobspresso/GlobalJobs: RSS nativo con identidad GUID/URL
  estable, respuesta acotada, DTD rechazado también en UTF-16, errores visibles,
  preservación del XML del item (contenido, no formato byte a byte) y filtros/
  extracción portados del legacy. Sin nueva dependencia ni activación.
- **20 regresiones rojas antes** del adaptador; **26 verdes después**, incluidas
  4 cadenas reales provider→admisión→sink→canónica y repetición sin duplicados.
- Paridad sobre UNA respuesta pública real por fuente, sin escrituras en BD:
  WWR 84/84, EU Remote 15/15, Jobspresso 16/16, GlobalJobs 207/207; 0 ausentes,
  0 adicionales, 0 discrepancias de campos canónicos, 0 URLs repetidas. Evidencia:
  `docs/audits/NATIVE_RSS_PARITY_2026-09-19.json`. No equivale a canary NAS.
- Coste preliminar de reconocimiento por URL en la copia core: EXPLAIN ANALYZE
  142 ms para 100 URLs, 20.163 slots examinados; máquina local, caché fría, NO
  presupuesto NAS. Registrar para punto 5; no añadir índices por conjetura.
- Suite completa final de este lote: **1.335 verdes, 931,62 s**, un aviso previo
  de deprecación Starlette/httpx. Código inmóvil mientras corrió la suite.
  Productores, perfiles/CDC, búsquedas/avisos y retorno de
  feedback POST-activación siguen pendientes. Punto 4 NO cerrado.

### Alternativa de ensayo sin exportación de SwissJob (19-09, en curso)

La autorización de exportación de `swissjobhunter` al ordenador sigue sin
concederse y NO se ha realizado. Se abrió una alternativa de menor exposición:
copia y restauración íntegramente DENTRO del NAS, sin red/puertos publicados.
Contenedor `swissjob-f-rehearsal-20260919`, memoria 384 MB, CPU 0,5; directorio
privado `swissjob-f-rehearsal.goIBte` bajo Public del NAS, plazo máximo 48 h.
El dump public de SwissJob quedó en ese directorio con modo 0600 y hash verificado
tras traslado. Se retiró el intermedio `/tmp/unused` del contenedor postgres.
Ese primer dump terminó con código 0 pero su stderr se suprimió por error:
NO se deduce validez de ello; faltan restore estricto y paridad estructural.
El ensayo no sustituye el backup operativo ni autoriza aún ningún flip.
Retirar también el PGDATA aislado, no sólo los dumps, al concluir.

### Zebis y paridad JSON (LOCAL, segundo lote)

- Zebis nativo conserva reparación de host roto, identidad estable por URL
  reparada, empleador, ubicación, skills y filtro. Diez regresiones rojas antes
  y verdes después. Una respuesta real: 50/50 ofertas, sin diferencias canónicas.
- La paridad JSON descubrió omisiones del primer adaptador: skills extraídas,
  categoría y filtro técnico/query de Working Nomads, y error parcial cuando
  hay elementos inválidos. Cinco reproducciones rojas antes del fix.
- Otra reproducción roja detectó cambio de texto por decodificar entidades y
  fusionar palabras entre tags inline. Corregido para preservar embeddings;
  se conserva la supresión defensiva de script/style, diferencia explícita.
  Remotive: 17/17; Working Nomads: 44/44; cero diferencias tras corregir.
  Jobicy: ConnectTimeout, paridad viva NO verificada, no se habilita.
- 82 pruebas verdes del lote ampliado (4,96 s), incluidas 3 cadenas JSON con
  PostgreSQL real y replay sin duplicados. La suite 1.335 anterior NO incluye
  estas adiciones; no presentarla como aceptación del nuevo HEAD.
  Evidencia: `docs/audits/NATIVE_PARITY_EXTRA_2026-09-19.json`.
- Restore SwissJob dentro del NAS: `--exit-on-error` termina en 0; coinciden
  52 índices, 1 trigger y 222 columnas; 40 restricciones y ninguna sin validar.
  Un CHECK de routing tiene distinta representación de casts tras reparseo:
  array varchar convertido a text[] frente a cada elemento convertido a text.
  Mismos cinco literales y tabla de verdad comprobada (cinco válidos, vacío y
  futuro rechazados, NULL desconocido). No se oculta la diferencia de hash.
  La copia core sigue restaurando índices; migración/roundtrip aún pendientes.

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
