# Auditoría de integración — 2026-09-18

El inventario priorizado, las correcciones propuestas y los criterios de cierre están
en [DEUDA_TECNICA.md](../../../../DEUDA_TECNICA.md), §0.A. Este directorio conserva
las reproducciones sintéticas; no modifica la aplicación ni despliega cambios.

## Alcance y límites

- SwissJob `d9c8a0ae60243216e54d3aa7adca5a8142eba2de` y Portfolio backend
  `26b752a3ec3c424c55e3c0a92ce5ace8cfbe6a9c`; documentación Public
  `2bcbcd916ba0d054bf47fa695f5a4e85a811da68` antes de esta actualización.
- Recorridos inspeccionados: UI → BFF → core de catálogo, matching y colegios;
  notificaciones, productores escolares, fronteras de autorización y validación,
  documentación de despliegue, retención y deuda vigente.
- NAS: comprobaciones de lectura de contenedores, configuración no secreta y
  una petición GET de catálogo. Sin migraciones, escrituras de negocio, SMTP,
  llamadas a proveedores de IA ni cambios de routing/políticas.
- No se ejecutó la suite integral PostgreSQL: la BD SwissJob local estaba parada.
  Los contadores de suites de entregas anteriores no son verificaciones de hoy.
- No es una certificación exhaustiva de seguridad ni de rendimiento bajo carga.
  Las supresiones documentales y cambios previos del propietario se conservaron.

## Reproducciones ejecutadas

**Importante:** las aserciones describen el defecto actual. Su éxito confirma la
reproducción, NO que el producto funcione correctamente. Al corregir, convertirlas
en regresiones del comportamiento esperado y comprobar rojo antes / verde después.

### Catálogo SwissJob: controles visibles rechazados

Desde la raíz SwissJob, con el contenedor local existente y su montaje `backend:/app`:

```sh
docker exec -i -e PYTHONDONTWRITEBYTECODE=1 swissjob-worker python - < docs/audits/2026-09-18/reproduce_swiss.py
```

Resultado: ocho combinaciones de orden/filtros → HTTP 501, cero llamadas de red.
Además, GET real del NAS `/api/v1/jobs/search?sort=oldest&limit=1` devolvió 501.
El script importa el código del montaje; no certifica otras imágenes desplegadas.

### Portfolio: paginación y avisos concurrentes

Ejecutar desde `/tmp` evita cargar el `.env` de SwissJob. Credenciales ficticias:

```sh
cd /tmp
env DEBUG=false \
  DATABASE_URL=sqlite:///audit-unused.db \
  DATABASE_URL_ASYNC=sqlite+aiosqlite:///audit-unused.db \
  ADMIN_EMAIL=audit@example.com ADMIN_PASSWORD=audit-not-a-real-password \
  SECRET_KEY=audit-not-a-real-secret \
  PYTHONPATH=/home/lothar/Public/ReactPortfolio/backend \
  /home/lothar/Public/ReactPortfolio/backend/venv/bin/python \
  /home/lothar/Public/SwissJob/docs/audits/2026-09-18/reproduce_portfolio.py
```

Resultado: `jobs(limit=1)` con 101 entradas agota 100 páginas y lanza
`CoreSchoolError`; dos avisadores del mismo trabajo alcanzan dos envíos simulados.
No se abre ninguna BD, conexión SMTP ni conexión HTTP real.

### Watchlist SwissJob: recorte previo al filtro

```sh
node docs/audits/2026-09-18/reproduce_watchlist.mjs
```

Ejecuta el cuerpo real de `useMemo` con datos sintéticos: colegio en posición 501
invisible; ausencia de datos produce lista vacía. No sustituye una prueba de navegador.

## Pruebas ejecutadas, siempre en serie

Frontend: `cd frontend` y `npx --no-install vitest run` → **16 passed / 8 ficheros**.

Core sin BD: desde `/tmp`, con `PYTHONPATH=/home/lothar/Public/SwissJob`,
`CORE_ADMIN_DATABASE_URL` eliminado del entorno y el Python del venv de Portfolio:

```sh
env -u CORE_ADMIN_DATABASE_URL PYTHONPATH=/home/lothar/Public/SwissJob \
  /home/lothar/Public/ReactPortfolio/backend/venv/bin/python -m pytest \
  /home/lothar/Public/SwissJob/jobhunt_core/tests/test_migrate_guards.py \
  /home/lothar/Public/SwissJob/jobhunt_core/tests/test_school_cutover.py \
  /home/lothar/Public/SwissJob/jobhunt_core/tests/test_school_source.py \
  /home/lothar/Public/SwissJob/jobhunt_core/tests/test_schools_identity.py \
  /home/lothar/Public/SwissJob/jobhunt_core/tests/test_profiles.py \
  /home/lothar/Public/SwissJob/jobhunt_core/tests/test_normalize.py \
  -q -p no:cacheprovider
```

Resultado: **39 passed**. Una ejecución previa de 10 está incluida en esos 39;
no se suman. Advertencia existente de pytest-asyncio sobre el loop de fixtures.
Estas pruebas verdes coexisten con las cuatro reproducciones: no cubren los
contratos de integración descritos en la deuda.
