# Plan de acción: fallos y consumo en SwissJob y ReactPortfolio

Para los agentes que ejecuten las correcciones del [análisis conjunto del 25 de noviembre](audits/ANALISIS_CONJUNTO_SWISSJOB_PORTFOLIO_2026-11-25.md). Ese documento da la evidencia de cada hallazgo; este dice qué cambiar, cómo probarlo, cómo desplegarlo y cómo saber que funcionó. Los identificadores F, R y S son los del análisis.

Cada tarea es independiente salvo donde se indica. Ejecuta **una tarea por sesión y por PR**, en el orden de las fases. Una tarea termina cuando su comprobación en producción da el valor esperado, no cuando se fusiona el PR.

## 0. Estado a 2026-11-26 — leer antes que el resto

Producción: núcleo y BFF `5d316c2`, frontend `303b16c`, Portfolio `7e3e65c`, base del núcleo en `core0063`.

| Parte | Estado |
|---|---|
| Tarea 0 (línea base) | Hecha: `docs/audits/mediciones/pgss-antes-2026-11-26.txt`; contador reiniciado el 26-11 a las 07:29 CET |
| Fases 1, 2.1 y 3 | Desplegadas |
| 1.1, reencolar títulos | Se reencolaron solo 945; la purga de D10 lo dejó sin objeto |
| 2.2 (plan de la consulta híbrida) | No ejecutada: con D1 esa consulta ya no corre. Solo vuelve a importar si se reactiva una política híbrida |
| Fase 4, D1 a D8 y D10 | Desplegadas. Detalle en `docs/COTAS_Y_DECISIONES.md` §O y en los PR #57 a #61 (SwissJob) y #12 y #13 (Portfolio) |
| D9 | Sin cambios: en 7 días ningún scope pasó de 9 min de los 30 del límite |
| **Fase 5 (limpieza)** | **Pendiente entera**, salvo la documentación de 5.8 |
| **Medición final** | **Pendiente**: desde el 27-11 a las 08:30. Mezclará los efectos de las fases 1 a 3 con los de la fase 4, desplegados el mismo día |

### Abierto, fuera de las fases

| Qué | Cuándo o condición |
|---|---|
| Copia diaria de los datos: no hay ninguna programada y no quedan los volúmenes originales (`ReactPortfolio/ops/nas-saturacion/propuesta-backup-ssd.md`) | Cuanto antes |
| Rotar la contraseña de `swissjob-redis`, el `SECRET_KEY` y la contraseña SMTP de Portfolio (expuestos el 24 y el 25-11) | Propietario |
| Volver a autorizar Gmail en el QTS: `sendmail` responde `invalid_grant` y el supervisor no avisa | Propietario |
| Borrar `/share/external/DEV3301_1/apps-ssd/backups/d3-retirada-2026-11-26` (datos personales) | **03-12-2026** |
| Comprobar la primera fusión nocturna: `merge_log`, `dedup_candidates` por `state`, log `dedup_merge:` | 27-11 |
| Comprobar el primer cierre de desaparecidas: `cerradas_desaparecidas` en el log de `archive_sweep` | Tras 3 barridos exhaustivos por fuente |
| Relleno de `title_translations`: 109.152 títulos descubiertos, 200 por pasada | Unos 6 días |
| El sumidero hace un INSERT y un DELETE inútiles por cada recosecha de una vacante fusionada (revalidación B-3, `harvest/sink.py`) | Sin fecha |
| Un test del núcleo falló una vez y pasó al repetir; no se guardó cuál | Sin fecha |
| `swiss_schools_vis` sin cosecha completa (F12 del análisis) | Sin fecha |
| Quitar `portfolio` de `CORE_DELIVERY_HTTP_DESTINATIONS` | Cuando no queden entregas pendientes a ese destino |

## Reglas

1. **Puerta de aprobación.** Los pasos marcados con 🔒 cambian producción o fusionan código. Antes de cada uno, enseña al propietario el diff o el comando exacto y espera su «sí» en ese turno. Crear ramas, commits en la rama y PR no necesita aprobación; fusionar y desplegar, sí.
2. **Parada.** Si un test falla por algo que no es tu cambio, si una comprobación da un valor distinto del esperado o si el código no coincide con lo que describe la tarea, detente e informa con la salida literal. Las líneas citadas se verificaron el 26-11 sobre `eeca872` (SwissJob) y `96623c6` (Portfolio); si han cambiado, localiza el bloque por su contenido.
3. **Cotas antes de tocar.** Antes de cada tarea, busca su tema en `docs/COTAS_Y_DECISIONES.md` (`grep -n -i`). Si lo que vas a cambiar figura como cota aceptada, detente e informa.
4. **Mínimo cambio.** Implementa lo que dice la tarea y nada más. Sin abstracciones ni ajustes configurables nuevos salvo que la tarea los pida.
5. **Test con control negativo.** Cada corrección lleva un test que falla sin el cambio. Compruébalo deshaciendo el cambio y viendo el rojo.
6. **Secretos.** No vuelques ficheros de configuración ni entornos de contenedores. Lee nombres de clave, cuenta coincidencias o compara huellas. Detalle en la memoria del proyecto y en la regla 9 de `docs/PLAN_CORRECCIONES_NAS_PENDIENTES_2026-11-25.md`.
7. **Producción en solo lectura** fuera de los despliegues. Nada de `UPDATE`, `DELETE` ni `ALTER` a mano salvo los pasos 🔒 que lo indican.
8. **Idioma.** Respuestas, documentación y comentarios de lógica no obvia en español; código e identificadores en inglés, salvo donde el fichero ya use español.

