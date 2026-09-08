# E.7 y matching visible: implementación, despliegue y rendimiento

## Resultado operativo, 2026-09-08 noche

Portfolio NAS ejecuta **2eb5f31 / rr11s0042u18**, imagen
`portfolio-backend:e7-2eb5f31`. La ventana de matching consume ahora realmente el
feed core. El routing anterior `matching=core_read` no bastaba: sus rutas de
background eludían el resolver y ejecutaban el motor local.

Canary autenticado contra la instancia desplegada:

- GET `/api/v1/ai-match/analyze/result`: 200, `source=core`, 50 ofertas, 3,918 s.
- POST `/api/v1/ai-match/analyze/start?force=true`: 200, mismas 50, 1,086 s.
- Consulta directa contemporánea a CoreMatching: mismas identidades.
- SHA-256 de IDs ordenados, sin publicar contenido:
  `9463f913dcdec3c9a71d4f9f26dee28e3ee9de0a45609f1f7f93c749d9b0c8b5`.
- Progreso `done / persisted_feed / 100 / core`; 0 operaciones documentales pendientes.
- URL pública ngrok `/health`: 200/healthy. Se conserva el alias `backend`.

No se ha flipado la autoridad de documentos: su modo por defecto sigue local.
La lógica nueva de entrega core está instalada pero no se declara ejercitada en
producción. Core NAS permanece en su release/esquema previos; este relevo no
despliega core0043 ni cambia modelos, cohortes, calidad o retención de datos.

## Correcciones y regresiones

### E.7 — generación y recuperación documental

Backend `497ad74`, frontend `a88649d`:

- Selección fresca y única de autoridad para generar/listar/leer/PDF/borrar/retener;
  lectura ilegible no usa caché rancia. Escrituras locales toman el lock de routing
  antes del lock de candidatura. Freeze bloquea mutaciones del router.
- Generación fuera de transacción/locks, revalidación de candidatura y autoridad
  antes de preparar la entrega. Cambio de autoridad durante el LLM → 409, no segundo
  escritor. Durante un corte real sigue siendo obligatorio freeze y drenar HTTP.
- El cliente conserva UUID de operación por intención. El servidor guarda el cuerpo
  terminado antes del HTTP y recupera la entrega sin repetir LLM, incluso después
  del borrado de la candidatura. ACK perdido → 202; intención distinta → 409;
  miembro ya eliminado → 410, nunca resurrección automática.
- Cola autenticada de pendientes y retry explícito; recibos expirados no se reenvían
  ciegamente. La UI distingue pendiente/entregado y no borra documentos previos.
- Biblioteca usa el documento más reciente de cada tipo, no el último recorrido
  de un listado descendente. PDF libera la sesión antes del trabajo de CPU.
- Retención sigue la misma autoridad y respeta freeze; no borra local como fallback
  de un fallo core. No se ha implementado con ello el erase integral del propietario.

Cuatro regresiones del wiring fallaron antes de conectar el router. Fichero nuevo:
`tests/test_document_workflow.py` (14 casos). Arnés externo
`scripts/test_document_adapter_contract.py`: **4 passed**, incluyendo FastAPI BFF,
HTTP/auth/core/PG reales, commit→ACK perdido→candidatura eliminada→retry sin LLM.
PG: `scripts/test_portfolio_document_transactions.py`, **1 passed**.

### Matching que eludía la costura

Backend `2eb5f31`, frontend `4bfabe7`:

- `/analyze/start`, `/analyze/result` y `/analyze/progress` respetan ahora el modo
  fresco de routing. `core_primary`/`rollback_pending`: error core → 503, nunca
  resultado local antiguo. `core_read`: fallback local explícito y observable.
- La carga de arranque no embebe ni recalcula el matching local en modos core.
  El fallback canary por solicitud explícita permanece disponible. No se apagan
  globalmente schedulers, colegios, alertas o cosecha sin tener su sustitución.
- Conserva el tamaño del contrato de background (top_k=100, rerank_top=50).
- La UI instala directamente el feed, sin pedirlo dos veces ni sondear un runner
  local para una lectura ya terminada. El error de API ya no simula un feed vacío.

