# DOC 1 — Blueprint: qué es JobHunting y hacia dónde va

> **Nivel 1 de 3.** Este documento explica el problema, las decisiones que definen la
> arquitectura y cómo encajan las piezas. No describe componentes uno a uno (eso es
> [DOC 2 — Componentes](DOC_2_COMPONENTES_2026-08-27.md)) ni explica mecanismos por
> dentro (eso es [DOC 3 — Detalle técnico](DOC_3_DETALLE_TECNICO_2026-08-27.md)).
>
> **Fecha:** 2026-08-27 (mañana) · **Ámbito:** `SwissJob` (`feat/fase-a-core`, HEAD `de56ae0`) y
> `ReactPortfolio` (`main`, backend HEAD `73d9212`).
>
> ⚠ **ACTUALIZADO la tarde del 2026-08-27.** El documento se escribió por la mañana y esa
> misma tarde ocurrieron tres cosas que lo invalidaban por partes: se **ejecutaron** las dos
> maniobras que §6 daba por pendientes, se **arregló y desplegó** el `not_ready` de §5, y una
> **auditoría externa** (veredicto NO-GO) encontró cuatro hallazgos más. Los pasajes
> afectados llevan ahora un bloque **ACTUALIZACIÓN**; el texto original se conserva porque
> el razonamiento sigue valiendo. Estado consolidado y vigente:
> `ESTADO_Y_HOJA_DE_RUTA.md` **§20**.
>
> **Marcas de verificación.** `[V]` = comprobado por mí leyendo el código o ejecutando
> contra el sistema vivo el 2026-08-27. `[I]` = procede de un informe y **no** lo he
> vuelto a medir. La distinción no es cosmética: ver §7.

---

## 1. El problema

Buscar trabajo cualificado en Suiza tiene tres fricciones que ningún portal resuelve solo:

1. **La oferta está fragmentada.** No hay un portal dominante: hay portales generalistas,
   portales cantonales, portales sectoriales (salud, gastronomía, academia, finanzas,
   educación) y las páginas de empleo de cada empleador. Un candidato serio revisa
   docenas de sitios.
2. **Está en cuatro idiomas.** La misma vacante puede publicarse en alemán en un portal y
   en francés en otro. Buscar «profesor de primaria» en español no encuentra
   *Primarlehrperson* ni *enseignant primaire*.
3. **El filtrado por palabras clave no funciona.** Los títulos suizos son compuestos
   (*Sachbearbeiter Finanzbuchhaltung*), los sinónimos abundan y la relevancia real
   depende del perfil del candidato, no de que aparezca una palabra.

**Para quién.** Hoy, un puñado de usuarios reales: 3 usuarios y 3 perfiles en la instancia
SwissJob `[V]`, más el propietario como usuario único del portfolio. No es un producto
masivo; es una herramienta personal-y-de-círculo-cercano construida con estándares de
sistema crítico. Esa asimetría —poco tráfico, mucha exigencia de integridad— explica casi
todas las decisiones de este documento.

**Qué hace el sistema.** Cosecha ofertas de muchas fuentes, las normaliza a un modelo
único, las traduce, las convierte en vectores multilingües, las deduplica, y las puntúa
contra el perfil de cada usuario con una combinación de similitud vectorial, reglas
explícitas y re-ranking por LLM. Encima de eso: generación de CV y carta adaptados a la
oferta, seguimiento de candidaturas, búsquedas guardadas y alertas.

---

## 2. Por qué hay dos sistemas y un tercero en medio

El proyecto no nació como un producto: nació **dos veces**.

- **ReactPortfolio** es el portfolio web del propietario, con una UI de escritorio con
  ventanas flotantes. Le creció dentro un agregador de empleo (20 fuentes), matching por
  IA y generación de documentos.
- **SwissJob** nació después, específicamente para la búsqueda en Suiza, multiusuario,
  con su propio motor: 25 providers, 15 scrapers, pipeline de cosecha, dedup, matching.

El resultado fue **dos motores de job-hunting**: dos cosechas de fuentes solapadas, dos
cómputos de embeddings, dos deduplicaciones, dos costes de LLM, dos esquemas de datos que
divergen. Duplicar el trabajo era caro; duplicar los *bugs* era peor.

La decisión estructural del proyecto es **unificar el motor en un tercer componente,
`jobhunt-core`**, y convertir a las dos aplicaciones existentes en **BFF** (*Backend For
Frontend*): conservan su autenticación, su UI y sus datos propios, pero el corpus, los
perfiles de matching, las evaluaciones, el estado del usuario, las candidaturas y los
documentos **los posee el core**.