## Cómo se trabaja en cada repositorio

| | SwissJob | Portfolio (backend) |
|---|---|---|
| Ruta | `/home/lothar/Public/SwissJob` | `/home/lothar/Public/ReactPortfolio/backend` |
| Remoto y rama base | `github`, `main` | `github`, `main` |
| Tests del BFF | Receta de cuatro condiciones de `CLAUDE.md`, sección «Comandos clave» (venv en `.venv`, URLs a `127.0.0.1:5435` y `:6380`, `ALLOW_DEV_CREDENTIALS=true`, sin cargar el `.env` entero). 669 tests, unos 3 min | — |
| Tests del núcleo | Perfil de desarrollo con imagen de test, `CLAUDE.md` «Tests core», avisos OJO 1 a 4. Unos 8 min. No los lances a la vez que otra corrida contra la misma base | — |
| Tests de Portfolio | — | `python -m pytest tests/ -q` con su entorno (`backend/CLAUDE.md`, línea 105). 1.688 tests, `timeout = 15` por test |
| Tests de scripts | `.venv/bin/python -m pytest scripts -q -p no:cacheprovider` | — |
| Lint | `ruff` 0.15.14 de `.venv`: `ruff check` y `ruff format --check` | El que defina su CI (`.github/workflows/ci.yml`) |
| Despliegue 🔒 | `scripts/deploy_nas.sh core\|bff\|frontend` | `scripts/deploy_nas.sh` |

Condiciones de los dos scripts de despliegue: árbol limpio y `HEAD` igual a `github/main`, así que el PR tiene que estar fusionado antes. `core` recrea los cuatro contenedores del núcleo; `bff` recrea backend y worker juntos.

**Cuándo desplegar el núcleo.** Fuera de las cosechas (01:30, 07:30, 13:30, 19:30, 09:30, 21:30 y 23:30 de Zúrich) y de las tareas diarias de 05:20 a 06:40, con 45 minutos de margen después de cada una. El `date` del NAS imprime CET; `TZ=Europe/Zurich` no funciona allí.

**Después de cada despliegue.** Comprueba con `docker ps` que no hay contenedores `unhealthy` y, si desplegaste el núcleo, que `/v1/ready` responde `ready` y `authoritative: true` (`scripts/check_core_release.py`). Si el despliegue cambió un compose del NAS, regenera su copia versionada: en Portfolio con `scripts/sync_nas_compose.py`; en SwissJob a mano y con `python3 scripts/check_no_secrets.py deploy/nas` en verde.

**Acceso al NAS.** `ssh -o ConnectTimeout=60 nas`. Dentro:

```bash
D=/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
export DOCKER_CONFIG=$(mktemp -d)
q() { $D exec -i swissjob-postgres psql -h 127.0.0.1 -U swissjob -d "$1" -AtF'|'; }   # q swissjobhunter_r5_rehearsal <<'S' … S
```

Las tablas del núcleo están en el esquema `jobhunt`: empieza cada consulta con `set search_path=jobhunt,public;`. Pasa el SQL por *heredoc* con comillas (`<<'S'`); las comillas anidadas a través de SSH se rompen.

## Línea base (25-11, 23:10 a 23:40 CET)

Son los valores con los que se compara cada tarea.

| Indicador | Valor | Cómo se mide |
|---|---|---|
| Tiempo de ejecución de PostgreSQL | 135.695 s en 375.042 s (36 % de un núcleo) | `pg_stat_statements`, suma de `total_exec_time` |
| Consulta híbrida | 170 llamadas, media 214 s, mínimo 109 s | `query like 'WITH ann AS MATERIALIZED%'` |
| Respaldo exacto | 85 `SET LOCAL enable_indexscan = off` | `query ilike '%enable_indexscan = off%'` |
| Facetas del feed | 9.905 llamadas, media 2,3 s | `query like 'WITH fb AS%'` con `category_count_` |
| Versión del feed | 29.963 llamadas, media 0,44 s | `query like 'WITH fb AS%'` con `md5(` |
| Ofertas sin embedding | 1.139 llamadas, media 8,5 s | `query like 'SELECT DISTINCT ON (o.text_hash)%'` |
| Peticiones al núcleo en 24 h | 23.879, de ellas 17.128 de `profile-erasures` | `docker logs --since 24h swissjob-core-api-r5` |
| Errores de `portfolio_worker` en 24 h | 2.456 (1.132 respuestas 429, 193 timeouts) | `docker logs --since 24h portfolio_worker` |
| Resúmenes pendientes en Portfolio | 8.010, el más antiguo del 6-11 | `job_enrichments`, `kind='summary' and value is null` |
| Títulos de Portfolio | 15.839 vacíos, 2.387 traducidos | `job_enrichments`, `kind='title_en'` |
| Calentador del BFF | `traducidos: 200` en 282 de 284 pasadas | `docker logs --since 24h swissjob-backend \| grep calentamiento` |

