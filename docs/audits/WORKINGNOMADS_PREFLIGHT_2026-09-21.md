# Punto 4 — WorkingNomads ensayado, corte todavía no aplicado

**Checkpoint histórico, superado:** el corte se aplicó posteriormente el mismo
día en `3d5d67a`. Consultar el
[acta operativa](WORKINGNOMADS_CUTOVER_2026-09-21.md) para el estado vigente.

## Evidencia de fuente e histórico

- Una descarga pública desde NAS: 54 registros crudos, 45 conservados por el
  filtro propio del adaptador; respuesta completa, 245.802 bytes. SHA256
  `fc046b37955507bdf4c422684d35963b65861ec5b5afd32b61ca45b982b2854f`.
- Sobre copia aislada `core_copy`, con código desplegado `346fd36`, ventana
  de 7 días y filtro de títulos SwissJob: **39 admitidas, 35 reutilizaciones
  históricas**, 39 canónicas presentes, segunda pasada con los mismos ids y
  punteros, rollback íntegro. No se escribió en producción.
  Contadores de admisión: 16 aceptadas por fecha, 23 refrescos, 1 antigua
  desconocida, 0 sin fecha, 5 excluidas por título. Los contadores no son una
  estimación ni se escogió otro cuerpo después de medir.
- Producción R5: **99 activas = 99 proyectadas**; cero diferencias, CDC pendiente
  cero y cero presentables sin canónica.
- Público: **85 activas, 85 resueltas** a ganador core activo con canónica;
  8 mediante las cadenas de duplicados existentes en R5. Cero ciclos,
  ganadores ausentes o ambigüedades.
- Portfolio no registra WorkingNomads. Ambos workers SwissJob siguen activos;
  inspección de active/reserved/scheduled vacía, sin pausar consumidores.

## Configuraciones preparadas, NO instaladas

NAS privado `unification-e15-20260914/workingnomads-preactivation.346fd36/`:

- Público candidato SHA256
  `29e61d4383dd9684173587575197759ec9180e90bda8a09cfa31011aff865987`:
  añade sólo `workingnomads` al guard de backend/worker.
- R5 candidato SHA256
  `34c85f26884268bd53f28b1f6802412c9740b07637a01fe45024cefeb5d6a548`:
  conserva `jobicy` deshabilitado y añade `workingnomads` al worker.
- La instalación compara bytes con su configuración previa antes de sustituir.
  Los candidatos Remotive anteriores también siguen SIN instalar.

## Dependencia activa que debe resolverse ANTES del corte

El censo de la base pública encuentra **10 búsquedas guardadas activas**:
6 diarias y 4 semanales, todas con push. Todas pueden cubrir WorkingNomads:
9 usan `q`, 10 `remote_only` y una `canton`; ninguna acota fuentes.

`backend/tasks/search_tasks.py::_execute_single_search` consulta `public.jobs`.
Su docstring supone expresamente que la cosecha legacy continúa. Por eso
activar el productor nativo sin cambiar ese lector **perdería avisos de ofertas
nuevas**, aunque el feed core y el canary de lectura pasaran. No se apagó el
productor para descubrirlo después.

Orden de ejecución corregido: migrar el lector/autoridad de búsquedas y sus
avisos conservando filtros, identidad, historial y watermark; demostrar en
copia la recuperación de ofertas posteriores al corte; luego drenar los dos
workers, aplicar guards, drenar CDC y activar únicamente el scope probado.

No sustituir la búsqueda local por GET `/v1/vacancies` sin más: hoy su `q`
tiene semántica distinta al FTS local y no cubre `canton`/otros filtros. No
eliminar filtros, avanzar una marca de agua ante respuesta incompleta ni enviar
correos reales para probarlo. El número de notificaciones históricas y las
marcas de deduplicación deben conservarse.

## Estado seguro al guardar este checkpoint

Cero scopes nativos activados. Ningún productor retirado ni cola pausada.
Core `346fd36`, BFF `39579d7`, ambos workers `d89b6ee`; Portfolio sigue E.15.
Guarda selectiva Portfolio **sólo local**, commit `0f7ac3f`, pruebas documentadas
en [el censo Remotive](REMOTIVE_ALL_PRODUCERS_PREFLIGHT_2026-09-21.md).

La copia NAS caduca **21-09 a las 20:00 UTC**; no extender el plazo en silencio
ni confundirla con el PostgreSQL productivo. El helper y el cuerpo público del
ensayo permanecen en su directorio privado; no hay copia de datos SwissJob en PC.
