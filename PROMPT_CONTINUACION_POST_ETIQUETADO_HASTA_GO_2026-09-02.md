# PROMPT — Cierre post-etiquetado hasta GO (dedup, matching, racha y cutover)

> Fecha: 2026-09-02. Ejecuta el trabajo; no hagas otra revisión puramente
> descriptiva. Repo core: `/home/lothar/Public/SwissJob`, rama
> `feat/fase-a-core`, HEAD inicial `3e2683722b3f818db81fd09fcad58c02f11c9f74`.
> Repo documental: `/home/lothar/Public`, commit de los juicios independientes
> `0d2284a8c3614fda3c4595c1809f7688dab3c2ad`.

## Misión y significado de GO

Continúa desde el estado medido del R5 hasta obtener evidencia suficiente para
el `GO`, sin contaminar los holdouts ni convertir un preview en aprobación.

Hay tres hitos distintos y debes nombrarlos correctamente:

1. **APTO PARA INICIAR RACHA**: todos los gates de calidad e infraestructura
   están verdes en preview bajo la release candidata.
2. **GATE-SOMBRA SUPERADO**: siete ciclos completos computables y verdes bajo
   exactamente el mismo código/configuración/evidencia, más los atestados
   bloqueantes exigidos por el contrato.
3. **GO DE CUTOVER/PRODUCCIÓN**: además de lo anterior, ensayo §4, Gate C,
   backups, rollback y comprobaciones operativas están aprobados. No ejecutes
   una mutación de producción sin autorización explícita del propietario.

Un `NO-GO` preciso es obligatorio si falta evidencia. Está prohibido obtener un
verde modificando labels, cohortes, umbrales, identidades o el periodo medido.

## Reglas de ejecución para cerrar sin generar nuevos errores

- Aplica YAGNI y corrige la causa raíz en el punto compartido más estrecho.
- Antes de cambiar código, escribe una reproducción que falle en el padre.
- Para cada corrección, enumera primero los invariantes y TODOS sus consumidores;
  evita arreglar solo el camino que reveló el defecto.
- Ejecuta tests y suites en serie: comparten BD de test.
- No reescribas migraciones Alembic publicadas; cambios nuevos en una revisión
  posterior a `core0040`.
- No toques producción. En NAS solo la BD `swissjobhunter_r5_rehearsal` y los
  servicios con sufijo `-r5`.
- No uses `bootstrap`, `down -v`, borrado de volúmenes ni limpieza de cohortes.
- No descongeles ni edites sets/pares congelados. No fabriques aliases, vacantes,
  activaciones o merges para cambiar el denominador.
- No vuelvas a ajustar contra el holdout después de verlo. Desarrollo decide;
  holdout examina una vez la versión congelada.
- Mantén la política experimental fuera del feed canónico hasta que pase todo
  el desarrollo y la verificación R5.
- Preserva el árbol ajeno: no hagas reset/clean. En particular no modifiques ni
  incluyas por accidente `school_job_monitor_architecture.md`,
  `INSTALAR_CODEX_TERMINAL.md` o `web_scraping_course_notes.docx`.
- No introduzcas secretos en código, argv, logs o informes.
- Toda afirmación de cierre debe incluir comando, salida, timestamp, release y
  objeto exacto medido.

## Evidencia independiente ya disponible

### Paquete seniority

Directorio: `/home/lothar/Public/DEV_SENIORITY_2026-09-02/`.

- 60 líneas completas: **5 `duplicate`, 54 `distinct`, 1 `unsure`**.
- `D25` es `unsure`; se excluye de métricas.
- Hay pares repetidos o invertidos. Antes de calcular resultados, agrupa por una
  clave canónica no ordenada de los dos registros visibles. No permitas que las
  repeticiones den más peso a una decisión.
- Hay evidencia directa de plazas distintas con título base próximo y nivel
  diferente dentro de la misma empresa, pero la regla debe derivarse de TODO el
  desarrollo único, no del FP del examen.

Hashes que deben permanecer iguales:

```text
hoja_ciega.json  f0bb731e76aaf7d7ccb6b05b929a3e2d1b37c6070752a62dd91a04f3b998f5b6
hoja_ciega.md    642e156a07c3da8c8f1824832bc4a9be85dbe145ab305641c26de3c484b28cee
juicios.txt      6766ac219c5df2084266eaf8195dfd4464ae7aa35132475926e9eafc49456c84
```