Consulta de referencia para el tiempo de base de datos:

```sql
select calls, round(total_exec_time/1000) total_s, round(mean_exec_time) mean_ms,
       round(100*total_exec_time/sum(total_exec_time) over ()) pct,
       left(regexp_replace(query, '\s+', ' ', 'g'), 120)
from pg_stat_statements order by total_exec_time desc limit 12;
```

### Tarea 0. 🔒 Poner el contador a cero

`pg_stat_statements` acumula desde el 21-11. Para medir el efecto de las fases 1 y 2 hay que reiniciarlo una vez, antes de empezar:

1. Guarda la salida de la consulta de referencia en `docs/audits/mediciones/pgss-antes-<fecha>.txt` (sin parámetros no lleva datos personales).
2. Tras el «sí»: `select pg_stat_statements_reset();` y anota la hora.

Después de cada fase, repite la consulta al cabo de al menos 24 horas y guarda la salida junto a la anterior.

## Fase 1. Parar el desperdicio

Cinco cambios pequeños que no alteran lógica de negocio.

### 1.1 Portfolio: la cola de enriquecimiento (F1 y F3)

**Problema.** `run_pending` (`services/enrichment.py:267-309`) tiene dos defectos:

- Si un resumen falla, hace `break` y la misma fila, que es la más antigua, se reintenta al minuto siguiente. La cuota de Groq es rodante (200.000 tokens al día para `openai/gpt-oss-20b`): libera unos 139 tokens por minuto, así que casi todas las llamadas reciben un 429 que dice «inténtalo en 1-2 minutos».
- Para los títulos guarda `translated.get(t, "")` para todo el lote. Cuando el traductor falla y devuelve un resultado parcial, los títulos que faltan quedan marcados como «no necesita traducción» para siempre.

**Cambio.**

1. En `services/groq_service.py`, donde se construye el cliente (`Groq(api_key=…)`, hacia la línea 71 o inmediatamente después de la clase), pasa `timeout=25.0, max_retries=0`. El SDK reintenta dos veces por defecto y el `wait_for` de 30 s abandona la llamada sin cancelarla.
2. En `run_pending`, distingue el fallo por límite de tasa: ante un 429, deja de pedir resúmenes durante 15 minutos (una marca de tiempo a nivel de módulo, `time.monotonic()`), y regístralo una sola vez con nivel `INFO`. Mira cómo expone `GroqUpstreamError` el código de estado antes de decidir cómo detectarlo.
3. Ante cualquier otro fallo de un resumen, pasa a la fila siguiente en vez de parar, con un máximo de 2 fallos por pasada.
4. Para los títulos, guarda solo los que el traductor **decidió**: los que tradujo y los que descartó por idioma. Lee `translate_titles` en `services/translation_service.py` (bloque de las líneas 150 a 180) y haz que devuelva, además del diccionario, el conjunto de títulos decididos; ante un fallo del proveedor o una respuesta ilegible ese conjunto no incluye el lote. `run_pending` escribe solo esas claves.

**Pruebas.** En `tests/test_enrichment.py`, que ya instala un LLM falso:

- Un 429 en el primer resumen: la pasada no intenta más resúmenes y la siguiente pasada, dentro de la pausa, no llama al LLM.
- Un timeout en el primer resumen: el segundo se procesa.
- El traductor falla: ningún título del lote queda con `value = ''`.

**Comprobación en producción**, 24 horas después del despliegue:

```bash
$D logs --since 24h portfolio_worker 2>&1 | grep -c "Rate limit reached"      # esperado: menos de 100 (eran 1.132)
$D logs --since 24h portfolio_worker 2>&1 | grep -c "Retrying request"        # esperado: 0
```

**Paso aparte 🔒: reencolar los títulos envenenados.** Con el cambio desplegado y verificado, en `portfolio_db`:

```sql
update job_enrichments set value = null where kind = 'title_en' and value = '';
```

Son 15.839 filas; a 25 por minuto el bucle las repasa en unas 10 horas. Antes de ejecutarlo, confirma leyendo el traductor que los títulos en inglés o español se descartan sin llamar al LLM; si no es así, informa del coste antes de pedir el «sí».