```
        ANTES                                   DESPUÉS (objetivo)

  ┌──────────────┐  ┌──────────────┐      ┌──────────────┐  ┌──────────────┐
  │  SwissJob    │  │ ReactPortfolio│      │  SwissJob    │  │ ReactPortfolio│
  │  backend     │  │  backend      │      │  BFF         │  │  BFF          │
  │  ┌────────┐  │  │  ┌────────┐   │      │  auth · UI   │  │  portfolio    │
  │  │ MOTOR  │  │  │  │ MOTOR  │   │      │              │  │  chat · CV    │
  │  └────────┘  │  │  └────────┘   │      └──────┬───────┘  └──────┬───────┘
  └──────┬───────┘  └──────┬───────┘             │  HTTP /v1        │
         │                 │                     └────────┬─────────┘
    portales           portales                           ▼
    (solapados)        (solapados)                 ┌─────────────┐
                                                   │ jobhunt-core│
                                                   │ API + worker│
                                                   └──────┬──────┘
                                                          ▼
                                                      portales
                                                     (una vez)
```

**El coste de esta decisión, dicho sin adornos:** es una migración de plataforma, no una
*feature*. La estimación del plan pasó de 3–4 meses a **10–15 meses** para un desarrollador
conforme afloró el rigor real de integridad y de corte sin caídas `[I]`. Se aceptó porque
la alternativa —mantener dos motores divergentes indefinidamente— tiene un coste que no
termina nunca.

---

## 3. La estrategia: Strangler Fig con sombra y una puerta

No se reescribe y se sustituye de golpe. Se aplica **Strangler Fig**: el sistema nuevo
crece al lado del viejo, absorbe responsabilidades una a una, y el viejo se retira solo
cuando el nuevo ha demostrado —con números— que hace su trabajo.

La secuencia por fases:

| Fase | Qué es | Estado hoy |
|---|---|---|
| **Pre-fase** | Sanear 5 defectos P0 del motor de SwissJob antes de copiar nada | Cerrada `[I]` |
| **A** | Construir `jobhunt-core` aislado: esquema propio, API `/v1`, worker, vertical mínima | Cerrada `[I]` |
| **B** | **Sombra**: el legacy sigue mandando, el core observa por CDC y se mide contra un oráculo propio | **En curso** — el gate está a **0/7** `[V]`, ver §5 |
| **C** | Portfolio como piloto: cutover real de sus capacidades | Consumada en el NAS `[I]`; en dev, 2 de 4 capacidades `[V]` |
| **D** | SwissJob: mismo runbook, con canary **por perfil** | No arrancada — su tabla de routing está **vacía** `[V]` |
| **E** | Colegios y documentos | Pendiente |
| **F** | Retirar el motor legacy | Pendiente |

La pieza que da nombre a la fase B y gobierna todo el calendario es el **GATE-SOMBRA**.

### Qué es el GATE-SOMBRA y por qué existe

Mientras el legacy cosecha y escribe, un consumidor CDC replica sus cambios al core, que
los proyecta a su propio modelo. Entonces el core se mide: ¿deduplica bien? ¿ordena bien
los resultados? ¿pierde algo? El gate exige **7 ciclos diarios consecutivos en verde**
`[V]` antes de autorizar el corte.

La decisión de diseño que más ha condicionado el proyecto está aquí: **el sistema viejo no
es el oráculo**. Si midiéramos «¿el core coincide con el legacy?», sólo estaríamos
certificando que reproducimos los errores del legacy. En su lugar se construye un **set
etiquetado propio** —juicios de relevancia y pares duplicado/distinto curados a mano— que
se **congela** (`frozen_at`), y contra ese set se mide a los dos motores.

El coste de esa decisión es alto y hay que decirlo: **el oráculo hay que fabricarlo a
mano**, y su calidad acota lo que el gate puede demostrar. Ver §5 y §6.

---

## 4. Las decisiones de arquitectura que definen el sistema

Cada una con su motivo y su precio. Los mecanismos están en
[DOC 3](DOC_3_DETALLE_TECNICO_2026-08-27.md).

### 4.1 El núcleo es un servicio independiente, no una librería compartida

`jobhunt-core` es un contenedor de API (`core-api`, puerto host 8003) y un worker Celery
(`core-worker`), con **esquema Postgres propio** (`jobhunt`), **Alembic propio**
(`core0001..core0032`, head único `core0032` `[V]`) y **broker Redis dedicado**
(`redis-core`, puerto 6381 en loopback).

*Por qué:* una librería compartida no tiene frontera. Un servicio con API y esquema
propios tiene dos fronteras duras que un refactor accidental no puede cruzar.

*Precio:* infraestructura nueva (2 contenedores + un Redis + una red privada) y toda
comunicación pasa por HTTP, con su latencia y su manejo de fallo.