### Paquete ranking

Directorio: `/home/lothar/Public/DEV_RANKING_2026-09-02/`.

- 79 líneas completas: **35 × 0, 36 × 1, 4 × 2, 4 `unsure`**.
- P1: 18/19/2/1; P2: 17/17/2/3 para 0/1/2/unsure.
- Excluye `R019`, `R021`, `R050` y `R051` de toda métrica.
- 44/79 ofertas carecen de descripción. No conviertas la ausencia en relevancia
  cero por defecto; usa los juicios ya emitidos.
- Clave privada ítem→perfil/vacante/estrato:
  `/home/lothar/Public/holdout_artefactos_2026-08-23/rank_dev_clave_2026-09-02.txt`.

Hashes:

```text
hoja_ciega.json  d3aaff87d601b28207b31565d864c6879bf9ea9c97a41fd77d3ef212b02fba24
hoja_ciega.md    d9fba86186ca41a952d20dd866fbbf57b3fd8ad298f10412c900eaefce8cb13b
juicios.txt      adf316ab9c3bd3118e0cf0185d84bd2ceafb9c358ae9e52610434e454d8c30a3
```

Los juicios fueron producidos por dos agentes nuevos y separados, sin historial,
limitados a su paquete; no consultaron repo, BD, detector, algoritmo ni holdout.

## Estado R5 de partida

Lee completo `CIERRE_HOLDOUT_MATCHING_2026-09-01.md` antes de actuar. Verifica de
nuevo el estado, no confíes ciegamente en estas cifras:

- suite core: **917 passed**;
- cohorte vigente: `holdout-dedup-20260830-v3`, congelada, 51/51 mapeables;
- dedup: TP=7, FP=1, FN=0; recall=1.0, precision=0.875 (<0.95);
- el FN histórico está cerrado y su backfill es idempotente;
- `cosine-baseline/v1` es canónica; `hybrid-rrf/v1` está en sombra;
- nDCG@10=0 en ambos perfiles y FN de matching rojo en P1;
- el backfill histórico dejó unas 190 172 filas `dedup_candidates` pending;
- release R5 declarada `2c19837-0e83e37a6f1b`, imagen
  `sha256:b759b562…`; resuelve y registra los IDs completos;
- producción no fue modificada.

NAS:

```text
host                 Ricardo@192.168.1.2
socket SSH           /home/lothar/.ssh/cm/capsule
docker QNAP          /share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker
compose R5           /share/Public/swissjob/docker-compose.rehearsal.qnap.yml
BD R5                swissjobhunter_r5_rehearsal
```

## Fase 0 — congelar el baseline y completar trazabilidad

1. Verifica hashes, cardinalidad, IDs consecutivos y valores válidos de ambos
   paquetes. Confirma el commit documental `0d2284a`.
2. Genera un informe de pares seniority ÚNICOS: clave no ordenada basada en los
   campos visibles; lista duplicados/inversiones y conserva un solo peso por par.
3. Falta una clave versionada Dxx→UUID/estrato para seniority. Recupérala del
   muestreo original o mediante join exacto de los campos visibles. Cada lado
   debe resolver de forma única. Si hay ambigüedad, márcalo sin adivinar y usa el
   par solo como caso de test offline, no como medición DB.
4. Valida la clave de ranking: exactamente R001..R079, perfil y vacancy UUID
   existentes, 0 referencias a cualquier set/cohorte congelado.
5. Captura en R5, solo lectura: release, salud, Alembic, políticas activas,
   manifests/hashes de cohortes, matriz dedup, ranking por política y preview de
   gates. Esa captura es el `before`.
6. No abras el holdout para desarrollar. Solo puedes conocer las métricas ya
   publicadas en el cierre; no inspecciones nuevos pares/juicios.

## Fase 1 — cerrar precisión dedup con los juicios de seniority

### 1.1 Medición de desarrollo

1. Excluye `unsure` y colapsa repeticiones/inversiones.
2. Ejecuta la regla actual sobre los pares resolubles y produce matriz TP/FP/FN,
   desglosada por patrón: seniority simétrica, asimétrica, función diferente,
   cross-portal, misma/múltiple ubicación.
3. Formula por escrito la regla mínima ANTES de ver el holdout. No cambies
   `CORE_DEDUP_LEX_TRGM_MIN`, `CORE_DEDUP_LEX_TRGM_INTRA_MIN` ni el umbral del gate.

