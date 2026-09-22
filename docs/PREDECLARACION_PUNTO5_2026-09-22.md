# Predeclaración del punto 5 — presupuestos, escenarios y baseline

Sellada el **2026-09-22 09:05 UTC**, antes de comparar ninguna variante. Los
presupuestos de §2 no se revisan después de ver qué variante gana; si alguno
hubiera que cambiarlo, se explica y se vuelve a medir todo bajo el criterio nuevo.

## 1. Entorno de referencia

| Concepto | Valor verificado |
|---|---|
| Release core productiva | `51be7576b3d749eb302c4aae1bab699749e3ffaf`, `core0050`, `authoritative: true` |
| Imágenes | core `point4-51be757`; BFF y worker públicos `point4-a0fb403`; Portfolio `e15-9f8c85a` |
| HEAD local | `ac81a59` (SwissJob). **`281cf54`, el flag de retirada CDC, está en HEAD pero NO desplegado** |
| Hardware | Celeron J1800, 2 núcleos, `MemTotal` 8.035.456 kB, `MemAvailable` 3.470.068 kB, swap 24 GB con 21,2 GB libres |
| Carga del host | loadavg 4.31 / 3.79 / 3.90 — **incluye otros servicios del NAS**, no sólo este proyecto |
| Límites de contenedor | Ninguno: `Memory=0`, `NanoCpus=0` en los seis servicios del proyecto |
| PostgreSQL | 16.14, extensiones `pg_trgm`, `vector`. `shared_buffers=128 MB`, `work_mem=4 MB`, `effective_cache_size=4 GB`, `random_page_cost=4` — **valores de fábrica, sin ajustar** |
| Topología | core en `swissjobhunter_r5_rehearsal` esquema `jobhunt` (el nombre «rehearsal» NO la hace desechable: es el core vivo); BFF en `swissjobhunter`; Portfolio en `proyecto` |
| Salud de cosecha | 8 rondas de `harvest.check_health` en 24 h, **0 con alertas** |

Volúmenes: BD 2.753 MB · `offer_revisions` 141.615 filas / 1.034 MB ·
`saved_search_observations` 436.008 · `source_listing_incarnations` 132.775 ·
`match_evaluations` 80.540 · `offer_embeddings` 56.150 · vacantes presentables
45.857 · `profile_vacancy_state` 35.092 · `integration_outbox` 73.651 ·
`school_job_details` 158 · `generated_documents` 2.
Feed servido: **1.800 / 1.800 / 1.799** items para los tres perfiles.

## 2. Presupuestos ratificados

Se adoptan los candidatos del encargo. No consta un SLO más específico ratificado
para estos recorridos; se citan como contrato de este cierre y nada más.

| Recorrido | Presupuesto |
|---|---|
| Lecturas autenticadas habituales en LAN (catálogo, feed, colegios, candidaturas, búsquedas), caché caliente, 1–2 sesiones | p95 extremo a extremo **≤ 2 s**, sin pérdida de resultados ni cambio de total/orden/filtros |
| Readiness y lectura ligera | p95 **≤ 1 s**; error nunca disfrazado de vacío |
| Primera lectura tras arranque (en copia) | **≤ 5 s** una vez el servicio está listo; arranque y carga de modelo se miden aparte |
| Escrituras locales/core sin LLM ni SMTP (en copia) | p95 **≤ 2 s** con persistencia e idempotencia correctas |
| Frontend, navegación representativa | contenido útil visible **≤ 3 s** en LAN |
| Fondo | la edad del pendiente no crece de forma sostenida a la tasa real; el ciclo siguiente progresa |

**Fuera de este presupuesto**: generación documental con LLM y recuperación de
backlog. No se aplica el umbral de 2 s a un PDF+LLM ni se aprueba midiendo su ACK.
Si al medirlos se incumple algo, se fija su presupuesto propio antes de comparar.

## 3. Escenarios y muestras

- Concurrencia **1 y 2** (portfolio de pocos usuarios). Concurrencia 4 sólo en
  copia, si hace falta margen. **No se sube la concurrencia en producción.**
- Producción: **canario secuencial acotado con pausa, 10–20 muestras por ruta**,
  sólo lectura. Su percentil es canario, no capacidad certificada.
- Copia autorizada: ≥100 observaciones por recorrido prioritario para p95 empírico,
  publicando n, errores, máximo y dispersión.