*Matiz importante:* vive **dentro del repositorio de SwissJob** como paquete
(`jobhunt_core/`). Es un servicio desplegable independiente que comparte repositorio, no un
repositorio separado. La frontera la imponen la API y el esquema, no el árbol de ficheros
— y hay un test estructural (`test_boundaries.py`) que la vigila `[I]`.

### 4.2 Aislamiento operativo obligatorio, incluso para el piloto

Colas Celery propias (`core.harvest`, `core.embedding`, `core.matching`,
`core.notifications`, `core.default`) `[V]`, rol de base de datos sin privilegios sobre el
esquema legacy, `search_path` explícito, leader-lock distinto, pools separados.

*Por qué es obligatorio y no una buena práctica:* el worker legacy escucha
`default,scraping,ai`. Sin namespacing, podría consumir tareas del core. Y el Redis de
caché usa `allkeys-lru`: bajo presión de memoria **puede expulsar mensajes Celery o
leader-locks aunque tengan otro prefijo**. Los prefijos evitan colisiones de nombres, **no
aíslan memoria ni política de expulsión**. De ahí un Redis dedicado con `noeviction`.

### 4.3 La identidad se construye con lo estable, no con lo que cambia

Este es el defecto más grave del motor legacy y la razón material más fuerte para migrar.

En el legacy, la clave primaria de una oferta es `MD5(title|company|url)` `[I]`. Es decir:
**la identidad se construye con campos mutables**. Cuando un portal corrige legítimamente
un título, el hash cambia, el `ON CONFLICT` no encuentra la fila, el INSERT choca con el
índice único de URL, la oferta deja de refrescar su `last_seen_at` y la limpieza la archiva
a los 60 días. **Pérdida de datos activa, por diseño, y silenciosa.**

En el core, la identidad es el **slot** (`source_listings`, único por fuente+`external_id`
o fuente+`url_normalized`), y el contenido son **revisiones versionadas por hash**. Un
título corregido produce una revisión nueva en el mismo slot. Un cambio de empresa produce
un *reciclado*: se cierra la encarnación y nace una vacante nueva con el historial intacto
— un *split* visible, nunca una pérdida callada.

*Cota honesta, y está escrita en el plan:* el modelo del core es condición **necesaria pero
no suficiente**. La garantía real la da el `external_id` que elige **cada adaptador**;
mientras el legacy siga de cosechador, la proyección sombra usa su `hash` como
`external_id` y **hereda la identidad mutable** `[I]`.

### 4.4 Integridad primero: el raw se persiste antes de normalizar

El orden del pipeline de cosecha es inviolable: *fetch → persistir listing + revisión raw →
normalizar → identidad → revisión canónica → embedding → dedup → matching → outbox →
commit del cursor*, y el commit del cursor va **en la misma transacción**.

*Por qué:* si normalizas antes de persistir, un bug del normalizador destruye información
que ya no puedes recuperar sin volver a pedirla al portal. Con el raw guardado, un
normalizador roto se arregla y se re-ejecuta.

*Precio:* más almacenamiento y una tabla más en cada camino de escritura.

### 4.5 La captura de cambios va por replication slot, no por outbox con secuencia

Para cortar sin perder nada hace falta una **frontera determinista** entre «lo que está en
el snapshot» y «lo que hay que reproducir después». Se decidió CDC por *replication slot*
de Postgres (`wal2json`, `wal_level=logical`).

*Por qué se descartó la alternativa obvia* (una tabla outbox con secuencia monotónica): el
`seq` se asigna **antes** del commit, así que una transacción con `seq` menor puede
commitear **después** del snapshot, y un replay `> seq_snapshot` la perdería. **Orden de
commit ≠ orden de secuencia.** Es un fallo que sólo aparece bajo concurrencia y que
destruye datos en silencio.

*Precio:* un slot de replicación retiene WAL mientras su consumidor esté caído. Si el
consumidor se atasca, el disco de la base de datos **compartida** peligra. De ahí un
healthcheck con ocho señales y un procedimiento de *drop* de emergencia documentado.

### 4.6 La costura es por capacidad, y su interruptor vive en el BFF

No hay una fachada única `JobHunting`. Hay **subinterfaces por capacidad** (catálogo,
matching, perfiles, candidaturas, documentos, colegios) `[V]`, cada una con implementación
`local` y `core`, validadas con *contract tests*.

El estado de cada capacidad vive en una **tabla local a cada BFF** (`jobhunt_routing`), no
en una variable de entorno. Modos: `local`, `shadow`, `core_read`, `core_primary`,
`rollback_pending` `[V]`.

*Por qué tabla y no env var:* la configuración por perfil+capacidad tiene que ser dinámica,
transaccional, auditable **y legible aunque el core esté caído**. Una env var exige
reinicio y no admite canary por usuario.