### 1.2 Corrección compartida

El arreglo esperado es un guard simétrico de compatibilidad de nivel, pero debes
confirmarlo con el desarrollo. Requisitos:

- extraer tokens de nivel por palabra/frontera, nunca substring (`intern` no debe
  casar dentro de `international`, `lead` dentro de `leader`, etc.);
- distinguir niveles incompatibles y el caso explícito↔no explícito solo cuando
  el título base restante sea realmente el mismo/compatible;
- no vetar títulos idénticos, variantes de género, porcentajes o detalles no
  relacionados;
- reutilizar una sola expresión/helper en ANN y léxico;
- aplicar el guard ANN **antes del LIMIT** y en el brazo léxico antes del INSERT;
- el exacto por `text_hash` no necesita una copia divergente de la regla;
- añadir revalidación auditable de pendientes: preview con `n+hash`, apply con
  `resolved_by` versionado y segunda ejecución 0; no tocar accepted/rejected;
- bump obligatorio de la versión de regla en el mismo commit.

Escribe primero regresiones con varios pares ÚNICOS del desarrollo: negativos de
seniority, duplicados de control, substrings, simetría A/B y ambos generadores.
Las pruebas deben fallar en el padre por el defecto correcto, no por fixtures.

### 1.3 Aceptación dedup

1. Desarrollo único: precision >=0.95, recall >=0.40 y ninguna regresión sobre los
   duplicados de control. Reporta también intervalos/conteos; la muestra es pequeña.
2. Suite focal y completa verdes.
3. Congela el código/regla. Solo entonces ejecuta UNA medición del holdout v3.
4. Cierre esperado, no garantizado: TP=7, FP=0, FN=0, precision=1.0, recall=1.0.
5. Si suspende, no ajustes usando el par que falló. Revierte el cambio canónico,
   declara NO-GO y crea nueva evidencia independiente para la nueva hipótesis.

## Fase 2 — reconstruir matching usando solo el desarrollo etiquetado

### 2.1 Baselines reproducibles

1. Une `juicios.txt` con `rank_dev_clave_2026-09-02.txt`; excluye los cuatro
   `unsure`. Persiste un manifest con hashes y mapping, separado del gate.
2. Mide sobre los mismos 75 ítems y por perfil:
   - `cosine-baseline/v1`;
   - `hybrid-rrf/v1` sin mover `current_eval_id`;
   - cobertura del candidate set para relevancia 2;
   - nDCG@10 con la misma fórmula de `shadow.metrics`;
   - latencia y filas examinadas.
3. Criterios fijados antes de cambiar código:
   - nDCG@10 >= 0.60 por perfil;
   - ningún relevante 2 presente en corpus fuera del feed completo (modo estricto
     porque hay <50 relevantes);
   - determinismo total y coste compatible con la ventana operativa.

### 2.2 No des por cierta una causa HNSW incompleta

El muestreador observó que una consulta ANN simple sin `iterative_scan` no entrega
más allá de `ef_search<=1000`. Eso NO demuestra por sí solo que el evaluador
canónico quede truncado: `matching.evaluate_profile` configura
`hnsw.iterative_scan='strict_order'` y posee fallback exacto cuando
`len(candidates)<target`.

Debes reproducir en PostgreSQL R5:

1. conteo del brazo ANN con los mismos GUC y SQL del evaluador a `k=1800`;
2. si usa índice HNSW, cuántos devuelve y cuándo activa fallback;
3. `EXPLAIN (ANALYZE, BUFFERS)` de ANN, FTS y exacto;
4. caso adversarial específico: ANN devuelve <target pero FTS llena la unión
   híbrida hasta target. El control actual mira el tamaño COMBINADO y puede omitir
   el fallback semántico. Esa regresión debe morder si el defecto existe.

Corrige el informe técnico si la hipótesis queda refutada. No subas ciegamente
`ef_search` fuera de su rango válido.

### 2.3 Política v2 mínima

No mutar `hybrid-rrf/v1`; crea `hybrid-rrf/v2`. Parte de los resultados de
desarrollo y aplica el mínimo necesario:

- la query léxica no puede ser “los primeros 32 tokens únicos de title+skills”;
- incluye señales ocupacionales del CV completo de forma determinista: title y
  skills con prioridad, más términos/frases de rol repetidos del CV;
