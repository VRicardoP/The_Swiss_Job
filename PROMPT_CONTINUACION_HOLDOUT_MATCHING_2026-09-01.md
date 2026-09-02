# PROMPT — Continuación controlada: holdout dedup + calidad de matching

> Fecha de relevo: 2026-09-01. Repo local: `/home/lothar/Public/SwissJob`, rama
> `feat/fase-a-core`, base HEAD `2c198377471c99dd55e4a59b226578438fc0603e`.
> El objetivo es IMPLEMENTAR, verificar y desplegar en el entorno R5 de ensayo; no
> limitarse a emitir otra revisión. No tocar producción.

## Rol y resultado requerido

Actúa como ingeniero senior responsable del cierre. Continúa el trabajo hasta obtener
un resultado demostrable y reproducible:

1. holdout de deduplicación íntegro, congelado, suficientemente mapeable y evaluado sin
   fabricar identidades ni usar sus etiquetas para ajustar reglas;
2. deduplicación por encima de los umbrales del GATE-SOMBRA;
3. matching validado con un conjunto de desarrollo INDEPENDIENTE y después examinado una
   sola vez contra los sets congelados;
4. suite completa verde, imagen inmutable desplegada en R5 y preview de gates sin rojos
   atribuibles a dedup/matching;
5. evidencia escrita de comandos, resultados, versión de imagen, métricas y decisiones.

No declares finalizado el trabajo ni abras la racha de siete ciclos mientras algún gate
de calidad siga rojo. Un resultado honesto `NO-GO` con el siguiente bloqueo exacto es
preferible a contaminar el examen o fabricar un verde.

## Reglas obligatorias

- Aplica YAGNI, responsabilidad única, cohesión alta, acoplamiento bajo, legibilidad y
  consistencia. Corrige la causa raíz con la menor solución completa.
- Antes de modificar, traza el flujo completo dato → candidato → evaluación → métrica →
  gate. Toda reproducción debe convertirse primero en test de regresión que muerda el
  código anterior.
- No ajustes ningún umbral ni regla mirando el verdict del holdout congelado. Sus labels
  son el examen final, no datos de desarrollo.
- No descongeles, edites, borres ni reemplaces cohortes congeladas. No crees aliases,
  URLs ni vacantes sintéticas para hacer mapeables pares ausentes. No cambies una etiqueta.
- No uses `duplicate_of`, `is_active`, archivado, merges o aliases como palancas para
  alterar artificialmente el denominador del gate.
- No ejecutes suites en paralelo: comparten BD de test y producen falsos fallos/deadlocks.
- Preserva todo el trabajo no relacionado del árbol sucio. No hagas `reset`, `checkout`,
  `clean`, borrados masivos ni reformateos globales. No borres los ficheros del usuario
  `school_job_monitor_architecture.md`, `INSTALAR_CODEX_TERMINAL.md` ni
  `web_scraping_course_notes.docx`.
- Los `.orig` actualmente no rastreados deben tratarse como artefactos desconocidos:
  no incluirlos en un commit ni borrarlos sin confirmar primero que son copias mecánicas.
- Solo entorno R5. Prohibido mutar la BD, volúmenes o contenedores de producción.
- No ejecutar bootstrap del R5: podría destruir/recrear el estado y el holdout. El deploy
  debe ser mediante `up`/recreación conservadora y migraciones incrementales.
- Nunca introducir secretos en código, logs, prompt o línea de comandos. Reutiliza la
  sesión SSH existente; si ha caducado, solicita al propietario que autentique.

## Estado exacto del relevo

### Árbol local

- Hay un diff amplio previo (auditoría integral, aproximadamente 36 ficheros, 1718
  inserciones/460 borrados) que pertenece al trabajo en curso. Revísalo, no lo descartes.
- La última suite completa conocida, anterior al ajuste histórico final de dedup, fue
  `916/916` verde.
- Después del ajuste histórico final se ejecutó:
  `docker compose run --rm core-migrate python -m pytest jobhunt_core/tests/test_integration_dedup.py -q`
  con `23/23` verde.