*Por qué por capacidad y no en bloque:* permite mover el catálogo (lectura, corpus global,
reversible) sin mover las candidaturas (escritura, datos durables del usuario). Son riesgos
distintos y merecen interruptores distintos.

### 4.7 Un solo escritor por estado, y el fallback es sólo de lectura

En cada estado del cutover hay exactamente un escritor autoritativo. En `core_read` el
legacy sigue escribiendo y el core sólo sirve lecturas, con **fallback al local** si el
core no responde. En `core_primary` **no hay fallback silencioso**: servir datos locales
desactualizados «porque el core no responde» sería mentir, y escribir en local sería
*split-brain*.

*Cota aceptada explícitamente:* la continuidad durante una caída del core es **sólo de
lectura**. Durante una caída no se guarda, no se aplica y no se genera. La continuidad de
escritura (cola local `pending_sync`) queda **diferida** hasta que la experiencia real
demuestre que hace falta.

### 4.8 Cosecha «humana» y cumplimiento explícito

Hay portales que el proyecto **no scrapea públicamente**: jobs.ch, jobup.ch, Indeed,
LinkedIn, Glassdoor, XING. Existen conectores para ellos, pero **sólo por ruta autorizada**
(credencial de partner o feed oficial) y **arrancan deshabilitados**: sin credencial no se
instancian y no emiten ni una petición `[V]` — verificado: 25 providers registrados, 16
instanciables sin credenciales `[V]`.

Sobre los portales sí permitidos, la cosecha imita a un humano en cuatro capas: huella de
navegador realista, ritmo circadiano con *jitter*, crawling incremental con presupuesto
explícito, y **no evasión** (si un portal bloquea, se registra y se desactiva la fuente
tras 3 bloqueos, no se busca la vuelta).

---

## 5. Dónde está el proyecto hoy — medido, no recordado

> **ACTUALIZACIÓN 2026-08-29 — medido EN EL NAS, que es donde importa.**
>
> Lo de abajo se midió el 27 contra la copia local y se conserva como foto de aquel día.
> Hoy se ha medido contra **producción**, y el cuadro es otro.
>
> **La sombra lleva 24 ciclos corriendo en el NAS** desde el 2026-08-06, con el slot
> lógico activo y `core-api`, `core-worker` y `core-capture` arriba. El holdout ciego
> `holdout-dedup-2026-08-23` **está vivo allí** y congelado: no se perdió: se perdió en la
> copia LOCAL, donde sí se aplicó la canonización de identidad.
>
> **Y el GATE-SOMBRA está en 0/7 porque TODOS los ciclos elegibles salen ROJOS.** No es un
> problema de reloj, ni de cohorte, ni de que el equipo de desarrollo se apague. Es
> sustancia:
>
> | Métrica | Umbral | 2026-08-28 | Lectura |
> |---|---|---|---|
> | `perdida` | **0** estricto | **45** | ofertas legacy activas sin slot en el core |
> | `falsos_negativos` (perfil 0b69…) | 0 | **0,667** | el core no devuelve **4 de 6** ofertas relevantes que **sí están** en el corpus |
> | `ndcg@10` (ambos perfiles) | ≥ 0,60 | **0** | el legacy también da 0: el core no es peor, pero el umbral absoluto no se alcanza |
> | `dedup_recall` | ≥ 0,40 | **0,407** | pasa por 7 milésimas |
>
> **La pérdida CRECE**: 0 → 25 → 27 → 27 → **45** entre el 24 y el 28. Empezó en cero el
> día que se congeló el holdout y sube desde entonces. Es el gate más estricto que hay y
> va en la dirección equivocada.
>
> **El recall de dedup real es 0,407, no 1,0.** El holdout re-muestreado en local dio 1,0
> porque 8 de sus 9 positivos eran del estrato trivial; el holdout de producción, con 58
> pares, dice **tp 11 · fn 16**. La advertencia que acompañaba a aquel 1,0 era correcta y
> aquí queda comprobada: medía lo fácil.
>
> **Migraciones**: el NAS está en `core0029`; el árbol de trabajo, en `core0036`. La
> imagen `swissjob-core:prod` es de hace 3 días.

Todo lo de esta sección lo he comprobado el 2026-08-27 contra el sistema vivo.

### Lo que funciona