Las **9 regresiones** de `test_matching_background_authority.py` fueron rojas antes
del fix. Suite final backend: **1951 passed, 1 skipped**, 193,54 s; sin suites
paralelas ni exclusión de tests. Cuatro warnings de Alembic ya existentes.

### Paquetes de interfaz

Frontend `3f6d7be`: react-leaflet y @react-leaflet/core se agrupan con Leaflet,
no con React. El build deja de producir el ciclo vendor-maps↔vendor-react y quita
el preload inicial del mapa. Revisión de frontera:
`node scripts/check-build-chunks.mjs`. Sin dependencias nuevas.

- Suite frontend final: **387 passed**, repetida tras el último commit.
- Build con API real: correcto, sin advertencia circular ni variable API ausente.
- E2E Chromium visible, un worker, backend simulado: **17 passed** (14,1 s).
- Skill Playwright usada para smoke público: HTTP 200, 31 botones, sin pageerror
  ni assets JS fallidos, sin overflow a 1440×900 y 390×844. Esa comprobación pública
  corresponde al frontend anterior, NO certifica los commits nuevos publicados.
- El primer intento de test de build en jsdom falló por el realm de TextEncoder
  al cargar esbuild; se reemplazó por comprobación Node, sin cambiar los tests DOM.

## Rendimiento medido, no estimado

Dos sondas idénticas dentro del NAS, 12 muestras por ruta, concurrencia 2,
pausa 0,1 s por pareja. Cada una: 48/48 respuestas 200 más 4 preflights 200.
Colecciones de documentos/candidaturas/búsquedas vacías: **no es una prueba de
capacidad con grandes volúmenes**, ni certificación de LLM/PDF/escrituras.

| Ruta | p95 antes (ms) | p95 después (ms) |
| --- | ---: | ---: |
| health | 328,89 | 68,64 |
| documentos | 351,47 | 473,32 |
| candidaturas | 808,12 | 186,19 |
| búsquedas | 1909,05 | 228,53 |

Antes: 21:01 UTC; después: 21:32 UTC. Primera sonda anterior dio timeout de 30 s;
no se oculta. La versión inicial no imprimió la ruta del timeout; no se le atribuye
una causa exclusiva. Se mejoró el diagnóstico sin aumentar la carga.

Snapshots de Docker: Portfolio pasó de CPU **70,40 % / 1,076 GiB** a
**0,28 % / 174,8 MiB**. Antes llevaba ~40 minutos en embedding 1664/2580; después
el feed era persistido. Son muestras bajo carga diferente, no garantía permanente
ni benchmark aislado. Documentos empeora en p95: no se declara mejora universal.

La sonda reproducible queda en `scripts/nas_portfolio_read_benchmark.py`: token
efímero creado dentro del contenedor, salida de timings/conteos, sin CV ni secretos.

## Release y protección de datos

- Build desde `git archive 2eb5f31`, runtime existente, red de build desactivada.
- Imagen config SHA-256 local y NAS:
  `af277ab7ee0f519f893ea5bdd0d2011df03a215b76590d146ac468269aaafee1`.
- Configuración/backup/script permanecen privados (0700/0600) en
  `/share/CACHEDEV1_DATA/Public/portfolio-release-e7.XXQ3LVdj/`.
- Se detuvo el backend anterior, se tomó pg_dump y se comprobó upgrade sin cambio
  de head. El proceso anterior acabó 137 tras el plazo de parada: no presentar eso
  como drenaje gracioso. Ningún journal ni documento pendiente estaba presente;
  PostgreSQL conserva la atomicidad de las transacciones interrumpidas.
- Nuevo contenedor conserva redes, IPs, puerto, alias, proxy concreto y caché HF.
- Recuperación: `nas_release.sh rollback`, contenedor
  `portfolio_backend_pre_2eb5f31` retenido. No restaura toda la BD ni ejecuta
  downgrade. La rama automática no fue necesaria; no afirmar que se ensayó viva.