**No resuelve** el desajuste de fondo: la cuota da para unos 230 resúmenes al día y hay 8.010 pendientes. Eso es la decisión D4.

### 1.2 BFF: intervalo del reconciliador de borrados (R6)

**Problema.** `run_erasure_delivery` (`backend/services/profile_erasure.py:235-246`) duerme 30 s. Corre en los dos procesos de gunicorn y en `swissjob-erasure-cdc`, y cada pasada lee los recibos del núcleo y reescribe su acuse: 17.128 peticiones al día para un único recibo.

**Antes de cambiar.** Busca en `docs/COTAS_Y_DECISIONES.md` y en `docs/BORRADO_COORDINADO_E13.md` si hay un plazo máximo comprometido para propagar un borrado. Si lo hay y es menor de 15 minutos, usa ese plazo.

**Cambio.** Una constante con nombre en ese módulo, `ERASURE_POLL_SECONDS = 900`, en lugar del `30` literal. Nada más: el bloqueo entre procesos no compensa el código que añade.

**Pruebas.** Busca tests que dependan del valor (`grep -rn "sleep(30)\|run_erasure_delivery" backend/tests`). Si ninguno lo fija, basta la suite completa.

**Despliegue.** `scripts/deploy_nas.sh bff` actualiza backend y worker. `swissjob-erasure-cdc` usa la misma imagen pero es otro proyecto de compose (`swissjob-erasure`, definición en `deploy/nas/erasure.configured.yml`, que documenta su comando de recreación): cambia su etiqueta de imagen y recréalo como paso 🔒 aparte.

**Comprobación**, una hora después:

```bash
$D logs --since 1h swissjob-core-api-r5 2>&1 | grep -c "profile-erasures"     # esperado: menos de 40 (eran unas 710 por hora)
```

### 1.3 Núcleo: no buscar embeddings pendientes si el corpus no cambió (R4)

**Problema.** `jobhunt.matching.recover` corre cada 5 minutos y empieza por `_drain_embeddings` (`jobhunt_core/tasks/matching.py:126-145`), que llama a `pending_offer_texts` (`jobhunt_core/embeddings.py:234-253`). Esa consulta es un anti-join sobre las 313.000 revisiones: 8,5 s para devolver casi siempre cero filas.

**Cambio.** Recuerda en memoria del proceso, por modelo, la `corpus_generation` en la que `pending_offer_texts` devolvió cero filas, y no la ejecutes para ese modelo mientras la generación sea la misma. Condiciones:

- La marca es por `model_id`: un modelo recién activado no tiene marca y se consulta.
- Solo se salta la consulta de **ofertas**. `pending_profile_revisions` es barata y un perfil nuevo no cambia la generación: se sigue ejecutando siempre.
- La generación se lee de la tabla `corpus_generation` (fila `id = 1`), como ya hace `_recovery_targets` en ese fichero.
- Si la consulta devolvió filas, no se marca.

Antes de implementarlo, confirma leyendo las migraciones `core0022` y `core0023` que toda alta de revisión vigente con texto nuevo sube la generación. Si hay un camino que cree texto sin subirla, detente e informa.

**Pruebas.** En `jobhunt_core/tests/`, junto a los de recuperación (`test_empty_feed_recovery.py`, `test_integration_native_recovery.py`):

- Dos drenajes seguidos sin cambios: la segunda vez no se ejecuta la consulta de ofertas.
- Sube la generación: se vuelve a ejecutar.
- Una oferta sin vector con la generación recién subida se embebe.

**Comprobación**, 24 horas después del despliegue (con el contador de la tarea 0 a cero):

```sql
select calls, round(mean_exec_time) from pg_stat_statements where query like 'SELECT DISTINCT ON (o.text_hash)%';
-- esperado: decenas de llamadas al día, no 262
```

### 1.4 BFF: el calentador (R5 y F4)

Dos defectos en `backend/services/matching/warm.py`. El calentador debe seguir corriendo en los dos procesos de gunicorn: la caché del feed es de cada proceso.

**R5. Calienta a usuarios que no entran.** `calentar_una_vez` (línea 38) recorre todos los enrolados cada 10 minutos. En 24 horas el BFF no recibió ni una petición de usuario y pidió al núcleo 1.732 versiones, 568 facetas y 554 páginas de feed.

*Cambio.* Marca la actividad del usuario en Redis y calienta solo a los marcados:

- En los manejadores de `GET /match/results` y de facetas de `backend/routers/match.py`, escribe `feed:active:<user_id>` con caducidad de 24 horas. Usa el cliente de Redis que el router ya tiene; si no hay Redis, no hagas nada.
- En `calentar_una_vez`, filtra `usuarios` con un `MGET` de esas claves. Si Redis no está disponible, calienta a todos, como hoy.

