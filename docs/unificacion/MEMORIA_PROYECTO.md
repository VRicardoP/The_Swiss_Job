# Memoria del proyecto JobHunting

Última reconciliación documental: **2026-10-13**.

Esta es la memoria vigente del sistema formado por SwissJob, jobhunt-core y
ReactPortfolio. Resume estado, autoridades y trabajo pendiente. Los contratos y
runbooks se conservan en los directorios `docs/unificacion` y `docs/audits`.

## Propósito y arquitectura vigente

El sistema unifica cosecha, normalización, identidad, deduplicación, matching y
estado durable de empleo en jobhunt-core. SwissJob y ReactPortfolio conservan
sus interfaces, autenticación y responsabilidades propias de BFF.

- Core es autoridad del corpus, catálogo, matching, facetas/categorías,
  feedback, candidaturas, búsquedas guardadas, documentos y colegios.
- SwissJob conserva localmente el perfil editable y lo sincroniza a Core. Sus
  costuras de catálogo, matching y colegios tienen autoridad fija; ya no existe
  routing runtime ni fallback silencioso de escritura.
- Portfolio conserva once fuentes propias y tiene nueve fuentes transferidas
  desactivadas. Seis capacidades resuelven exactamente a core_primary. Esta
  convivencia es transitoria y queda reabierta como DT-144; no constituye la
  arquitectura final.
- Un solo proceso Core de concurrencia 1 carga el bi-encoder. Cosecha,
  operaciones/beat y matching usan workers separados.
- core-capture, su slot de replicación, staging, productores duplicados, el
  scraper escolar duplicado y el código histórico de las fuentes transferidas
  fueron retirados después del cierre formal de rollback.

## Estado vivo comprobado

Sondas de solo lectura ejecutadas el 2026-10-13:

| Pieza | Release/imagen viva | Resultado |
|---|---|---|
| Core API y tres workers | swissjob-core:point5-254e826 | ready, authoritative, Alembic core0054; 17/17 scopes saludables, cero alertas |
| SwissJob BFF/worker | point5-4017081 | healthy/Celery pong; autoridades fijas, dos perfiles entregados, cero errores pendientes |
| SwissJob frontend | point5-254e826 | healthy; categorías canónicas y tarjetas con contenido |
| Portfolio API/worker | portfolio-backend:board-444f072 | healthy; Alembic uu44v3375x21; 1 GiB y 1 CPU por proceso |
| Portfolio frontend público | remoto 5045a28 | cookie-only publicado; login real y 12 ventanas con contenido verificadas por Playwright |

Core registra 25.382 entregas, cero dead/pending/inflight, staging cero y slot de
captura ausente. SwissJob conserva 12 providers, 13 scrapers —ocho escolares— y
ningún módulo retirado importable. Portfolio conserva nueve fuentes transferidas
desactivadas y once propias.

## Optimización cerrada

Los seis pasos autorizados del 10 de octubre están completos, en orden:

1. una copia del bi-encoder y límites;
2. salud 17/17 y retirada de core-capture;
3. desactivación de cosecha duplicada y scraper escolar duplicado;
4. facetas y categorías autoritativas en Core detrás de bandera, comparadas;
5. rollback cerrado formalmente;
6. motores, costuras y código histórico retirados.

Resultados principales frente a las líneas base conservadas:

- bi-encoder controlado: -605,64 MiB PSS (-43,08 %);
- reposo comparable: -266,81 MiB (-10,08 %);
- carga diaria equivalente: 3.667,175 -> 167,499 s (-95,43 %);
- memoria mediana de esa carga: 722,4 -> 443,95 MiB (-38,55 %);
- arrastre del dashboard: 43,25 -> 31,12 ms/frame de media y
  100,1 -> 50,0 ms de peor p95;
- lecturas SwissJob, 100 muestras: facetas p95 0,180 s, categoría 1,033 s,
  guardados 1,551 s y catálogo 0,838 s; todas cumplen p95 <= 2 s.

Los límites causales, protocolos y artefactos están en
[OPTIMIZACION_CORE_PORTFOLIO_2026-10-10.md](../audits/OPTIMIZACION_CORE_PORTFOLIO_2026-10-10.md)
y su directorio de evidencias.

## Cierre de deuda y exclusiones

- **DT-144:** abierta. La cosecha todavía no pertenece exclusivamente a Core.
  El [contrato de centralización total](CONTRATO_CENTRALIZACION_TOTAL.md) fija
  el objetivo vinculante, las fases, los controles contra desviaciones y la
  única definición válida de terminado.

- **A19-31:** cerrado. BFF y worker comparten la cuenta SMTP autorizada, están
  saludables y completaron dos envíos reales; rollback fechado conservado.
- **DT-54:** cerrado. Backend y frontend cookie-only publicados; lint, build,
  413 pruebas, 18 E2E y recorrido Playwright real de 12 ventanas en verde.
- **DT-47, A19-19 y A19-20** quedan fuera por instrucción expresa del propietario.

DT-72, DT-102, DT-115, A18-05, A19-13, A19-15 y A19-25 están cerrados con
checkpoints reproducibles en [DEUDA_TECNICA.md](DEUDA_TECNICA.md).

## Metodología de continuidad

Antes de cada cambio se comprueba el estado real y el recorrido completo. Se
caracteriza el contrato, se aplica la mínima solución cohesiva, se prueba el
borde modificado, se despliega un SHA exacto con rollback y se repite la sonda
sobre producción. Las comparaciones mantienen protocolo y número de muestras,
separan frío/caliente y no atribuyen causalidad sin aislar la variable. Cada
punto se documenta con causa, cambio, pruebas, release, límites y reanudación.

El alcance anterior permanece cerrado, pero DT-144 abre una nueva ejecución con
punto de reanudación en su Fase -1. No deben incorporarse automáticamente DT-47,
A19-19 ni A19-20, que continúan excluidos por decisión expresa del propietario.