- También estaban verdes antes de ese último cambio: tests de labels+metrics `71/71`.
- La corrección histórica de dedup está SOLO en local y todavía no se ha reconstruido ni
  desplegado.

### NAS R5

- Host: `Ricardo@192.168.1.2`.
- Control socket SSH existente: `/home/lothar/.ssh/cm/capsule`.
- Binario Docker QNAP:
  `/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker`.
- Compose R5:
  `/share/Public/swissjob/docker-compose.rehearsal.qnap.yml`.
- Script de deploy en NAS:
  `/share/Public/swissjob/scripts/nas_deploy_rehearsal_r5.sh` o, según la copia vigente,
  `/share/Public/swissjob/nas_deploy_rehearsal_r5.sh`; resuelve la ruta por lectura antes
  de ejecutar.
- BD exclusiva de ensayo: `swissjobhunter_r5_rehearsal`.
- Imagen desplegada al entregar: release `2c19837-c940d43844be`, config Docker
  `f8fe323...`. Confirma el ID completo en Docker; no lo asumas.
- Producción es ajena a este encargo y debe permanecer intacta.

### Holdout dedup

- Cohorte original, intocable: `holdout-dedup-2026-08-30`, 56 pares, 9 `dup` y 47
  `distinct`.
- Se recuperaron identidades legacy legítimas usando datos reales y el mismo
  `RawListingSink`; después se restauró el estado original de `is_active` y
  `duplicate_of`. No deben quedar ofertas artificialmente activas.
- Se añadieron únicamente dos aliases históricos respaldados por URLs exactas del
  artefacto original en la fuente `evaluation:holdout-2026-08-30`:
  `4ae5... → efc0818...` y `591a... → c62920b...`. Verifica los UUID completos en BD.
- Cohorte derivada congelada vigente:
  `holdout-dedup-20260830-v3`, con 51/56 pares mapeables. La selección se hizo solo por
  resolubilidad independiente de ambos refs; NO se utilizó el verdict para seleccionar.
  Su manifest debe conservar padre, totales, incluidos, excluidos y método de recuperación.
- `jobhunt_core/shadow/labels.py` ya apunta `DEDUP_EVAL_COHORT` a v3.
- Antes del último fix local, la medición real de v3 tras backfill activo fue:
  TP=6, FP=1, FN=1, precision=0.857143, recall=0.857143.
- El FN restante es un duplicado exacto de Klickpiloten cuyo lado correspondiente está
  archivado. El backfill diario activo no debía incluirlo, pero un backfill histórico
  pre-rollback del holdout sí.
- El FP restante es AuroraSolar: `Senior Account Manager` frente a `Account Manager`,
  misma empresa, candidato pending con similitud aproximada 0.850.

### Fix dedup local pendiente de deploy

En `jobhunt_core/dedup.py`:

- `_EXACT_INTRA_HISTORY_SQL` deriva de `_EXACT_INTRA_SQL`, conserva la exclusión de
  `merged_into` pero incluye archivadas;
- `exact_intra_backfill()` usa esa variante histórica;
- `scan_semantic_candidates()` sigue usando directamente `_EXACT_INTRA_SQL`, por lo que
  la ejecución diaria continúa siendo active-only.

En `jobhunt_core/tests/test_integration_dedup.py` la regresión archiva un lado, prueba
que el scan diario devuelve 0 y que el backfill histórico crea exactamente 1 candidato
y después 0 de forma idempotente.

Tras desplegar y ejecutar el backfill histórico, el resultado esperado —que debe medirse,
no asumirse— es TP=7, FP=1, FN=0, recall=1.0 y precision=0.875. La precisión seguiría por
debajo de 0.90; NO resuelvas el FP usando la etiqueta del holdout.

### Matching

- `jobhunt_core/matching.py` contiene una política experimental `hybrid-rrf/v1`:
  ANN + FTS PostgreSQL, RRF y query léxica basada en title+skills.