Consecuencia aceptada: la primera carga de un usuario que vuelve tras más de 24 horas va en frío. Dilo en el PR.

**F4. La traducción de fondo no avanza.** `_traducir_en_fondo` (líneas 75 a 106) toma los 100 primeros títulos sin clave en Redis. Los que están en inglés o español nunca se cachean, así que ocupan la ventana en todas las pasadas.

*Cambio.* Que los títulos descartados por idioma dejen de ser «pendientes». Lee `translate_titles` en `backend/services/translation_service.py` (bloque de las líneas 150 a 180) y elige la opción de menor diff:

- cachear también la decisión de no traducir, guardando el propio título como su traducción, **solo** para los títulos que el traductor descartó por idioma y nunca cuando falló el proveedor; o
- excluir antes del corte de 100 los títulos cuyo idioma resuelto esté en `SKIP_LANGUAGES`, moviendo `language_store.lookup` por delante del corte.

La primera cubre también los títulos de idioma desconocido. Sea cual sea, un fallo del proveedor no debe marcar nada.

**Pruebas.**

- Con 150 títulos, los 100 primeros en inglés: dos pasadas seguidas piden al traductor títulos distintos, y los 50 restantes se traducen.
- El proveedor falla: Redis queda sin claves nuevas.
- Un usuario sin marca de actividad no se calienta; con marca, sí; sin Redis, se calientan todos.
- Los tests existentes del camino de respuesta siguen instalando un Groq que lanza: no los relajes.

**Comprobación**, 24 horas después:

```bash
$D logs --since 24h swissjob-backend 2>&1 | grep -o "calentamiento del feed: {.*}" | sed -E "s/'usuarios': [0-9]+, //" | sort | uniq -c
# esperado: 'calentados': 0 mientras nadie entre, y 'traducidos' decreciendo hasta 0 cuando alguien entra
$D logs --since 24h swissjob-core-api-r5 2>&1 | grep -c "matches/facets"     # esperado: cerca de 0 sin usuarios (eran 568)
```

### 1.5 Portfolio: páginas de 500 al leer el feed (R10, primera parte)

**Problema.** `services/matching/core_client.py:82` fija `_MAX_PAGE_LIMIT = 100` citando una constante del núcleo que hoy vale 500 (`jobhunt_core/api/v1.py:65`). El feed se drena entero cada hora (`services/match_feed.py`, `REFRESH_INTERVAL_SECONDS = 3600`), en unas 18 peticiones.

**Cambio.** `_MAX_PAGE_LIMIT = 500` **solo** en `services/matching/core_client.py`. Los otros dos clientes con la misma constante (`saved_searches`, `applications`) hablan con rutas distintas: no los toques sin comprobar el límite de cada ruta.

**Pruebas.** `tests/fake_core_v1.py` es el núcleo falso: comprueba si impone un máximo de 100 y ajústalo al contrato real. Suite completa.

**Comprobación**, tras una hora:

```bash
$D logs --since 1h swissjob-core-api-r5 2>&1 | grep "172.29.16" | grep -c "/matches?"   # esperado: 4-5 por refresco (eran unas 18)
```

La segunda parte, saltarse el drenaje cuando la versión no cambió, es la tarea 3.8.

## Fase 2. El respaldo exacto del matching híbrido (R1)

Es el mayor ahorro individual: el 27 % del tiempo de base de datos.

### 2.1 Corregir el recuento

**Problema.** `_fetch_candidates` (`jobhunt_core/matching.py:1602-1620`) repite la consulta con `enable_indexscan` y `enable_bitmapscan` desactivados cuando `_semantic_arm_filled` (líneas 984 a 995) dice que el brazo semántico no se llenó. Esa función cuenta las filas con `semantic_rank` en el resultado **ya fusionado y cortado** por `LIMIT :k`. Como las filas solo-léxicas ocupan parte de ese corte, la cuenta nunca alcanza `target` y el respaldo se dispara siempre: 85 de 85 evaluaciones en la línea base.

**Cambio.**

1. En las consultas híbridas (`HYBRID_CANDIDATES_SQL`, cuyo `SELECT` final está en las líneas 141 a 155, y `HYBRID2_CANDIDATES_SQL`), añade una columna con el tamaño real del brazo semántico: `count(a.vacancy_id) OVER () AS ann_rows`. Una función de ventana se evalúa antes del `LIMIT`, así que cuenta las filas del CTE `ann` y no las del corte.
2. `_semantic_arm_filled` compara ese valor con `target`. Con el resultado vacío, devuelve `False` como hoy.
3. Cuando se cae al respaldo, regístralo con nivel `WARNING` e indica `ann_rows`, `target` y el perfil. Hoy es mudo.

