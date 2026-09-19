# Barridos truncados: evidencia operativa previa al corte

Lecturas del NAS: 2026-09-19 23:30 UTC (20-09 en Europe/Madrid).
Sólo consultas de lectura y logs filtrados; sin contactos, perfiles ni secretos.

## Hechos

- Worker público: scheduler y cosecha diaria habilitados; escritura escolar no
  congelada. Ocho extractores escolares registrados, monitores activos en core.
- Sus `last_attempt_at` y cursores siguen en 27-08, no en la fecha del inventario.
  `is_allowed=true`, `robots_txt_ok=true`, cero bloqueos registrados para ellos.
- Barrido del 19-09: gastrojob termina a 12:34:34 UTC, stelle_admin 12:35:47,
  TES 12:36:39; a 13:03:11 el límite blando interrumpe el barrido. El siguiente
  scraper en el registro es schuljobs; las fuentes restantes no se alcanzan.
- Providers: límite blando durante TheHub a 12:33:10; siete fuentes completadas.
  El pipeline descarga en paralelo pero persiste en orden fijo del registro.
- No se concluye que cada fuente antigua tenga el mismo fallo, ni que el core
  ya la sustituya. Último resultado `ok` no acredita ejecución reciente.

## Corrección local verificada

Ordenar por intento más antiguo (no por posición fija), usando la tabla de salud
existente. Scrapers: registrar el inicio antes de HTTP y confirmarlo sin avanzar
el cursor ni inventar éxito; si muere el proceso, la siguiente pasada alcanza
antes las fuentes todavía no intentadas. Providers: persistir primero los
resultados de las fuentes que no alcanzaron pasadas anteriores.

No ampliar límites, borrar cursores, desactivar compliance ni hacer llamadas
de correo como prueba. La corrección evita la inanición entre barridos, no
promete completar todos los portales dentro de uno ni arregla un portal bloqueado.
La descarga paralela de providers conserva su límite global; su sustitución por
scopes nativos sigue pendiente.

Cuatro regresiones fallaron con el código anterior. Tras la corrección:
**69 passed** (10,75 s), incluidos los pipelines reales con PostgreSQL y dos
barridos consecutivos interrumpidos. El primero no registra éxito; el segundo
alcanza el colegio antes que la fuente lenta. Los contadores de fallo y el
último éxito anterior se conservan. La suite BFF completa está pendiente en
este momento; no se presenta como canary de producción.

## Durables que el corte debe preservar

Inventario público: 2 perfiles y 2 vínculos core, 10 búsquedas, 1.207 notificaciones,
cero candidaturas en `job_applications`. Hay 18 marcas escolares: dos enlazadas
al corpus y 16 en cuarentena explícita; dos de estas últimas son positivas.
Cero marcas escolares sin observación core. No descartar la cuarentena ni exigir
una vacante sintética para hacer accionables esos estados.

No se ha desplegado la corrección ni ejecutado otro barrido como parte de esta
lectura. El punto 4 sigue abierto hasta verificar los sustitutos y el corte real.