- Migración nueva: `core0040_offer_search_document.py`, `tsvector` generado + GIN.
- Existe una sola cota canónica compartida:
  `matching.CANONICAL_EVAL_LIMIT = 1800`, consumida por tarea y proyector. No vuelvas a
  introducir límites divergentes que hagan oscilar el feed.
- El deploy vigente activó `hybrid-rrf/v1`. Aún no está validada y no debe gobernar el
  camino canónico hasta superar desarrollo independiente.
- Preview observado con hybrid:
  - persona 1: nDCG=0; relevantes Translator rank 461, Linguist Japanese rank 152 y
    cuatro ofertas Teacher ausentes del feed canónico completo de 1800;
  - persona 2: nDCG=0; su único relevante, Linguist Japanese, rank 33
    (`semantic_rank=16`, `lexical_rank≈981`).
- Las cuatro Teacher tienen embedding del modelo activo y están vivas; sus descripciones
  están vacías. El CV de persona 1 contiene experiencia docente extensa.
- Causa concreta confirmada: `_lexical_query` toma los primeros 32 tokens únicos de
  title+skills. La query observada terminaba en `ipgce` y excluía `teacher`, por lo que
  ofertas con título docente y descripción vacía no tenían señal léxica y quedaban fuera
  del top-1800 semántico.
- Solo existen dos sets de ranking congelados: persona 1 (34 juicios, 6 relevantes) y
  persona 2 (32 juicios, 1 relevante). No hay un set de desarrollo independiente y no
  hay feedback posterior suficiente. Esos sets ya fueron observados; no se permite seguir
  afinando v1 contra ellos.

## Secuencia obligatoria de trabajo

### Fase 0 — inventario y copia de seguridad lógica

1. Lee completos `AGENTS.md`, los runbooks de sombra/NAS, documentación del gate y los
   módulos/tests citados. Lee también:
   - `/home/lothar/Public/PROTOCOLO_DEV2_DEDUP.md`
   - `/home/lothar/Public/holdout_artefactos_2026-08-23/dev2_*`
   - `/home/lothar/Public/holdout_artefactos_2026-08-23/dev3_*`
   - `/home/lothar/Public/HOLDOUT_ETIQUETADO_2026-08-30/INSTRUCCIONES.md`
2. Ejecuta `git status`, guarda `git diff --stat` y revisa el diff exacto de dedup,
   matching, labels, migración core0040, deploy y tests.
3. Verifica que Alembic mantiene una única cabeza y que no se han reescrito migraciones
   publicadas.
4. En R5, captura de forma read-only: release/config image, salud de contenedores,
   `alembic_version`, políticas/modelos activos, tamaños y hashes/manifests de cohortes,
   matriz TP/FP/FN y ranking por perfil. Registra todo como baseline.
5. Comprueba que la cohorte original y v3 están congeladas y que sus filas/manifests no
   han cambiado. Si hay discrepancia, detente: no la repares destructivamente.

### Fase 1 — cerrar el FN histórico sin cambiar producción diaria

1. Revisa la implementación de `_EXACT_INTRA_HISTORY_SQL` y demuestra que:
   - incluye archivadas;
   - excluye merged/losers;
   - respeta consumer/fuente, identidad exacta, ubicación y demás invariantes existentes;
   - no cambia el scan diario active-only;
   - es idempotente y no reabre candidatos resueltos/rechazados.
2. Ejecuta primero el test específico y luego toda la suite. Corrige solo defectos
   reproducibles.
3. Construye una imagen nueva e inmutable. Obtén fingerprint de contenido y etiqueta de
   release; no reutilices `r5-cycle` como única identidad de evidencia.
4. Copia imagen/compose/script al NAS R5, verifica checksum antes y después, ejecuta
   migraciones y deploy conservador. No uses bootstrap, `down -v` ni borres la BD.
5. Ejecuta exactamente una vez el backfill histórico; una segunda ejecución debe devolver
   cero. Verifica el candidato Klickpiloten, el estado de los demás y la ausencia de
   activaciones artificiales.