Comprueba antes quién consume las filas: si algún sitio las desempaqueta por posición en lugar de por nombre, la columna nueva lo rompe. Hay tests que fijan el texto del SQL (*goldens*): actualízalos en el mismo PR y explica en él por qué cambian.

**Pruebas.** En `jobhunt_core/tests/test_matching.py` o `test_integration_matching.py`:

- Un corpus donde el brazo semántico devuelve `target` filas y además hay filas solo-léxicas mejor situadas: el respaldo no se ejecuta. Sin el cambio, sí.
- Un corpus donde el brazo semántico queda corto de verdad: el respaldo se ejecuta y queda registrado.
- Los candidatos devueltos en el primer caso son los mismos que antes del cambio.

**Comprobación**, 24 horas después del despliegue:

```sql
select calls, left(query, 60) from pg_stat_statements where query ilike '%enable_indexscan = off%';
-- esperado: 0 o muy pocas (eran la mitad de las ejecuciones híbridas)
select calls, round(mean_exec_time/1000) mean_s, round(max_exec_time/1000) max_s
from pg_stat_statements where query like 'WITH ann AS MATERIALIZED%';
```

```bash
$D logs --since 24h swissjob-core-worker-r5 2>&1 | grep -c "respaldo"          # cuenta de caídas al respaldo, con el texto que hayas puesto
```

### 2.2 Entender por qué la ejecución «buena» tarda 109 s

El mínimo de la línea base es 109 s: incluso sin respaldo, la consulta es lenta. Esta tarea es de investigación y **no cambia código**.

1. Con 2.1 desplegada, obtén un `EXPLAIN (ANALYZE, BUFFERS)` de una ejecución real, con los parámetros de un perfil de producción. Hazlo desde el contenedor `swissjob-core-worker-r5`, reutilizando el código que construye los parámetros, fuera de las ventanas de cosecha. Es un paso 🔒: carga la base durante uno o dos minutos.
2. Guarda el plan en `docs/audits/mediciones/`.
3. Informa de dónde se va el tiempo. Tres sospechas, ninguna confirmada: el `NOT EXISTS` sobre `profile_exclusions` con `ILIKE` evaluado por fila; la rama léxica, que no usa `ix_offrev_search_document` (263 MB, cero usos); y los 92 GB de temporales con `work_mem` de 4 MB.

**Hecho cuando** el propietario tiene el plan y una propuesta con su ahorro estimado.

## Fase 3. Lo que está roto o trabaja en balde

Tareas pequeñas e independientes. Agrupa en un PR solo las que tocan el mismo fichero.

| | Tarea | Dónde | Cambio | Prueba |
|---|---|---|---|---|
| 3.1 | **F2. Informe diario de Portfolio** | `services/daily_match_report.py:279-284` | Quitar `matcher=None, groq=None, cv_svc=None` de la llamada: `analyze` solo recibe `params` (`services/matching/core_client.py:203`) | Un test que **no** parchee `_load_core_matches` y llegue al núcleo falso. En producción: `grep "Daily match report"` en `portfolio_worker` tras las 06:00 UTC, sin traza |
| 3.2 | **F6. Botón «Find new matches»** | `backend/routers/match.py:72-93`; `frontend/src/pages/MatchPage.jsx:237,334`; `useAnalyze`, `matchApi.analyze`, esquemas `MatchAnalyze*` | Borrar el botón, el hook, la llamada, el endpoint y los esquemas. Despliegue: `bff` y `frontend` | `MatchPage.firstrender.test.jsx` y los tests del frontend en verde; `grep -rn "analyze" frontend/src backend/routers/match.py` sin restos |
| 3.3 | **F7. Atajo por categoría** | `backend/services/matching/core_client.py:913-916` | Cuando la versión declarada coincide con la cacheada, sellar `_feed_verified_at[pid]` igual que lo hace `_remember_feed` (hacia la línea 1105) | En `tests/test_feed_version_cache.py`: tras una coincidencia de versión, una lectura por categoría pasado el plazo de frescura no va al núcleo |
| 3.4 | **R14. Feedback implícito** | `backend/services/matching/feedback.py:158-170` | Limpiar la caché y publicar en el bus solo cuando el evento es feedback explícito. Antes, confirma en `jobhunt_core/feedback.py` que el implícito no mueve la versión | Un evento implícito no vacía `_feed_cache`; uno explícito, sí |
| 3.5 | **F10. Limpiezas diarias de Portfolio** | `app/scheduling.py:90,103,116,129` | Sustituir `initial_delay_s=CLEANUP_INTERVAL_SECONDS` por retardos de 10, 12, 14 y 16 minutos | El test de programación que haya; si no hay, uno que compruebe que el retardo inicial es menor que una hora |
| 3.6 | **F11. Panel de fuentes** | `services/job_sources_feed.py:136-160` | Si ninguna fuente respondió, lanzar sin escribir el sobre, para que se conserve la copia anterior y actúe `JOB_SOURCES_FEED_RETRY_SECONDS` | Con el núcleo falso caído, la clave `dashboard:job_sources_v1` conserva su valor |
| 3.7 | **A-09. Presupuesto de los clientes del BFF** | `backend/services/schools/http_client.py:55-56,78-79`; `documents/core_client.py:113-114`; `saved_searches.py:78-79` | Usar `CORE_FEED_TOTAL_BUDGET_S`, que ya existe y vale 60 s, en lugar de `max(CORE_HTTP_TIMEOUT_SECONDS, 1) * 2` | Los tests de esos tres clientes; uno que fije el presupuesto total |
| 3.8 | **R10, segunda parte. Versión del feed en Portfolio** | `services/match_feed.py` (`refrescar`), `services/matching/core_client.py` | Antes de drenar, pedir `GET /v1/profiles/{id}/matches/version`; si coincide con la guardada en el sobre de Redis, renovar solo la caducidad. Sin versión fiable, drenar como hoy | Dos refrescos sin cambios: el segundo hace una petición. Con versión distinta, drena. Añadir la ruta al núcleo falso |
| 3.9 | **R15. Trabajos sin demanda de Portfolio** | `app/scheduling.py:20-21`; `services/job_sources_feed.py` | Avisos de colegios cada 30 min en vez de 5. El refresco del panel de fuentes, solo si `/dashboard/job-sources` se pidió en las últimas 6 horas (marca en Redis, como en 1.4) | Sin marca no se refresca; con marca, sí |

