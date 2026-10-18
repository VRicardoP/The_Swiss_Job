# Plan de acción post-DT-144 — simplificación y eficiencia

> **Estado:** EN EJECUCIÓN · P0-P3 implementados y validados localmente; pendiente de P4, CI y despliegue.
>
> **Objetivo final:** cerrar la última vía de cosecha externa fuera de Core,
> retirar el código que quede sin responsabilidad productiva y reducir el coste
> de construir, transferir y mantener las imágenes, sin perder ni degradar
> ninguna función.
>
> **Bases inmutables verificadas:** SwissJob/Core `069f83a` (Core productivo
> `5b3bf3d`, BFF/worker `5002445`) y ReactPortfolio backend `c47b7a3`.
> Ramas de ejecución: `feat/post-dt144-simplification` en ambos repositorios.

## 1. Reglas imperativas

Estas reglas prevalecen durante toda la ejecución y no se pueden reinterpretar
para declarar un punto terminado antes de demostrarlo:

1. **Nunca asumas nada: compruébalo todo antes de tomar una decisión.** Toda
   decisión debe apoyarse en código recorrido hasta sus llamadores, pruebas,
   configuración efectiva o medición reproducible. Una ausencia de errores no
   demuestra por sí sola que una función siga operativa.
2. **Principios arquitectónicos (SIEMPRE):** responsabilidad única (SRP),
   cohesión alta, acoplamiento bajo, legibilidad y consistencia. Si chocan con
   YAGNI, se aplicará el mínimo que satisfaga ambos; nunca se añadirá una
   abstracción sólo para aparentar el cumplimiento de un principio.
3. **Realizar todo cambio propuesto.** Ningún punto de este plan queda como mera
   recomendación. Sólo puede descartarse una implementación cuando una prueba
   reproducible demuestre que rompe una función o contradice el objetivo; en
   ese caso se documentarán la evidencia, el límite y la alternativa mínima que
   sí se ejecuta.
4. No se elimina validación de fronteras, seguridad, privacidad, control de
   pérdida de datos, manejo de errores necesario, accesibilidad ni una función
   observable. Cada retirada sustituye antes cualquier responsabilidad viva.
5. Tras cada punto se añadirá a `DEUDA_TECNICA.md` un checkpoint con causa,
   cambio, pruebas, métricas, SHA, estado de despliegue, límites y punto exacto
   de reanudación. Los commits serán estrechos y no absorberán cambios ajenos.

## 2. Evidencia de partida

- ReactPortfolio conserva un colector escolar manual. Los endpoints
  `POST /schools/refresh` y `POST /schools/{id}/refresh` fuerzan
  `manual_refresh=True`; esa bandera elude la delegación a Core y permite que el
  BFF consulte páginas externas, use Jina/Groq y escriba después la observación
  en Core. Core es la autoridad de persistencia, pero no el único colector en
  ese recorrido.
- El ratchet `scripts/check_local_harvest_ownership.py` enumera los 40 módulos
  retirados de portales, pero no prohíbe `school_scraper`, `school_extractor`,
  `school_classifier`, `school_urgency` ni su llamada manual.
- Esos cinco módulos suman 1.109 líneas. Junto con las partes de
  `job_service.py` y `jobs_config.py` que sólo sostienen la cosecha retirada, la
  superficie candidata alcanza aproximadamente 1.700 líneas; el número final
  se contará después de preservar los pocos símbolos vivos.
- Imágenes locales exactas: SwissJob BFF y worker, 996.337.717 bytes cada una;
  ReactPortfolio backend, 893.031.655 bytes; Core, 2.196.087.738 bytes. La capa
  APT de SwissJob ocupa 336 MB y la de Portfolio 240 MB. Son líneas base, no
  ahorros prometidos.
- Las imágenes productivas instalan herramientas de prueba/desarrollo. Además,
  SwissJob conserva `lxml` sin import productivo observado. Toda retirada se
  confirmará con grafo de imports, construcción limpia, `pip check`, import
  smoke y suites; no basta con una búsqueda textual.
