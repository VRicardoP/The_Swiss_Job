# Punto 5 — matriz de aceptación (2026-09-24)

Cumple lo que el acta del 22-09 exigía en su §10: presupuesto fijado **antes**,
matriz única con **≥100 muestras** en copia, frío y caliente **separados**,
canario acotado en el NAS, y **veredicto por escenario** — cumple o queda
expresamente pendiente. Una mejora porcentual no sustituye ese veredicto.

Reproducible: `scripts/measure_point5.py`.

## Presupuesto (predeclarado, no renegociado al ver el resultado)

| Recorrido | Presupuesto |
|---|---|
| Lecturas habituales (`p95`) | **≤ 2 s** |
| Contenido útil en pantalla | **≤ 3 s** |

`limit=3000` es lo que pide `MatchPage` **en cada entrada** a la pantalla
principal: es la lectura habitual, no una exportación. El lote completo alimenta
cinco cosas (categorías y sus contadores, Watchlist, top score, matches ≥ 70 y
las tarjetas), así que bajarlo no es una optimización sino un cambio de
funcionalidad.

## La copia de medida no era fiel, y eso invalidaba medir aquí

Antes de medir nada hubo que arreglar el entorno. En local,
`/match/results` **ni siquiera pasaba por el core**: servía de la tabla legacy
(244 filas) porque `CORE_FEEDBACK_ENABLED` era `false`, no había fila en
`jobhunt_profile_map` y faltaba el enrutado `core_primary` en `jobhunt_routing`.
Medir así habría dado 0,047 s y no habría dicho nada del recorrido que se quiere
medir. Producción tiene las tres cosas; la copia las tiene ahora, con un perfil
del mismo tamaño (**1.800 ofertas**, igual que producción).

Efecto colateral que conviene recordar: poner `CORE_FEEDBACK_ENABLED=true` en el
`.env` puso **89 pruebas del BFF en rojo** de golpe. No era un defecto: la suite
heredaba el entorno del operador y su veredicto dependía de la máquina. Ahora
`conftest` declara su propia línea base.

## Matriz en copia — n=100, frío y caliente separados

El frío no es un adorno: la caché del recorrido vive **en proceso**, así que sólo
es frío la primera petición tras reiniciar el BFF.

| Fase | Recorrido | n | p50 | p95 | máx | Presupuesto | Veredicto |
|---|---|---|---|---|---|---|---|
| frío | pantalla-principal | 1 | 0,182 | — | 0,182 | ≤ 3 s | **CUMPLE** |
| frío | página-20 | 1 | 0,163 | — | 0,163 | ≤ 2 s | **CUMPLE** |
| frío | guardados | 1 | 0,057 | — | 0,057 | ≤ 2 s | **CUMPLE** |
| frío | catálogo | 1 | 0,241 | — | 0,241 | ≤ 2 s | **CUMPLE** |
| caliente | pantalla-principal | 100 | 0,084 | 0,138 | 0,145 | ≤ 3 s | **CUMPLE** |
| caliente | página-20 | 100 | 0,106 | 0,149 | 0,176 | ≤ 2 s | **CUMPLE** |
| caliente | guardados | 100 | 0,019 | 0,023 | 0,024 | ≤ 2 s | **CUMPLE** |
| caliente | catálogo | 100 | 0,033 | 0,036 | 0,042 | ≤ 2 s | **CUMPLE** |

El frío de la pantalla principal es 0,182 s **porque el calentamiento ya había
llenado la caché** antes de que llegara la petición. Sin calentamiento y con
páginas de 500 son 0,607 s; con páginas de 100, 1,668 s.

## Canario en el NAS — n=20, sólo lecturas

| Recorrido | p50 | p95 | Presupuesto | Veredicto |
|---|---|---|---|---|
| pantalla-principal | 4,478 | 8,067 | ≤ 3 s | **NO CUMPLE** |
| página-20 | 2,787 | 4,869 | ≤ 2 s | **NO CUMPLE** |
| guardados | 1,165 | 1,714 | ≤ 2 s | **CUMPLE** |
| catálogo | 1,259 | 1,954 | ≤ 2 s | **CUMPLE** |

(Medido antes del despliegue, con carga 9,43.)

## Atribución del p95 — medida, no deducida (§10.3)

Esto es lo que el acta dejaba «sin atribuir», y ya no lo está:

| Evidencia | Valor |
|---|---|
| CPUs del NAS | **2** |
| `load average` durante las medidas | **9,43 → 14,91** (4,7×–7,5× sobresuscrito) |
| Contenedor que más CPU consume | **`tinymediamanager`, 71,55 %** (30,61 % en otra muestra) — ajeno al proyecto |
| CPU de lo nuestro | `swissjob-postgres` 9,45 %, `swissjob-backend` 0,40 % |
| Memoria | 7.847 MB totales, 7.529 usados, **317 libres** |
| Trabajo PROPIO (método en proceso) | p50 **3,089 s**, mínimo 1,453 s |
| Extremo a extremo (HTTP) | p50 **4,645 s** |
| Diferencia atribuible a espera | **1,556 s** |
| El MISMO código en la copia | **0,084 s** |

El mismo recorrido cuesta 0,084 s en una máquina holgada y entre 1,45 s y 3,09 s
en el NAS. **No es el código: es la máquina.** Y el mayor consumidor de CPU no es
nuestro.

### Modelo de coste de una página del core, medido

| | Coste fijo por petición | Coste por elemento |
|---|---|---|
| Copia | **13 ms** | 0,15 ms |
| NAS | **292 ms** | 2,75 ms |

Con el modelo del NAS, 18 páginas de 100 = 18×0,292 + 1800×0,00275 = **10,2 s**,
que es exactamente el frío que midió el acta (10,196 s). Modelo validado.

## Qué se cambió, y qué efecto tuvo de verdad

**1. `MAX_PAGE_LIMIT` 100 → 500** (aditivo: quien pida ≤100 no nota nada).
El recorrido es **por cursor**, así que las páginas son secuenciales por diseño y
no se pueden solapar; la única palanca es pedir menos veces. Verificado en
producción: **4 peticiones de 500** en lugar de 18 de 100, y `limit=501` sigue
dando 400 — el tope se movió, no desapareció.

**Honestidad sobre el efecto:** en un A/B *controlado por carga* en el NAS
(configuraciones alternadas, mismo proceso, misma ventana, carga 10,27), el
recorrido completo dio **22,50 s con 18 páginas** y **19,53 s con 4**, con
muestras solapadas (16,7–28,0) y n=3. **La mejora de latencia NO es
significativa en esa máquina**; lo que es un hecho es el cambio estructural.

**2. Calentamiento del recorrido fuera de la petición** (§10.3-ter). Calienta
**sólo el recorrido**, nunca las vistas: dependen del overlay local y cachearlas
serviría estado rancio, que es la regresión que cerró T2. Sin leader-lock a
propósito — la caché vive en proceso, así que cada worker calienta la suya; en
producción arrancan los dos de gunicorn. Medido: no cuesta latencia (p50 7,735
con él, 7,991 sin él, dentro del ruido) y elimina el recorrido de la cara del
usuario tras cada reinicio.

## Veredicto

- **En hardware holgado el contrato SE CUMPLE**, en los cuatro recorridos, frío
  y caliente, con n=100.
- **En el NAS NO se cumple** en `pantalla-principal` y `página-20`, y queda
  **expresamente pendiente**. La causa está atribuida y no es el código.

Tres vías, y las tres son decisión del propietario:

1. **Dar CPU a lo que sirve**: 2 núcleos con carga 10-15 y un contenedor ajeno
   llevándose el 71 % no admiten arreglo por software. Límites/reservas de CPU o
   mover `tinymediamanager`. Es lo único que cierra el escenario tal cual está.
2. **Rediseñar la carga (§10.5)**: que el servidor calcule los cinco agregados y
   la pantalla pagine las tarjetas. Serviría ~20 ítems en vez de 1.800. Es un
   **cambio de funcionalidad**, y el acta ya dijo que no se cuela dentro de una
   corrección de rendimiento.
3. **Aprobar expresamente otro presupuesto** para este hardware. Sería una
   decisión nueva y registrada, no una aprobación retroactiva.

## Lo desplegado

`swissjob-core:point5-6286ca2` y `swissjob-backend:point5-6286ca2`.
`core-api` publica `ready` / `core0051` / `release 6286ca2` / `authoritative: true`;
el BFF, `/health` 200 y el calentamiento activo en sus dos workers. Copias
`.before` de ambos composes en `unification-e15-20260914/*.before-6286ca2`.

Tropiezo registrado: el primer intento de desplegar el BFF lo dejó sin arrancar
(`unable to find user app`) por construir la imagen con `backend/Dockerfile` en
vez de `backend/Dockerfile.prod`, que es el que crea ese usuario. Producción se
restauró en el acto con la imagen anterior y se reconstruyó bien. **Para el BFF,
producción se construye SIEMPRE con `Dockerfile.prod`.**