`swiss_schools_vis` (F12) no es de este plan: lleva más de 7 días sin cosecha completa y necesita que alguien mire su productor.

## Fase 4. Decisiones del propietario

No ejecutes nada de esta fase sin una instrucción expresa. Para cada decisión, prepara lo que se indica y espera.

| | Decisión | Qué preparar | Si se aprueba |
|---|---|---|---|
| D1 | **R2. ¿Sigue haciendo falta la segunda política de matching?** Dos políticas evalúan a la par, unas 230 veces al día cada una | `select id, name, prompt_version, active from scoring_policies;` y, leyendo `matching.py:1195-1206`, cuál es la canónica | Desactivar la sombra con `policy_ctl`. Reduce a la mitad el trabajo de la fase 2 |
| D2 | **R3. ¿Se retira el barrido de dedup?** Su tabla solo se escribe; es el 11 % del tiempo de base de datos | Confirmar con `grep` que nada lee `dedup_candidates` ni escribe `merged_into` | Quitar `maintenance-dedup-scan` del beat (`jobhunt_core/celery_app.py:181`). La tabla y el código se conservan |
| D3 | **R7 y S6. ¿Se retiran el esquema heredado y `swissjob-erasure-cdc`?** El contenedor apunta a la base del núcleo y solo protege las tablas `public` copiadas del legado (501 MB) | Lista de tablas de `public` con tamaño y filas; qué lee `reconcile_replica` de ellas; volcado previo | Volcado, `DROP` de las tablas heredadas y de `shadow_change_log`, y retirada del contenedor y de su entrada en el supervisor. Es un borrado: lista exacta y confirmación |
| D4 | **F1. ¿Qué resúmenes quiere Portfolio?** La cuota da para unos 230 al día y hay 8.010 pendientes | Cuántas ofertas distintas se muestran al día en el panel | Encolar resúmenes solo para lo que se muestra, o cambiar de modelo o de proveedor (Portfolio no tiene clave de Gemini) |
| D5 | **F5. ¿Qué se hace con el buzón de Portfolio?** 13.659 eventos que nada lee | Recuento por `event_type` | Purga por antigüedad, o dejar de emitir a `portfolio` desde el núcleo |
| D6 | **F8. ¿Se relajan los receptores a `extra="ignore"`?** Cuatro modelos en `backend/routers/integration_inbox.py` y dos en `core_client.py:196,205` | Comprobar si la validación estricta figura como cota | Cambiar a `ignore` y dead-letter inmediato para los 4xx que no sean 408 ni 429 (`jobhunt_core/tasks/delivery.py:119-131`) |
| D7 | **R9. ¿Se cierran las ofertas que desaparecen del portal?** El 87 % de las encarnaciones activas no se ve desde hace más de 7 días | Histograma de `now() - last_seen_at` **por fuente**, separando las de barrido completo de las incrementales | Diseño aparte: es un cambio de contrato (ADR-07) |
| D8 | **R8. Reescritura de revisiones sin cambios** en la cosecha (`harvest/sink.py:1504-1514`) | `n_tup_upd` y `n_tup_hot_upd` de `source_listing_revisions` en 24 horas | Diseño aparte: toca el sumidero de la cosecha |
| D9 | **F9. `SoftTimeLimitExceeded` como fallo reintentable** (`tasks/harvest.py:92-95`, `harvest/runner.py:140-151`) | Cuántas veces ocurre: `grep -c SoftTimeLimit` en el worker de cosecha, 7 días | Diseño aparte: al relanzar hay que seguir liberando la reserva del scope |
| D10 | **R17. ¿Se traduce una sola vez en el núcleo?** BFF y Portfolio traducen el mismo corpus con la misma clave | — | Diseño aparte: `title_en` aditivo en `VacancyDTO` |