| Qué | Medida `[V]` |
|---|---|
| Corpus legacy | 10 805 ofertas · 10 524 activas · 10 776 con embedding · 25 fuentes |
| Corpus proyectado en el core | 14 193 vacantes · 13 624 slots · 14 525 revisiones canónicas · 18 536 embeddings |
| Entrega de eventos | 13 425 eventos en el outbox, 13 425 entregas, 13 425 filas en el inbox sombra |
| Staging CDC | 6 872 filas, **0 sin aplicar** — el proyector va al día |
| Suite legacy | 2 284 tests recolectados (= 2 277 passed + 3 skipped + 4 xfailed) — **por la tarde, 2 280 passed** |
| Cadena de migraciones core | 33 ficheros, head único `core0032` |
| Beat del core | 9 cadencias, sólo colas `core.*` |

Las suites del core (668) y del portfolio (1 823 backend / 319 frontend) las tomo del
registro previo: **no las he re-ejecutado** (el encargo lo prohíbe expresamente) `[I]`.

> **ACTUALIZACIÓN (tarde del 2026-08-27).** Los arreglos de la auditoría externa añadieron
> regresiones. Contadores vigentes: core **675**, legacy **2 280** (+4 xfailed), portfolio
> backend **1 823** (+1 skip), frontend **330**. `[I]` — aportados por el propietario, no
> re-ejecutados aquí.

### Lo que no está donde parece

**El contador del GATE-SOMBRA está en 0/7, y no por calidad.** `[V]`

Ejecuté el informe del gate contra la base real. Dice: *«consecutivos OK: 0/7 · estado: EN
CURSO (faltan 7)»*, y **todos** los ciclos aparecen como `INELEGIBLE`.

La causa no es que las métricas estén rojas. La causa es estructural y está en el código:
un ciclo sólo puede sumar si su ventana empezó **después** del congelado de la cohorte de
evaluación de dedup, y **sin cohorte congelada ningún ciclo es elegible**. Consulté la
tabla: `labeled_dedup_cohorts` tiene **0 filas** `[V]`.

Es *fail-closed* deliberado —una salvaguarda contra sellar una racha con un oráculo que
todavía se puede tocar—, pero la consecuencia práctica hay que decirla clara:

> **El reloj del gate no ha empezado a correr.** No empezará hasta que exista una cohorte
> de dedup registrada y congelada con su acta. Y esa cohorte no puede congelarse antes de
> la maniobra de canonización (§6), porque el sello vuelve los pares inmutables y la
> maniobra invalidaría sus referencias.

Esto encadena el calendario entero: **canonización → cargar el estrato positivo → congelar
la cohorte → empiezan a contar los 7 ciclos**.

> **ACTUALIZACIÓN (tarde del 2026-08-27).** **El primer eslabón ya está hecho**: la
> canonización se ejecutó (§6, actualizado). La carga y el congelado de la cohorte estaban
> **en curso** por otro agente al cerrar esta actualización, y su resultado **no se
> documenta aquí porque todavía no se conocía**. El reloj del gate sigue, pues, sin
> arrancar, pero ya no por el motivo de este párrafo.

**SwissJob no ha empezado la Fase D.** Su tabla `jobhunt_routing` está **vacía** `[V]`, y
`jobhunt_profile_map` también `[V]`. Sin filas, toda capacidad resuelve al default seguro
`local`. La costura está construida, probada y con *contract tests*; simplemente **no está
activada**. Es un hecho, no un problema: el plan pone la Fase D después del gate.

**El portfolio sí está flipado, y en dev menos de lo que la documentación sugiere.**
En la instancia local verifiqué exactamente dos filas `[V]`: `catalog` y `matching` en
`core_read`, ambas por fila comodín, del 2026-08-25. `applications` y `saved_searches` **no
tienen fila** ⇒ resuelven a `local`. La hoja de ruta describe una producción (NAS) con las
cuatro capacidades sobre el core, `applications`/`saved_searches` en `core_primary` `[I]`:
**no he podido verificarlo, el NAS queda fuera de mi alcance**. Son entornos distintos y la
diferencia es legítima; lo señalo para que nadie lea el estado de dev como el de producción.

### Un hallazgo nuevo: `core-api` responde `not_ready` ahora mismo

`GET /v1/ready` devuelve **503** `[V]`:

```json
{"status":"not_ready","alembic":"core0032","expected":"core0029"}
```

La base está en `core0032`, que es lo correcto. Lo que está mal es el **esperado**. La
función que lo calcula, `_expected_head()` en `jobhunt_core/api/main.py`, está decorada con
`@lru_cache(maxsize=1)`: lee la cadena de migraciones **una vez por proceso** y cachea el
resultado para siempre. El proceso de `core-api` arrancó el **2026-08-25T14:42:31Z** `[V]`;
`core0030` y `core0031` se añadieron el 2026-08-26T00:19 y `core0032` el 2026-08-26T11:33
`[V]`. La caché se pobló cuando el head era `core0029` y **no puede refrescarse sin
reiniciar el proceso**.