6. Recalcula la matriz v3. Resultado mínimo: recall >= umbral y cero FN; cualquier
   discrepancia exige investigar el dato, no cambiar etiquetas.

### Fase 2 — resolver la precisión sin contaminar el holdout

1. Busca primero evidencia independiente existente en dev-2/dev-3 sobre calificadores de
   seniority (`Senior`, `Junior`, `Lead`, etc.) y pares equivalentes a Account Manager,
   SIN consultar el verdict de v3 para formular la regla.
2. Si esa evidencia ya cubre la regla de forma suficiente, escribe tests generales con
   positivos y negativos independientes; hazlos morder antes del fix; implementa la menor
   regla simétrica y explícita. Debe preservar recall en los casos dev y no ser específica
   de AuroraSolar.
3. Si la evidencia independiente NO basta, no modifiques el detector. Genera una muestra
   ciega nueva de desarrollo, excluyendo por UUID/job_ref/vacancy_id TODOS los miembros de
   holdout y sets congelados. Incluye estratos:
   - mismo título con seniority distinto;
   - mismo cargo con seniority igual;
   - títulos cercanos pero funciones distintas;
   - cross-portal y same-source;
   - positivos y negativos difíciles por empresa/ubicación.
   Versiona manifest, SQL exacto, fingerprint de corpus, seed, UUIDs y hoja ciega sin
   scores, estrato ni predicción. Solicita etiquetado independiente al propietario/agente
   autorizado. No autoetiquetes y continúes como si fuera evidencia independiente.
4. Ajusta una sola vez contra desarrollo, congela la regla y ejecuta el holdout solo como
   examen final. Si suspende, informa NO-GO y amplía desarrollo con una hipótesis nueva;
   no itera mirando cada error del examen.

### Fase 3 — reconstruir y validar matching con desarrollo independiente

1. Retira `hybrid-rrf/v1` del camino canónico mientras no esté validada. El estado seguro
   es conservar la política canónica previa y ejecutar la experimental solo en sombra, sin
   `move_current=True`. Verifica la semántica real de selección de política antes de tocar
   flags: no asumas que múltiples políticas activas eligen la deseada.
2. Construye un paquete de desarrollo de ranking independiente de los dos sets congelados.
   Excluye sus job_refs/vacancy_ids y cualquier par del holdout dedup. Usa al menos:
   - candidatos ANN altos/medios;
   - candidatos FTS altos/medios;
   - familias de rol extraídas del CV (incluidos roles repetidos que no aparecen en
     title/skills);
   - hard negatives de profesión vecina;
   - ubicaciones/remoto y casos con descripción vacía.
   Guarda consulta, seed, fingerprint, manifest y una hoja ciega.
3. Obtén etiquetas independientes con relevancia graduada y un mínimo suficiente por
   perfil. No uses los juicios congelados como desarrollo ni reveles scores/algoritmo al
   etiquetador.
4. Define antes de implementar criterios de aceptación en desarrollo: nDCG@10,
   falsos-negativos/recall de relevantes, estabilidad y latencia. Usa los umbrales del
   contrato, no otros elegidos después de ver resultados.
5. Investiga soluciones generales y pequeñas. Prioridad recomendada:
   - extracción/ponderación determinista de términos de rol del CV completo, no “primeros
     32 tokens únicos”;
   - preservar frases/títulos ocupacionales y repeticiones informativas;
   - desempate o combinación semántica/léxica calibrada en desarrollo;
   - recuperación suficiente antes del ranking, especialmente ofertas sin descripción.
   Evita diccionarios específicos de una persona, IDs, títulos del holdout o reglas para
   hacer subir una oferta concreta.
6. Crea regresiones unitarias y de integración: un rol repetido solo en `cv_text` debe
   entrar en la query; términos comunes no deben expulsar roles; dos ejecuciones deben ser
   deterministas; el feed canónico no debe oscilar según tarea/proyector; resultados no
   finitos se rechazan; latencia/plan SQL quedan acotados.