## Fase 5. Limpieza

Después de las fases 1 a 3, un PR por punto. Antes de borrar algo, confirma con `grep` en los dos repositorios y en los dos frontends que no tiene llamador.

| | Qué | Dónde | Cuidado |
|---|---|---|---|
| 5.1 | **S1. Rutas por fuente de Portfolio sin cliente** | `routers/core_source_compat.py`, `routers/jobicy_jobs.py`, `routers/jsearch_jobs.py` salvo `/search`; en `services/core_source_compat.py`: `aggregate`, `dimension`, `stats`, `remote_stats`, `health`, `jsearch_*`, `jobicy_dimension`, `jobicy_stats`, `_summary`, `_counts` | El frontend usa `/dashboard/job-sources`, `/jobs/search` y `/jsearch-jobs/search`: esas tres se quedan |
| 5.2 | **S2. `folium` y la ruta `/mapa-web-dinamico`** | `routers/analytics.py:19,206`; `requirements.txt`: `folium`, `branca`, `xyzservices`, `numpy` | `requests` solo si nada más lo importa |
| 5.3 | **S2. Restos del motor local de AI Match** | `routers/ai_match.py:33,149,157`; `frontend/src/hooks/useAnalysisRun.js:38-54,90-106`; `useSSEEvents.js:96` | El frontend es otro repositorio y se despliega solo al hacer push |
| 5.4 | **S4. Menudencias del BFF** | `orjson` en `backend/requirements.txt:19`; `weights_used` en `backend/routers/match.py:295-300,333-338,432-437`; `send_watchlist_digest` y su cita (`tasks/watchlist_tasks.py:286-414`, `services/scheduler.py:164-169`); la rama de re-ranking de `groq_service.py` (DT-148 B1) | `check_source_ownership.py` vigila dependencias: ejecútalo |
| 5.5 | **S5. Tareas del núcleo sin llamador** | `run_all_task` y `_run_all_impl` (`tasks/harvest.py:129-215`), `run_profile_task`, `run_pending_task`, `ping`; la ruta `jobhunt.notifications.*` y la cola `core.embedding` (`celery_app.py:54,62`) | Busca también invocaciones por nombre de tarea en `docs/` y `scripts/` |
| 5.6 | **S3. Ramas de `CORE_FEEDBACK_ENABLED=False`** | `_results_with_local_overlay` (`core_client.py:697-777`), `match_result_service.py`, ramas locales de `pattern_analysis_service` y `watchlist_state`; respaldo `limit=3000` del frontend | La más larga: `conftest.py` fija el flag a `False` y arrastra muchos tests. Hazla la última y sola |
| 5.7 | **S7. Variables de entorno sin lector** | `CORE_CONSUMER_KEY`, `CORE_API_BASE_URL` y `CORE_HTTP_TIMEOUT_SECONDS` en el servicio `postgres`; `POSTGRES_*` en el BFF; `SCHEDULER_DAILY_HARVEST_ENABLED` en `erasure-cdc` | 🔒 Toca composes de producción. Las de `postgres` vienen de `.env.postgres.runtime` y quitarlas obliga a recrear PostgreSQL: espera a una ventana que ya esté prevista |
| 5.8 | **Documentación** | `CLAUDE.md` de SwissJob: A-03, A-19 y A-68 cerrados; los correos del BFF sí salen; la regla «núcleo antes que BFF» por `profile` ya no tiene quien lo envíe. `ReactPortfolio/backend/CLAUDE.md`: módulos que no existen | Lee `~/.claude/skills/writing-for-agents/SKILL.md` antes de editar un `CLAUDE.md` |

## Medición final

Al terminar las fases 1 a 3, y al menos 48 horas después del último despliegue:

1. Repite la consulta de referencia y todos los indicadores de la línea base.
2. Guarda la salida en `docs/audits/mediciones/` y añade al análisis una tabla «antes y después» con las mismas filas que la línea base.
3. Informa al propietario de qué tareas no dieron el valor esperado, con su salida literal.

No atribuyas a una tarea una mejora que coincida con otro cambio desplegado en la misma ventana: dilo.

## Informe de cada tarea

Termina cada sesión con un mensaje al propietario que contenga, en este orden:

1. Qué tarea era y si quedó hecha, a medias o bloqueada.
2. El enlace al PR y el resultado de los tests, con el control negativo.
3. El valor esperado y el medido en producción, o cuándo se podrá medir.
4. Cualquier cosa que no coincidiera con este plan, con su salida literal.