- filtra palabras genéricas con una lista pequeña y probada; no codifiques nombres,
  IDs ni títulos del holdout;
- el límite debe escoger por peso/señal, no por orden accidental del JSON/CV;
- separa la suficiencia del brazo ANN de la del FTS; un brazo no debe ocultar el
  underfill del otro;
- fusiona rankings solo después de que cada brazo tenga rango correcto;
- usa desempate estable por vacancy UUID;
- conserva una única `CANONICAL_EVAL_LIMIT` para tarea y proyector;
- evita un scan exacto O(corpus) habitual: fallback solo cuando se demuestre
  underfill y con coste medido.

Regresiones obligatorias:

- rol repetido solo en `cv_text` entra en la consulta y recupera oferta sin desc;
- title/skills siempre conservan prioridad;
- términos comunes no expulsan roles informativos;
- orden distinto del mismo contenido produce la misma query/ranking;
- ANN bajo + FTS lleno no oculta underfill semántico;
- limit >1000 entrega el objetivo o demuestra corpus menor;
- scores finitos/acotados y desempate determinista;
- v1 permanece inmutable;
- tarea y proyector materializan el mismo top-K;
- una política de sombra jamás mueve el feed.

Limita el ajuste a una pequeña comparación predeclarada de alternativas. Elige por
los 75 juicios de desarrollo, registra ablation y congela v2. No abras los sets
congelados durante esta fase.

### 2.4 Promoción segura

1. Despliega v2 en R5 como sombra, manteniendo `cosine-baseline` canónica.
2. Reevalúa ambos perfiles y confirma que las métricas de desarrollo en R5
   coinciden con local.
3. Si desarrollo pasa, cambia canonicidad transaccionalmente. Como la selección
   actual es alfabética, no basta activar v2: desactiva `cosine-baseline/v1` y
   `hybrid-rrf/v1`, deja solo v2 canónica, o implementa una selección explícita
   mínima y testeada. El deploy debe preservar esta decisión y no reponer v1.
4. Reevalúa el feed canónico. Ejecuta UNA evaluación final contra los dos sets
   congelados: nDCG@10 >=0.60 (y >= legacy-0.05) por perfil, FN estricto=0.
5. Si falla, rollback transaccional a cosine y NO-GO; no afines contra el examen.

## Fase 3 — tratar la cola histórica sin romper evidencia

Los ~190 172 `pending` requieren decisión operativa, no un DELETE improvisado:

1. mide tamaño, crecimiento, índices, tiempos de consultas y cuántos pares tienen
   ambos lados archivados, uno activo o ambos activos;
2. comprueba si UI/tareas de revisión incluyen archivados y si la cola degrada beat,
   backup o gate;
3. el holdout histórico necesita que sus candidatos sigan contando. No marques
   `rejected` ni borres en masa sin una política que preserve esa evidencia;
4. solución mínima preferida: excluir archivados del flujo humano/operativo y
   conservar la evidencia histórica, con índice parcial si el plan lo exige;
5. si propones retención, añade manifest, preview/hash/apply, rollback y test de que
   los pares congelados siguen evaluables.

El bloque puede quedar como deuda acotada solo si demuestras que no afecta a gate,
latencia, almacenamiento, backup ni operación durante la racha.

## Fase 4 — verificación integral y deploy limpio R5

1. Ejecuta en serie, con overlay de desarrollo requerido:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml \
  run --rm core-migrate python -m pytest jobhunt_core/tests -q