- Frío y caliente se informan **por separado**, declarando qué capa se enfría.
  **No se vacía Redis ni el page cache del NAS vivo.**
- La carga del resto del NAS es parte del entorno y se anota; **no se paran otros
  servicios para fabricar un benchmark favorable**.

## 4. Criterios de parada

Se detiene la carga de prueba ante: cualquier 5xx inesperado, timeout, OOM o
reinicio de contenedor, bloqueo de un usuario real, o degradación sostenida
frente a la referencia. Límites de seguridad fijados **ahora**: ninguna prueba
supera 2 sesiones concurrentes en producción, ninguna serie pasa de 20
peticiones por ruta, y se aborta si `MemAvailable` baja de 1 GB o si aparece
cualquier reinicio. Se retira sólo la carga propia; no se matan tareas productivas.

**No se fabrican** búsquedas, ofertas ni avisos en producción. No se llama a
proveedores facturables.

## 5. Baseline medida (antes de cualquier cambio)

Camino real `CoreMatching.results()` dentro del contenedor del BFF desplegado,
solo lectura, n=5 por caso:

| Perfil | limit | p50 | min | max | Peticiones internas al core | Items recorridos | Devueltos |
|---|---:|---:|---:|---:|---:|---:|---:|
| 49d283b9 | 20 | **9,332 s** | 7,463 | 47,246 | 18 | 1.800 | 20 |
| 49d283b9 | 100 | 9,592 s | 7,308 | 11,521 | 18 | 1.800 | 100 |
| b4619e85 | 20 | **12,865 s** | 8,230 | 25,441 | 18 | 1.799 | 20 |
| b4619e85 | 100 | 9,259 s | 7,147 | 10,292 | 18 | 1.799 | 100 |

**Incumple el presupuesto de 2 s por un factor de 5–6.** Pedir 100 cuesta lo
mismo que pedir 20: el coste no depende de lo devuelto sino del recorrido.

Desglose por fases (n=3): `_fetch_full_feed` **8,684–11,401 s** (85–90 % del
total); overlay y resto 0,9–2,1 s; resolución de identidad 0,010–0,611 s.

Costes unitarios en el core, medidos dentro de su contenedor:

| Operación | Coste |
|---|---|
| Una página de 20 del feed (`matching.feed`) | **0,029 s** |
| COUNT del total con feedback **correlacionado** | 1,075 – 9,265 ms ×10³ → **1,1–9,3 s** |
| COUNT del total con feedback **por lotes** | **0,404 – 0,930 s**, mismo total |
| Petición HTTP completa al core, por página | ~0,5 s (de los cuales ~0,03 s son SQL) |

## 6. Causa dominante identificada

Para servir 20 ofertas, `CoreMatching.results()` recorre el feed **completo** en
18 peticiones de 100 items, transforma los 1.800 y sólo entonces corta. El
recorrido existe porque el BFF necesita el `total` y `MatchesPageDTO` del core
**no lo trae** (`items` y `next_cursor` únicamente).

Las exclusiones locales que justificaban recorrer todo (accionabilidad y feedback
negativo) viven en la rama de `CORE_FEEDBACK_ENABLED = false`. En producción el
flag está **activo**, y esa rama devuelve un resultado por cada item sin excluir
ninguno: `total == len(items)`. Es decir, el recorrido completo hoy sólo sirve
para contar lo que el core puede contar en una consulta.

## 7. Hipótesis a validar, con su riesgo declarado

1. Añadir `total` a `MatchesPageDTO` (aditivo) calculado con el feedback **por
   lotes**, y hacer que el BFF recorra sólo las páginas necesarias para cubrir
   `offset + limit`. Esperado: de ~9–12 s a **< 1,5 s**.
   Riesgo: que el total del core y el del BFF difieran. **Se verifica con una
   regresión que compara ambos contra los datos reales**; la rama del flag
   apagado conserva el recorrido completo, porque allí sí hay exclusiones.
2. No se toca el orden, ni los filtros, ni el cursor, ni el contrato de la API
   de barrido que usan los productores.

## 8. Lo que este trabajo no hace

No toca `jobhunt.shadow.project`, el worker legacy público, la cosecha del
Portfolio, la ventana de admisión, las cotas de NAV/IrishJobs, la identidad CH
Media, el ranker, el holdout, los umbrales, el cron de retención ni la retirada
del slot. No reactiva fuentes ni repara el `canton` histórico. Ningún avance de
rendimiento justifica relajar un contrato funcional.