7. Mide en desarrollo y solo cuando pase congela `hybrid-rrf/v2` (no reescribas v1).
   Ejecuta entonces UNA evaluación final contra los sets congelados. Exige nDCG y falsos
   negativos verdes por perfil. Registra también ranks y cobertura del candidate set.
8. Si el examen final suspende, mantén v2 fuera del camino canónico y devuelve NO-GO con
   diagnóstico. No sigas ajustando contra los juicios observados.

### Fase 4 — verificación integral, deploy y gate

1. Ejecuta en serie:

   ```bash
   docker compose run --rm core-migrate python -m pytest jobhunt_core/tests -q
   ```

   La suite debe quedar completamente verde; informa el conteo real.
2. Verifica migración desde la cabeza anterior hasta la nueva y downgrade seguro cuando
   aplique. Confirma índices/planes FTS/ANN en PostgreSQL real, no solo mocks.
3. Construye y despliega una única imagen final inmutable al R5. Registra commit, dirty
   diff hash, config image ID y sha256 del tar. Todos los servicios R5 deben ejecutar el
   mismo artefacto.
4. Comprueba salud de API, worker, beat, CDC, projector, PostgreSQL y Redis; logs sin
   crash-loop; slot avanzando; outbox drenando; cosecha con un único escritor.
5. Ejecuta preview, no selle ciclos históricos ni cambie el reloj. Desglosa cada gate con
   `value`, `no_data`, umbral, muestra y causa.
6. Condiciones mínimas para declarar cierre de esta sesión:
   - `labels_ready` verde sobre v3 congelada;
   - dedup precision y recall verdes;
   - nDCG@10 y falsos negativos verdes por cada perfil medible;
   - ningún feed controlado por una política no validada;
   - suite completa verde;
   - contenedores R5 sanos y release verificada;
   - ningún dato de producción tocado;
   - ninguna cohorte/label/identidad fabricada o modificada para obtener el verde.
7. Solo después de todo lo anterior puede abrirse una ventana NUEVA de siete ciclos. El
   primer contador será el primer ciclo completo cerrado bajo la misma imagen, configuración,
   cohortes y umbrales. Un preview no cuenta.

## Comandos operativos orientativos

Adapta rutas tras comprobarlas; son ejemplos, no permiso para actuar sobre producción:

```bash
ssh -S /home/lothar/.ssh/cm/capsule Ricardo@192.168.1.2 \
  "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker inspect \
   -f '{{.State.Health.Status}} {{.Config.Image}} {{.Image}}' swissjob-core-api-r5"

ssh -S /home/lothar/.ssh/cm/capsule Ricardo@192.168.1.2 \
  "/share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker exec \
   swissjob-core-worker-r5 python -c \
   \"from jobhunt_core.tasks.shadow import preview_cycle_task; r=preview_cycle_task.apply(); print(r.get())\""
```

No encadenes operaciones destructivas. Antes de cada deploy resuelve explícitamente los
nombres R5 y demuestra que ningún target corresponde a producción.

## Entregable final

Crea `/home/lothar/Public/SwissJob/CIERRE_HOLDOUT_MATCHING_2026-09-01.md` con:

- veredicto `APTO PARA INICIAR RACHA` o `NO-GO`;
- inventario before/after y release exacta;
- cambios y causa raíz de cada uno;
- tests que mordían y resultados de suite;
- manifest/fingerprint del desarrollo independiente;
- métricas de desarrollo y examen final separadas;
- matriz dedup TP/FP/FN y métricas de ranking por perfil;
- preview completo de gates y bloqueos restantes;
- evidencia de que original/v3 permanecen congelados e íntegros;
- evidencia de que producción no fue modificada;
- rollback concreto y seguro de la imagen/configuración R5.

No ocultes incertidumbre. Si falta etiquetado independiente, la conclusión correcta es
`NO-GO: pendiente de etiquetado`, con el paquete listo y sin mantener una política fallida
como canónica.
