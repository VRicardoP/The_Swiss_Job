# Optimización de Core, SwissJob y ReactPortfolio

Inicio: **2026-10-10**. Cierre funcional: **2026-10-13**.

Estado: **completa, 18/18 puntos y los seis pasos autorizados cerrados**. Esta
acta sustituye la fotografía parcial inicial; los artefactos crudos permanecen
en [2026-10-10-optimization/](2026-10-10-optimization/).

## Objetivo y método

Simplificar y optimizar sin perder funciones, en este orden:

1. una sola copia del bi-encoder y ajuste de límites;
2. salud de cosecha y retirada de core-capture;
3. fuentes duplicadas y scraper escolar duplicado;
4. facetas/categorías en Core detrás de bandera y comparación;
5. cierre formal de rollback;
6. retirada de motores, costuras y código histórico.

Se comprobó el estado real antes de cada mutación, se fijó una línea base, se
caracterizaron consumidores y contratos, se aplicó el cambio mínimo que
preservaba SRP/cohesión/acoplamiento/legibilidad, se ejecutaron pruebas y se
desplegó un SHA exacto con copia .before. Cada gate se repitió sobre producción.
Las comparaciones conservan protocolo, conjunto y muestras; frío y caliente se
separan y los límites causales se declaran.

## Resultado por paso

| Paso | Resultado final |
|---|---|
| 1. Bi-encoder y límites | Worker de matching con concurrency=1: una sola copia del modelo. API/worker Portfolio limitados a 1 GiB y 1 CPU cada uno; límites Core conservados. |
| 2. Salud y captura | IrishJobs prueba ambos hosts y las 17/17 fuentes están sanas. core-capture retirado, staging=0 y slot CDC ausente. |
| 3. Duplicados | Nueve fuentes transferidas están desactivadas en Portfolio; el scraper escolar programado duplicado se retiró. SwissJob conserva 12 providers y 13 scrapers, ocho escolares. |
| 4. Facetas/categorías | Core calcula la taxonomía y expone facetas/categorías; SwissJob las activa por bandera, sirve 50 tarjetas y el frontend ya no clasifica con 600 reglas duplicadas. |
| 5. Rollback | Autoridades en core_primary, credenciales y entregas verificadas, puntos .before conservados y gates de vuelta atrás cerrados. |
| 6. Simplificación | Routing runtime, motores locales transferidos, módulos de fuentes, variables LEGACY_DISABLED, captura y costuras históricas eliminados. Las autoridades son fijas y estrechas. |

## Resultados medidos

| Medición comparable | Antes | Después | Delta/veredicto |
|---|---:|---:|---|
| Bi-encoder, PSS controlado | 1.405,75 MiB | 800,11 MiB | -605,64 MiB (-43,08 %) |
| Reposo comparable | — | -266,81 MiB | -10,08 % |
| Carga diaria equivalente | 3.667,175 s | 167,499 s | -95,43 % |
| Memoria mediana de carga | 722,4 MiB | 443,95 MiB | -38,55 % |
| Arrastre dashboard, media | 43,25 ms/frame | 31,12 ms/frame | -28,05 % |
| Arrastre dashboard, peor p95 | 100,1 ms | 50,0 ms | -50,05 % |
| SQL de facetas | base | final | -91,48 % |
| Página por categoría | base | final | -53,48 % |

La carga diaria final neutralizó únicamente entrega SMTP y escritura del ledger
para no enviar mensajes ni mutar estado fuera de horario; mantuvo fuentes,
matching, feed Core y colegios. Por ello el delta refleja el cálculo, no el coste
trivial de composición/correo. La mejora de arrastre usa seis gestos y dos
corridas públicas; se conservaron también muestras desfavorables.

### Contrato servido actual

Después del cierre original se volvió a medir la interacción vigente con 100
muestras por ruta y presupuesto predeclarado p95 <= 2 s:

| Ruta | Base p50/p95 | Final p50/p95 | Estado |
|---|---:|---:|---|
| Facetas | 2,943 / 5,991 s | 0,088 / 0,180 s | cumple |
| Categoría, 50 tarjetas | 5,152 / 10,876 s | 0,371 / 1,033 s | cumple |
| Guardados | 0,689 / 1,723 s | 0,564 / 1,551 s | cumple |
| Catálogo, 20 | 1,530 / 7,579 s | 0,186 / 0,838 s | cumple |

Core publica job_category, el BFF reutiliza el feed calentado, cachea joins
escolares y mantiene 60 s las páginas de catálogo antes de revalidar su ETag.
Un control inmediatamente anterior a la última corrección dio catálogo
5,166/9,731 s, confirmando que la reconstrucción remota dominaba el coste.

## Funcionalidad y contenido

- Portfolio: las 12 ventanas cargaron contenido sin excepciones JavaScript en
  las dos corridas públicas finales.
- SwissJob: frontend lint, 57/57 pruebas y build Vite verdes; BFF y Core pasaron
  sus suites completas durante la ejecución (1.919 BFF, cuatro xfail esperados;
  1.547 Core, una omitida).
- Presentación viva sobre 1.800 matches: 975 Arbeitnow y 484 Jobgether con
  descripción no vacía y cero HTML; cuatro tarjetas escolares con descripción,
  URL y detalle HTTP 200.
- La clasificación es autoridad de Core. El frontend conserva sólo metadatos de
  presentación y agrupa por job_category.
- Análisis de imports de carga: cero ciclos en 185 módulos BFF/558 aristas y
  116 Core/342. Radon no encuentra E/F en runtime; las siete funciones restantes
  son herramientas de corte ya ejecutadas.

## Estado operativo final

Sondas de solo lectura del 2026-10-13:

- Core point5-254e826: ready, authoritative, core0054, 17/17 fuentes, cero
  alertas, 25.382 entregas y ninguna dead/pending/inflight.
- SwissJob BFF/worker point5-4017081: healthy/pong, dos perfiles sincronizados,
  facetas/feedback/perfil activos y ningún módulo retirado importable.
- Portfolio board-444f072: healthy, uu44v3375x21, schedulers activos, nueve
  fuentes transferidas desactivadas, once propias y seis capacidades exactamente
  en core_primary.
- Ocho contenedores afectados, cinco muestras/40 s: 2,89 % CPU mediana agregada,
  2.830,06 MiB y 55 procesos. El NAS conserva dos CPU y carga global alta por
  QTS; aun así el contrato servido cumple.

## Rollback y releases

Puntos de vuelta atrás más recientes:

- core.configured.yml.before-core-254e826;
- swissjob.configured.yml.before-bff-4017081;
- swissjob.configured.yml.before-worker-4017081;
- swissjob.configured.yml.before-frontend-254e826;
- portfolio.configured.yml.before-444f072.

Las copias saneadas de compose no contienen secretos. La recuperación restaura
el .before correspondiente y recrea únicamente el servicio afectado con el
proyecto compose indicado por los scripts de despliegue.

## Deuda fuera del cierre de optimización

- A19-31: la cuenta SMTP SwissJob necesita una contraseña de aplicación válida
  o permiso literal para reutilizar la cuenta Portfolio.
- DT-54: backend cookie-only desplegado; frontend 5045a28 validado y pendiente
  de publicar/verificar.
- DT-47, A19-19 y A19-20 están excluidos por instrucción del propietario.

Punto exacto de reanudación: completar esos dos efectos externos autorizados,
recorrer el frontend autenticado final y publicar los commits locales en los
destinos expresamente aprobados.