Es exactamente la misma clase de fallo que la avería O-1 del consumidor CDC —un proceso de
larga vida sirviendo una foto congelada del mundo—, pero con el signo invertido: aquella
estaba **verde estando rota**; ésta está **roja estando sana**. Hoy no rompe nada porque
`core-api` no declara *healthcheck* en el compose y nadie actúa sobre esa sonda `[V]`; en
un despliegue donde un orquestador la consulte, sacaría el servicio de rotación sin motivo.

**No lo he arreglado ni he reiniciado nada** (el encargo lo prohíbe). Queda anotado.

> ### ✅ ACTUALIZACIÓN (tarde del 2026-08-27) — arreglado, y el primer intento fue peor
>
> **Ya no devuelve 503.** `/v1/ready` responde
> `{"status":"ready","alembic":"core0032","release":"ae7fbf2","authoritative":true}` y
> `core-api` reporta *healthy* `[V]`.
>
> El camino tiene una lección que este documento no podía anticipar. El primer arreglo
> (`bf3fbfd`) hizo que `_expected_head()` releyera la cadena del volumen montado en
> caliente: cerró este falso **rojo** y abrió el falso **verde** simétrico —el proceso
> certificando la release B con los handlers todavía en A—. Lo encontró una **auditoría
> externa** (P1-3). El cierre real son `f728518` + `ae7fbf2`: `_EXPECTED_HEAD` se lee **una
> vez al importar**, desde la misma imagen que trae los handlers (y si no se puede leer, el
> proceso **no arranca**), y sobre todo el **perfil operativo deja de montar el código**, con
> lo que cambiar la cadena exige cambiar la imagen y eso recrea el proceso.
>
> Se cerró además la ceguera que este documento señala: `/v1/health` publica ahora `release`
> + `alembic_expected`, `/v1/ready` añade `release` + `authoritative`, y **`core-api` sí
> declara healthcheck** en el compose base (no en `prod.yml`/`qnap.yml`: siguen sin él).
>
> ⚠ **Qué cambia para quien trabaje aquí:** los comandos del core que deban ver el árbol de
> trabajo necesitan `-f docker-compose.yml -f docker-compose.dev.yml`. Sin el override se
> prueba el código de la **imagen**.

---

## 6. Lo que está a medias

Nada de esto es deuda accidental: son maniobras planificadas que esperan una ventana.

> ### ✅ ACTUALIZACIÓN (tarde del 2026-08-27) — la ventana se abrió y las maniobras se hicieron
>
> **La maniobra 1 se EJECUTÓ** (commit `2462717`), con sus dos mitades y en la misma parada
> de workers, autorizada por el propietario. Ensayo en seco y ejecución dieron cifras
> idénticas: g3 **5 419** identidades reescritas · 406 clones · 30 `match_results`
> descartados y **0 con señal del usuario** · 5 263 slots reapuntados (+371 de clones);
> g6 **879** · 40 · 0 · 879 (+40). `canonical_refs` re-mapeó **6 298** filas, **10 juicios**
> y **162 pares**.
>
> Verificaciones posteriores, re-medidas: **1 335** slots huérfanos (los 924 previos + 371 +
> 40, exactamente lo previsto — **no** los 7 477 del fallo), **91 de 91 juicios siguen
> mapeando** (cero etiquetas perdidas), staging a 0 y el slot con retención baja. **El
> GATE-SOMBRA no se invalidó.**
>
> **La maniobra 2** (cargar el estrato y congelar la cohorte) estaba **en curso** por otro
> agente al escribir esta actualización: su resultado **no se documenta aquí porque no se
> conocía**.
>
> **El NAS sigue sin canonizar** y lo necesitará en el mismo despliegue en que suban las
> imágenes nuevas — ver `docs/DEPLOY_NAS.md` §5.4.
>
> Lo que sigue se conserva porque explica **qué estaba en juego** y **cómo se repite**.

### Maniobra 1 — Canonización de identidad legacy (primero)

Dos scripts SQL reescriben `jobs.hash` para arreglar la deriva de identidad en
`arbeitnow`/`jobgether` e `irishjobs`. **Tiene dos mitades y sólo una vive en `backend/`:**

| Qué se rompe | Quién lo arregla | Si falta |
|---|---|---|
| El slot CDC queda huérfano | PASO 7c del script SQL | 6 553 slots huérfanos; la fila legacy se vuelve invisible para la sombra `[I]` |
| Los `job_ref` de las **etiquetas** conservan el hash viejo | `jobhunt_core/shadow/canonical_refs.py` | 10 de 91 juicios y 1 de 260 pares dejan de resolver — **sin error** `[I]` |