- Huellas tras E.7 frente al relevo anterior: estructura/revisión idénticas;
  cv_profiles (7), schools (6), school_jobs (88), saved_searches (0),
  generated_documents (0), iguales por contenido. Evidencia privada
  `nas-after-e7.json`; no incluye payloads personales.

## Bloqueo concreto de publicación y trabajo siguiente

Frontend local HEAD `3f6d7be`; **no publicado**. Dos intentos de push fueron
rechazados por revisión automática: destino GitHub público, falta autorización
específica para estos commits. Se verificó remoto y API pública del repositorio,
y diff saliente: solo código/pruebas/traducciones. No se eludió la denegación.
Se solicitó permiso explícito para publicar `a88649d`, `4bfabe7`, `3f6d7be` en
`VRicardoP/ReactPortfolio`. La publicación anterior 8836254 permanece.

Orden de continuación, sin repetir trabajos cerrados:

1. Con permiso de publicación, push normal (sin force), comprobar CI/Cloudflare
   y assets reales, repetir smoke y canary UI. El backend nuevo es compatible
   con el frontend anterior; la ruta visible ya devuelve feed core.
2. Cerrar documentos SwissJob: operación/cuerpo durable, autoridad y freeze,
   generación fuera de tx, exactitud de PDF/contexto y recuperación sin otro LLM.
3. Probar inbox de ambos consumidores, erase/retención del journal, migración con
   UUID/fechas/hash y rollback con escrituras nuevas sobre copia estricta. Solo
   entonces core0043 + scopes + freeze/canary/flip documental. No usar datos cero
   de Portfolio como sustituto del ensayo no vacío ni apagar el escritor antes.
4. Colegios y retirada de fuentes: inventariar cada reemplazo. Logs del arranque
   registran HTTP 404, fin de paginación WordPress 400 y timeout transitorio;
   no afirmar salud de todas las fuentes. Revisar la clasificación de fin de
   página (actualmente entra como fallo de fetch/circuit), el endpoint 404 y su
   sustituto antes de retirar el proveedor. No confundirlo con error del feed core.
5. Cerrar F con drenaje y reversibilidad, carga representativa y presupuestos
   medidos, no con muestras de colecciones vacías. Los tests verdes no garantizan
   ausencia de bugs futuros. Calidad sigue separada: exige examen independiente
   válido, sin fabricar etiquetas, métricas ni siete días transcurridos.

YAGNI guió la reutilización de routing, journal, adaptador y suite existentes;
no se añadió otra cola, motor de PDF, dependencia ni framework de almacenamiento.

## Preflight adicional de retirada F

Consulta de solo lectura en `swissjob-core-api-r5` (sources + harvest_scopes +
source_scope_state): **ningún scope de cosecha directa habilitado**. Los scopes
presentes son `legacy:*`, disabled, sin last_complete de fetch propio. Incluyen
portales y colegios; los registros de evaluación no son fuentes productivas.
Esto no declara averiado el CDC: demuestra que no puede retirarse el productor
legacy suponiendo que el core ya cosecha esas fuentes directamente. F exige
portar/activar y medir cada sustituto, o conservar explícitamente el adaptador
productor; apagarlo antes reduciría la entrada de ofertas.

## Regresión completa adicional del core

**1109 passed, 1 skipped, 2 warnings**, 799,75 s, suite completa en serie tras
Portfolio/frontend. No se modificó código core ni se relajó un test. La primera
invocación se detuvo en colección porque faltaba el montaje de scripts de cutover;
se corrigió el entorno, y la segunda terminó íntegra:

```sh
docker compose -f docker-compose.yml -f docker-compose.core-local.yml run --rm --no-deps \
  -v /home/lothar/Public/SwissJob/jobhunt_core:/app/jobhunt_core:ro \
  -v /home/lothar/Public/SwissJob/backend/scripts:/app/backend/scripts:ro \
  core-migrate python -m pytest jobhunt_core/tests -q --tb=short \
  --show-capture=no -o cache_dir=/tmp/pytest-cache
```

Comprobación posterior NAS: contenedor running, **0 reinicios** desde la release.