```

2. Exige `git diff --check`, una sola cabeza Alembic, upgrade desde `core0040` y
   downgrade probado cuando sea seguro.
3. Construye desde un árbol limpio del commit candidato (worktree/archive temporal),
   sin resetear el workspace del usuario. Etiqueta por commit+digest; nunca uses
   `:prod` para una build sucia.
4. Copia imagen/compose/script al NAS con sha256 origen=destino.
5. Despliega exclusivamente R5 mediante `up`; nunca bootstrap ni volúmenes.
6. Verifica que API, worker y beat usan el mismo image ID/release; health/ready,
   Alembic, CDC, projector, Redis, outbox y harvest sanos.
7. Ejecuta los one-shot en orden: preview, apply, hash idéntico, segunda apply=0.
8. Espera a que outbox drene realmente; no llames “transitorio” a un lag sin medir
   tasa de llegada, salida y ETA.
9. Ejecuta `preview_cycle_task` en savepoint. Deben estar verdes:
   labels_ready, dedup precision/recall, nDCG/FN por perfil, pérdida, latencia,
   outbox lag/dead y restantes gates. Publica valores, umbrales y muestras.

Si cambia código, configuración, política, cohortes o umbrales, la release candidata
y la futura racha empiezan de nuevo. No heredes ciclos de otra versión.

## Fase 5 — racha de siete ciclos

Solo cuando Fase 4 esté totalmente verde:

1. registra inicio de ventana en zona `Europe/Madrid` y fingerprint inmutable de
   imagen, config, modelos, políticas, cohortes y umbrales;
2. deja correr siete ciclos COMPLETOS de 24 horas; un preview no cuenta;
3. cada cierre: guarda informe, muestras, slot CDC, harvest, outbox, métricas de
   calidad y `N/7`;
4. un ciclo rojo medido no puede convertirse en ausencia declarada;
5. una parada solo cuenta si cumple el contrato de autor/evidencia/inmutabilidad y
   los límites; no tapa fallos de calidad;
6. si un defecto exige deploy, corrígelo con reproducción, suite y restart 0/7;
7. si hay drift de datos, crea desarrollo nuevo e independiente; nunca reetiquetes
   el holdout vigente para salvar la racha.

Al séptimo verde confirma que el gate exige también y encuentra los atestados de
ratificación y ensayo rollback/replay. `streak=7` sin esos artefactos no es
`gate_passed`.

## Fase 6 — Gate C y cutover

Tras GATE-SOMBRA superado:

1. ejecuta el ensayo §4 completo sobre copia NAS: ledger por entrada, procedencia
   exacta, verificación estructural independiente y rollback FK-safe;
2. ejecuta el checklist C-6 con hechos del core atestados y routing fresco;
3. comprueba C-2: kill-switch en quiesce y write-freeze real antes del snapshot;
4. backup y restore rehearsal verificados, incluidos triggers/guardas;
5. seed C-3 del CV con perfil provisionado y credencial `profiles:write`;
6. migración C-4 single-call, manifest `ok`, identidades/rollback exactos;
7. canary `core_read` por capacidad, observabilidad y ausencia de doble escritor;
8. solicita autorización explícita antes de cualquier flip de producción;
9. flip `core_primary` solo para catálogo/matching conforme al runbook, verifica
   E2E y mantén durables single-writer;
10. si falla cualquier invariante, rollback por routing y manifiesto; nunca borres
    corpus compartido por `profile_id` o heurística.

## Prioridades anti-regresión

Aplica siempre este orden:

1. reproducir el fallo real;
2. enumerar invariantes y caminos consumidores;
3. añadir tests adversariales e interleavings antes del fix;
4. corregir una frontera compartida, no síntomas repetidos;
5. medir en desarrollo independiente;
6. comprobar eficiencia con datos/planes reales;
7. ejecutar suite integral;
8. desplegar en sombra con rollback;
9. examinar el holdout una vez;
10. congelar artefacto y comenzar racha desde cero.

Antes de cada cierre realiza una “mordida”: revierte temporalmente solo el fix en
un worktree y demuestra que la regresión falla por la causa esperada. Revisa además
que los tests no pasen por sleeps, mocks laxos, fixtures alteradas o rutas distintas
de producción.

## Entregables

Mantén un informe `CIERRE_POST_ETIQUETADO_HASTA_GO_<fecha>.md` con:

- commits local/documental y release/digest NAS;
- hashes de hojas, juicios, claves y manifests;
- matriz de desarrollo dedup por pares únicos y holdout final separado;
- métricas/ablations de ranking desarrollo y examen final separado;
- EXPLAIN/latencias/cobertura ANN y FTS;
- estado y decisión de la cola histórica;
- suite completa y mordidas;
- preview de todos los gates;
- siete informes de ciclo y fingerprint común;
- evidencia §4, Gate C, backups, rollback y autorización del flip;
- veredicto exacto: `NO-GO`, `APTO PARA INICIAR RACHA`,
  `GATE-SOMBRA SUPERADO` o `GO`, sin mezclar significados.

No termines con “parece solucionado”. Termina cuando el estado correspondiente
esté probado, o con un único bloqueo concreto, reproducible y accionable.