La segunda mitad es la peligrosa: las etiquetas no tienen clave foránea, ningún paso del
script las toca, y la función que las resuelve **descarta en silencio** las referencias sin
slot. De los 91 juicios de los tres sets congelados se perderían 10, **8 del mismo set** y
**6 con relevancia > 0** `[I]`. Es decir: degradaría el oráculo, calladamente, justo antes
de usarlo para decidir el go/no-go.

Orden operativo (cinco pasos, ejecutable en `jobhunt_core/shadow/RUNBOOK.md` §7): parar
workers → `pg_dump` incluyendo el esquema `jobhunt` y ensayo sobre la copia → los dos
scripts → `canonical_refs` primero en `--dry-run` y luego en firme, **con los workers aún
parados** → arrancar.

*Ventana abierta:* el re-mapeo aborta si una cohorte de dedup **sellada** tiene pares que
tocar. Hoy no hay ninguna `[V]` — por eso la ventana está abierta, y por eso el gate no
puede contar todavía. Son la misma moneda.

> **La ventana se aprovechó esa misma tarde**, con `labeled_dedup_cohorts` todavía vacía
> `[V]`. Si hay que repetir la maniobra (el NAS) hay que volver a comprobar esa condición:
> con una cohorte ya sellada, la única salida es cargar una cohorte NUEVA con los refs
> canónicos y retirar la vieja del gate.

### Maniobra 2 — Cargar el estrato positivo (después, no antes)

187 pares etiquetados que amplían la cohorte de dedup. Van **después** de la canonización:
cargarlos antes grabaría referencias que la maniobra invalida, y la opción `--excluir` no
sirve **porque el daño no lo detecta ninguna guarda del cargador** `[I]`.

### Acción de seguridad abierta

**`GEMINI_API_KEY` sigue sin rotar** `[V]`. El canal de fuga se cerró el 2026-08-26 (el
logger de httpx emitía la URL completa a nivel INFO), pero **cerrar el canal no borra lo ya
publicado**: la clave estuvo en claro en el journal. El `.env` local conserva `mtime` del
2026-07-02, así que la rotación no se ha hecho. Es acción del propietario; esta
documentación no toca `.env`.

### Regla vigente mientras tanto — ✅ LEVANTADA la tarde del 2026-08-27

**No reiniciar `worker`, `worker-ai` ni `backend`.** La maniobra los para ella misma en su
paso 1; pararlos antes no compra nada y abre la ventana en la que un ciclo de métricas
podría observar el estado intermedio. `core-capture` queda fuera de esa regla: ya se
reinició, con medidas antes y después `[V]`.

> **La regla ya no aplica.** Existía **solo** para proteger la canonización de un rearranque
> que hiciera cosechar con código nuevo sobre datos sin migrar. La maniobra se ejecutó, paró
> ella misma los tres servicios en su paso 1 y los rearrancó al terminar. Los tres corren
> desde entonces `[V]`.

---

## 7. La regla de oro, y cómo leer esta documentación

Nueve ciclos de auditoría dejaron un hallazgo transversal, y no es un bug concreto:

> **Un documento —docstring, comentario, mensaje de commit o encabezado— puede afirmar por
> escrito una garantía que el código no da.**

No es una anécdota. El mecanismo de la «gracia» del gate se **reinterpretó cuatro veces** y
en cada reescritura la línea del informe que lo describía sobrevivió intacta, describiendo
un comportamiento que ya no existía. El parser de salarios se rompió **cuatro veces por el
mismo sitio**, y las cuatro versiones se validaron contra el mismo corpus — un corpus que
**no podía refutar nada**.

De ahí dos reglas que este proyecto convirtió en método:

1. **Toda guarda que fije una cota tiene que afirmar el número literal** y probarse por
   *mutación*: degradar la constante al valor anterior y exigir que la prueba se ponga en
   rojo. Un test que lee la cota del módulo que audita no mide nada.
2. **Verifica ejecutando.** Ninguna cifra de este documento marcada `[V]` está copiada de
   un informe.

**Y la regla se mordió a sí misma mientras yo escribía esto.** El registro de cotas
(`SwissJob/docs/COTAS_Y_DECISIONES.md`, escrito hace unas horas) tiene una sección §8
titulada «Afirmaciones falsas VIVAS (no corregidas)» con seis entradas. Al comprobarlas una
a una en el código, **cinco ya estaban corregidas**:

| Entrada de §8 | Realidad `[V]` |
|---|---|
| Docstring de `find_semantic_duplicates` | Corregido en `de56ae0`, diez minutos **después** de escribirse el registro |
| `scheduler.py`, «URL check weekly Sun 03:00» | Corregido en el mismo `de56ae0` |
| Docstring de `irishjobs.py` | Rectificado el 2026-08-26 en `319fbe7` — **antes** del registro |
| «único borde» de `groq_service.py` | Rectificado el 2026-08-26 en `c056837` — **antes** del registro |
| Pre-`SELECT` de `mark_delivered` | Rectificado el 2026-08-26 en `4fe2378` — **antes** del registro |
| Cifras del commit `fbe22f0` | **Sigue en pie**, y no puede corregirse: un mensaje de commit es inmutable |

