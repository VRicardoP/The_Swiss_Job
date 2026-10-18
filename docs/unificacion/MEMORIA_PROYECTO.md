# Memoria del proyecto JobHunting

Última reconciliación documental: **2026-10-18**.

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
- Portfolio no conserva productores externos. Sus seis capacidades resuelven
  exactamente a `core_primary` y consumen el mismo corpus que SwissJob.
- Un solo proceso Core de concurrencia 1 carga el bi-encoder. Cosecha,
  operaciones/beat y matching usan workers separados.
- core-capture, su slot de replicación, staging, productores duplicados, el
  scraper escolar duplicado y el código histórico de las fuentes transferidas
  fueron retirados después del cierre formal de rollback.

## Estado vivo comprobado

Sondas finales ejecutadas el 2026-10-18:

| Pieza | Release/imagen viva | Resultado |
|---|---|---|
| Core API y tres workers | swissjob-core:point5-acf48b2 | ready, authoritative, Alembic core0055; 50/50 scopes con estado, cero alertas |
| SwissJob BFF/worker | swissjob-backend:point5-acf48b2 | healthy/Celery pong; misma imagen inmutable; cero productores locales |
| SwissJob frontend | swissjob-frontend:point5-acf48b2 | healthy; categorías canónicas y tarjetas con contenido |
| Portfolio API/worker | portfolio-backend:board-5492147 | healthy; seis capacidades `core_primary`; cero productores locales |
| Portfolio frontend público | remoto 5045a28 | cookie-only publicado; login real y 12 ventanas con contenido verificadas por Playwright |

Core tiene delivery drenado, cero dead/pending/inflight, staging cero y slot de
captura ausente. SwissJob y Portfolio tienen cero módulos productores,
credenciales de portal y conexiones directas. JSearch conserva un único scope
manualmente deshabilitado y cero ejecuciones desde la dispensa.

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

DT-145 completó la simplificación posterior: cerró el último colector escolar
local, redujo las imágenes de producción, dividió los dos transformadores de
complejidad F y retiró herramientas de corte consumidas. El delta conjunto es
−18.148 líneas netas. En una ventana comparable de ocho contenedores, memoria
mediana agregada bajó de 1.867,87 a 1.232,40 MiB (−34,0 %) y procesos de 54 a
36 (−33,3 %).

VIS quedó habilitado en Core y un refresco iniciado en Portfolio sólo contactó
`core-api`; el ciclo terminó `ok` con tres ofertas activas. Tras verificar todas
las sondas se retiraron 65 etiquetas antiguas no usadas. Se conservó la imagen
del servicio vivo de borrado GDPR. Evidencia completa en
[2026-10-18-dt145-final-optimization.json](../audits/2026-10-18-dt145-final-optimization.json).

## Cierre de deuda y exclusiones

- **DT-144:** cerrada el 2026-10-16. La cosecha pertenece exclusivamente a Core.
  El [contrato de centralización total](CONTRATO_CENTRALIZACION_TOTAL.md) fija
  el objetivo vinculante, las fases, los controles contra desviaciones y la
  única definición válida de terminado.

- **DT-145:** cerrada el 2026-10-18. P0-P4 están fusionados, desplegados y
  verificados; no queda trabajo por reanudar.

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

El alcance de DT-144 permanece cerrado y protegido por su contrato y ratchets.
No deben incorporarse automáticamente DT-47, A19-19 ni A19-20, que continúan
excluidos por decisión expresa del propietario.