- Radon identifica dos concentradores de ramas en caminos vivos:
  `ReactPortfolio.services.core_source_compat._job` (F/88) y
  `jobhunt_core.harvest.providers.native_json._content` (F/58). La finalidad de
  dividirlos es SRP, legibilidad y aislamiento de contratos por fuente; no se
  atribuirá una mejora de velocidad sin benchmark.
- Hay 6.476 líneas en herramientas de importación/cutover. Algunas son
  exclusivamente históricas y otras todavía suministran primitivas a runtime o
  recuperación. Está prohibido borrarlas en bloque.

## 3. Orden obligatorio de ejecución

### P0 · Cerrar el colector escolar manual de ReactPortfolio

1. Caracterizar con pruebas el contrato HTTP de ambos endpoints de refresco y
   el mapeo colegio → fuente/scope de Core.
2. Reutilizar `POST /v1/harvest/sources/{source}/runs` con
   `Idempotency-Key`; no crear otro dispatcher.
3. Hacer que los endpoints de Portfolio sólo validen/autoricen y despachen al
   Core. Eliminar toda petición a portales, Jina o Groq desde Portfolio.
4. Retirar los módulos escolares locales y sus pruebas exclusivamente locales;
   conservar o reemplazar las pruebas de contrato del refresco Core.
5. Ampliar el ratchet para prohibir módulos, dependencias, configuración y
   llamadas de cosecha escolar local. El control negativo debe fallar si vuelve
   cualquiera de ellas.
6. Gate: suites del backend, prueba de contrato contra Core, imagen construida,
   sonda sin tráfico externo desde Portfolio y refresco funcional observado.

### P1 · Reducir dependencias e imágenes de producción

1. Separar dependencias de runtime y desarrollo sin duplicar versiones ni
   introducir un gestor nuevo. CI instalará explícitamente las de desarrollo;
   las imágenes sólo las de runtime.
2. SwissJob: comprobar y retirar `pytest`, `pytest-asyncio`, `ruff` y `lxml` de
   producción. Core: comprobar y retirar `pytest`. Portfolio: comprobar y
   retirar `black`, `pytest`, `pytest-asyncio`, `pytest-timeout` y cualquier
   dependencia directa sin consumidor real (`requests`/`psycopg2-binary` son
   candidatas, no conclusiones).
3. No conservar compiladores/cabeceras en la capa final. Usar el mínimo cambio
   Docker que permita compilar y purgar en la misma capa o una etapa de build si
   es imprescindible; mantener las bibliotecas de runtime de WeasyPrint y el
   healthcheck.
4. Construir una sola imagen inmutable SwissJob por SHA y usarla para API y
   worker. Ambos procesos mantienen comandos, colas, límites y sondas propios.
5. Gate: builds desde caché vacía, `pip check`, smokes de import, suites,
   healthchecks y comparación de bytes/capas con la línea base. No declarar
   ahorro hasta medir la imagen candidata.

### P2 · Reducir complejidad de transformadores por fuente

1. Congelar primero, mediante los tests de fidelidad existentes, la salida de
   cada fuente y los casos hostiles.
2. Dividir `_job` y `_content` en transformadores cohesionados por fuente o
   familia y seleccionarlos con un despacho explícito. Reutilizar familias sólo
   donde ya comparten contrato; no crear una jerarquía ni una abstracción de un
   solo uso.
3. Gate: igualdad exacta de payloads, mismas excepciones, suites completas,
   Ruff/formato y nueva medición Radon sin grados E/F en esas dos unidades.

### P3 · Podar herramientas de migración congeladas

1. Construir el grafo real de imports desde API, workers, CLI de operación,
   scripts de recuperación y pruebas. Clasificar cada módulo como runtime,
   recuperación vigente o ejecución consumida.
2. Extraer una primitiva compartida sólo si tiene un consumidor vivo y su
   módulo contenedor puede retirarse. No mover código para mejorar una métrica.
3. Eliminar módulos y pruebas cuya ejecución esté consumida y que no formen
   parte de un rollback todavía válido. Si existe una retención operativa, se
   conserva el mínimo recuperable con fecha/condición de retirada explícita.
4. Gate: imports de API/workers, CLIs vigentes, restore/rollback que continúe
   autorizado, suites Core y recuento antes/después.