Las tres del medio estaban marcadas `[I]` —copiadas de un informe sin re-medir— y las tres
eran falsas por obsolescencia. Es la regla de oro operando exactamente como se predijo, sobre
el documento escrito para prevenirla. **Ninguna afirmación de mecanismo debería sobrevivir
sin re-verificarse**, incluidas las de esta documentación.

Corolario práctico para quien lea los tres documentos: donde veas `[I]`, trátalo como una
hipótesis con procedencia, no como un hecho.

> **Y la regla volvió a morder esa misma tarde, esta vez a mí.** Una **auditoría externa
> independiente** encontró que mi arreglo de `/v1/ready` (`bf3fbfd`) **hacía lo contrario de
> lo que su comentario prometía**: cerró un falso rojo y abrió el falso verde simétrico
> (§5, actualización). Y de dos hipótesis que el auditor **no** llegó a elevar a hallazgo,
> **las dos resultaron ciertas** al reproducirlas. El registro de cotas
> (`docs/COTAS_Y_DECISIONES.md` §8) ha sido corregido en consecuencia: cinco de sus seis
> «afirmaciones falsas vivas» estaban ya cerradas y ahora lo dice.

---

## 8. Hacia dónde va

El camino crítico, en orden, es corto de enunciar y largo de ejecutar:

1. ~~**Canonización de identidad** (maniobra 1) — desbloquea todo lo demás.~~
   ✅ **HECHA** el 2026-08-27 (`2462717`). Ver §6.
2. **Cargar el estrato positivo** y **congelar la cohorte de dedup** — arranca el reloj del
   gate. 🔄 **En curso** por otro agente al escribir esto; resultado no documentado aquí.
3. **Siete ciclos verdes consecutivos** — el go/no-go de la Fase B. El reloj sigue sin
   arrancar: falta el paso 2.

> **Y antes de cualquier despliegue al NAS**, dos cosas que no estaban en esta lista porque
> aparecieron después: rotar `GEMINI_API_KEY` (§6), y aplicar en el NAS la **misma
> canonización en el mismo despliegue** en que suban las imágenes nuevas
> (`docs/DEPLOY_NAS.md` §5.4). La auditoría externa del 2026-08-27 cerró con veredicto
> **NO-GO y cinco condiciones**.
4. **Fase D**: canary por perfil en SwissJob, capacidad a capacidad, empezando por el
   catálogo con flip global por comodín (el catálogo es corpus global sin propietario, no
   necesita canary por perfil).
5. **Fase E** (colegios y documentos) y **Fase F** (retirada del motor legacy, sólo tras
   retención acordada, backup probado y N ciclos sin divergencia).

Y una cota conocida que condiciona el paso 4: las limitaciones actuales del `/v1` (filtros
por cantón, idioma, seniority, tipo de contrato, salario y orden) son **vigentes pero no
permanentes**. La Fase D sólo exige `core_read`, y el `FallbackCatalog` cubre esos filtros
localmente sin regresión. El modelado estructurado que las cierra es **prerrequisito
explícito de `core_primary` del catálogo**, no de la fase.

---

## Continuar

- **[DOC 2 — Componentes](DOC_2_COMPONENTES_2026-08-27.md)** — recorrido pieza a pieza: qué
  es cada módulo, qué responsabilidad tiene y con quién habla.
- **[DOC 3 — Detalle técnico](DOC_3_DETALLE_TECNICO_2026-08-27.md)** — cómo funcionan por
  dentro los mecanismos importantes, y las cotas y vías muertas con su número.

### Fuentes de referencia (no duplicadas aquí)

| Tema | Documento |
|---|---|
| Cotas aceptadas y vías muertas, índice completo | `SwissJob/docs/COTAS_Y_DECISIONES.md` |
| Plan de unificación, runbook de cutover | `PLAN_UNIFICACION_JOBHUNTING.md` §15bis |
| Contratos y DTOs del core | `CONTRATOS_FASE_{A,B,C}.md` |
| Estado y contadores por fecha | `ESTADO_Y_HOJA_DE_RUTA.md` §19 |
| Decisiones ratificadas del propietario | `ACTA_DECISIONES_2026-08-26.md` |
| Operación de la sombra y la canonización | `SwissJob/jobhunt_core/shadow/RUNBOOK.md` |
| Investigación de portales | `PORTALES_EMPLEO_SUIZA.md` |
