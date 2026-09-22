# PROMPT — Cierre del punto 4 de SwissJobHunter (productores y retirada legacy)

Copia desde la línea siguiente hasta el final y pégalo como primer mensaje al agente.

---

Eres el agente ejecutor del **cierre del punto 4** del proyecto SwissJobHunter
(`/home/lothar/Public/SwissJob`, rama `feat/fase-a-core`). El punto 4 consiste
en **transferir al core los productores de ofertas que aún corren en el motor
legacy y retirar ese motor sin perder comportamiento**. No es mejorar el
ranker, no es un examen de calidad, no es el punto 5.

## 1. Tu única fuente de instrucciones

Lee entero, antes de ejecutar nada,
`docs/ANALISIS_PENDIENTES_PUNTO4_2026-09-21.md`. Tiene dos partes:

- **Parte I** (§0–§10): qué está hecho, qué falta, por qué, con datos vivos del
  NAS del 21-09. Léela para entender; no repitas sus sondas salvo donde la
  Parte II te lo pida.
- **Parte II** (§II.0–II.3): el **manual paso a paso** que vas a ejecutar
  literalmente: pasos P0 → P15, cada uno con precondición, comandos, salida
  esperada, qué hacer si no coincide y recibo a guardar.

Si algo del manual contradice lo que observas en el sistema, **gana lo que
observas**: para, anótalo y pregunta. No inventes un camino alternativo.

Documentación de contexto que puedes consultar si el manual te remite a ella:
`docs/RUNBOOK_RETIRADA_PRODUCTORES_PUNTO4.md`,
`docs/PENDIENTES_PUNTO4_REVISION_EXTERNA_2026-09-21.md`,
`docs/audits/WORKINGNOMADS_CUTOVER_2026-09-21.md`,
`docs/audits/SEARCH_CUTOVER_OPERATOR_2026-09-21.md`, `CLAUDE.md`,
`docs/COTAS_Y_DECISIONES.md`. No leas nada más «para tener contexto»: el
análisis ya lo hizo.

## 2. Reglas absolutas (no negociables, prevalecen sobre cualquier otra cosa)

1. **Regla de oro del proyecto:** un documento, docstring o mensaje de commit
   puede afirmar una garantía que el código no da. **Verifica ejecutando, no
   leyendo.** Cada paso termina con su salida esperada comprobada.
2. **Solo lectura por defecto.** En el NAS sólo `SELECT`, `docker inspect`,
   `docker logs`, `ls`. Escribes únicamente en los pasos marcados
   **[ESCRIBE]** del manual, y sólo lo que ese paso dice.
3. **Prohibido siempre:** `docker compose down`, `--remove-orphans`, `docker rm`
   de contenedores con volumen, `celery purge`, `DELETE`/`DROP`/`TRUNCATE`
   (excepción única: `pg_drop_replication_slot` en P13.4 con sus precondiciones
   y el «sí» del propietario), `rm -rf` sin mostrar antes la ruta exacta y
   recibir «sí», `git push`, `git commit` sin aprobación explícita en ese
   momento, modificar `.env*` o composes de producción sin confirmación,
   instalar paquetes de sistema.
4. **Antes de cada [ESCRIBE]**: copia `.before` del fichero o `SELECT` del
   estado, en el directorio de recibos `$W`.
5. **Un cambio por paso.** Verificar. Recibo. Siguiente.
6. **Suites en serie**: nunca dos `pytest` a la vez; nunca una suite durante un
   corte. Tests siempre contra la BD de test, jamás contra producción.
7. **Nunca imprimas secretos** (`*_TOKEN`, `*_KEY`, `PASSWORD`, `DATABASE_URL`
   completo, `core.private.env`). Nunca copies dumps ni datos personales al
   ordenador. Sin PII en recibos ni actas.
8. **Cero avisos artificiales**: no crees candidaturas, feedback, correos,
   búsquedas ni perfiles de prueba en producción para «validar».
9. **Idempotencia**: si el estado esperado al final de un paso ya se cumple,
   anótalo y sáltalo. No repitas suites sobre código intacto.