### P4 · Validación, despliegue y cierre

1. Ejecutar suites completas de Core, SwissJob y ReactPortfolio, lint/formato,
   builds y comprobaciones de secretos/propiedad.
2. Desplegar Core antes que consumidores cuando cambie un contrato; después
   SwissJob y ReactPortfolio. Usar imágenes por SHA y conservar rollback.
3. Repetir las tres sondas terminales y añadir una sonda negativa específica:
   ningún proceso de Portfolio puede resolver ni contactar hosts de portales o
   Jina durante un refresco escolar.
4. Medir imágenes, tiempo de build/transferencia, recursos y rutas funcionales
   con el mismo protocolo previo. Conservar también resultados desfavorables.
5. Cerrar la deuda únicamente cuando todos los cambios anteriores estén
   desplegados y verificados en la release exacta.

## 4. Definición de terminado

- Core es el único proceso que contacta fuentes externas, también en refrescos
  manuales; Portfolio y SwissJob sólo consumen o despachan al Core.
- Los ratchets fallan ante cualquier reintroducción de cosecha local.
- Se han retirado los módulos, pruebas, configuración y dependencias sin
  responsabilidad productiva comprobada.
- API y worker SwissJob usan la misma imagen inmutable y mantienen sus roles.
- Las dos unidades F/88 y F/58 quedan divididas sin alterar payloads.
- Las herramientas de migración quedan reducidas al mínimo runtime/recuperable
  demostrado, con cualquier retención restante explícita y verificable.
- Todas las suites, builds, CI, sondas productivas y mediciones finales están
  verdes y enlazadas desde la deuda.

## 5. Reanudación exacta

Leer primero este documento y después el bloque DT-145 al principio de
`DEUDA_TECNICA.md`. Trabajar únicamente en:

- `/tmp/swissjob-dt144-deploy-a2ba944`, rama
  `feat/post-dt144-simplification`, base `069f83a`;
- `/tmp/reactportfolio-dt144-deploy-b587c3e`, rama
  `feat/post-dt144-simplification`, base `c47b7a3`.

Antes de cada cambio comprobar `git status`, HEAD, consumidores e invariantes.
P0 quedó implementado en Core `1736dd6` y ReactPortfolio `08ca961`: el
colector escolar manual ya no existe y el ratchet impide recuperarlo. P1 quedó
implementado en SwissJob/Core `68f21c6` y ReactPortfolio `1c795f9`; los tres
builds sin caché, `pip check`, imports y Compose pasaron, con una reducción
agregada única de 802.060.265 bytes. P2 quedó implementado en Core `ba47ec8` y
ReactPortfolio `cdf9996`: los despachadores bajaron de F/58 a A/2 y de F/88 a
A/4 sin cambiar sus payloads.

P3 quedó implementado en Core `1f415d3`. El grafo real clasificó los 17 módulos
previstos: se retiraron 15 herramientas de corte consumidas; se conservaron
`migrate.py` y toda la cadena Alembic, la recuperación vigente
`erasure_restore.py` usada por `nas_backup_daily.sh`, y la síntesis runtime de
candidaturas, extraída como `portfolio_vacancies.py`. También se retiraron 4
auxiliares exclusivos del corte, 6 scripts de ensayo y 23 pruebas históricas.
Resultado del commit: 58 ficheros, 166 inserciones y 15.872 eliminaciones
(−15.706 líneas netas). No queda ninguna referencia ejecutable a los módulos
retirados. Validación: compilación/imports, Ruff, formato y `diff --check`
verdes; 58 pruebas integradas focales pasaron tras la extracción. La suite Core
completa anterior a la última limpieza pasó 1.768/1; P4 debe repetirla sobre el
SHA final junto con las suites de SwissJob y Portfolio.

Siguiente punto exacto: P4. Ejecutar validación completa, builds finales,
ratchets y CI; después fusionar y desplegar Core antes que Portfolio, provisionar
y habilitar `swiss_schools_vis`, desplegar consumidores y ejecutar sondas
positivas y negativas. No ejecutar JSearch. Los gates de operación continúan
abiertos hasta completar esas sondas sobre los SHA desplegados.