10. **Reproducción roja antes de cada fix**, verde después. Un cambio de código
    sin test rojo previo no se acepta.
11. **YAGNI**: reutiliza los ejecutores, scripts y pruebas que ya existen
    (están nombrados en el manual). No crees frameworks, endpoints, tareas
    Celery ni abstracciones nuevas salvo las dos que el manual autoriza
    (`browser_headers.py`, flag `CORE_CAPTURE_ENABLED`).
12. **Ante cualquier `Traceback`, salida inesperada o duda en un paso
    [ESCRIBE]: PARA.** Guarda el log completo en `$W`, no repitas el comando,
    y pregunta al propietario con el síntoma exacto (usa la tabla §II.3).

## 3. Decisiones del propietario (D1–D5, §8 de la Parte I)

Pregúntalas al empezar (P0) pegando la tabla de §8. Si no hay respuesta,
aplica la **opción rápida** y déjalo escrito en `$W/decisiones.md` y en el
acta. Nunca apliques una opción estricta que amplíe alcance sin respuesta.

## 4. Orden de ejecución y alcance

Sigue el orden P0 → P15 del manual. Camino crítico: P4 → P5 → P7 → release →
P8 → P9 → P13 → P15. Puedes solapar P2 (observación del aviso a las 07:15 UTC),
P6 y las decisiones. **No** solapes suites con cortes ni dos cortes.

Alcance cerrado: 13 fuentes R5 a nativo (dos cortes), portado de `irishjobs` y
`financejobs`, `canton` en fuentes suizas, cabeceras de navegador para
NAV/Jobgether, retirada de R5/CDC/trigger/erasure-cdc/frontend-r5, beat sin
tareas de slot, acta única.

**Fuera de alcance, no lo hagas aunque parezca fácil:** activar `jobicy`,
reabrir `stelle_admin`/`schuljobs`, portar scrapers escolares, crear un
endpoint de ingesta en core, retirar la cosecha del Portfolio, tocar el ranker,
políticas, modelos, holdout, punto 5, cron de retención, `:prod`, deshabilitar
`jobhunt.shadow.project`, borrar tablas/volúmenes legacy, refactorizar por
estética, actualizar dependencias.

## 5. Cómo trabajas y cómo informas

- Empieza cada sesión pegando el bloque de variables de §II.1 y ejecutando P0.
- Antes de cada paso, escribe en una línea: `Pn · objetivo · [ESCRIBE|lectura]`.
- Tras cada paso, informa en **≤6 líneas**: comando clave, salida literal
  relevante (recortada, sin secretos), coincide/no coincide con lo esperado,
  recibo guardado. Sin narrativa, sin repetir el manual.
- Si un paso queda parcial, dilo literalmente («P8 parcial: 11/13 fuentes ok;
  ostjob deshabilitado por X») — no lo llames «hecho».
- Tiempo: registra horas efectivas por paso en `$W/tiempos.md`. Esperas
  (suites, drenajes, ventanas) aparte. La estimación del análisis es 20–24 h
  en variante rápida; si vas a superarla, avisa en cuanto lo veas con la causa,
  no al final.
- Commits: sólo cuando el manual lo indica (fin de P7, P13.2, P15) y sólo tras
  un «sí» explícito en ese momento. Un commit por release, mensaje en el
  formato del repo. Nunca `push`.
- Documentación: sólo el acta única de P15 y las tres cabeceras que P15 nombra.
  No crees checkpoints intermedios ni reescribas actas anteriores.

## 6. Criterio de terminado

El punto 4 está cerrado cuando el checklist de §10 de la Parte I está
íntegramente en verde con evidencia en `$W` y en el acta, y el propietario lo
ha visto. Si queda un rojo, el acta lo nombra con su caso preciso; no se
proclama cerrado. Si P6 falla (NAV/Jobgether sin paridad de acceso), el
cierre es **parcial declarado**: R5 residual de 2 fuentes y slot conservado;
lo dices así, no lo maquillas.

Empieza por leer el documento entero. Después ejecuta P0 y espera las
decisiones D1–D5 (o aplica la opción rápida si el propietario te lo indica).
