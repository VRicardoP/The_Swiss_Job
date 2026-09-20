# Punto 4 — checkpoint del worker público

Fecha: 20-09-2026, aproximadamente 02:07–02:11 Europe/Madrid.
Estado: **desplegado y comprobado en arranque; punto 4 NO cerrado**.

## Cambio exacto

Sólo `swissjob-worker`: imagen `swissjob-worker:point4-d89b6ee`, revisión
`d89b6ee09c875191cbda590b12bd458efe517380`, ID
`sha256:2859bfc2e958115e60259ac185b3d2edfbad46fe40722c6dc96f072c06cf0b74`.
Construida en el NAS desde el backend del commit limpio, sobre la base exacta
del worker anterior (`sha256:abf64f992b589f18b79be8f6f23f95757a7661ce6430c490db79bf041cfa8cd2`).
Sin cambios de dependencias ni migraciones BFF respecto a `4e40ffe`.

Incluye el orden de cosecha por intento más antiguo, con empates estables en el
orden original, y el fencing de resultados lentos de CV. Las listas de retirada
siguen vacías y `CORE_FEEDBACK_ENABLED=false`: no activa ningún nuevo escritor.
API pública, core, Portfolio, worker R5 y trigger R5 conservan sus imágenes.
Core sigue en core0047. No implica despliegue de core0048 ni de los adaptadores
nativos desarrollados localmente.

## Evidencia de pruebas

- Core `cf1be3b`: **1.482 passed**, 890,75 s, un aviso preexistente.
- Cuatro regresiones de equidad: rojas antes del fix; 69 dirigidas verdes después.
- La primera suite completa detectó tres regresiones por desempate alfabético de
  fuentes nunca intentadas. Se corrigió el código para preservar el registro en
  los empates; no se debilitaron los tres tests anteriores.
- 95 pruebas dirigidas verdes tras esa corrección.
- BFF `d89b6ee`: **2.444 passed, 3 skipped, 4 xfailed, 5 warnings**, 291,75 s.
  Suites siempre en serie. Log local `/tmp/point4-d89b6ee-bff-final.log`, SHA256
  `2107c17c7c420dc600de9915f3a32017366ccc0fdbc56bca18037b5d6bc43dc8`.
  Los avisos de mocks/corrutinas y deprecación no son errores productivos demostrados.

## Despliegue y comprobaciones

1. Configuración candidata privada validada por Compose, comparada estructuralmente:
   cambia exclusivamente `services.worker.image`. Copia previa privada conservada.
2. Inspección Celery: active/reserved/scheduled vacíos. Parada cálida sin plazo de
   SIGKILL (`docker stop -t -1`); salida 0, OOM=false. No se purgaron colas.
3. Recreación únicamente de `worker` con `--no-deps`. No se usó `--remove-orphans`:
   frontend, PostgreSQL y Redis pertenecen al mismo proyecto pero a otros ficheros.
4. Celery responde pong; worker running, cero reinicios. API pública devuelve
   HTTP 200 / healthy. Los cinco archivos críticos coinciden byte a byte con el
   contexto de build limpio.
5. Entorno idéntico antes/después (comparación por digest sin imprimir valores),
   mismo comando y colas default/scraping/ai, concurrencia 2, usuario app,
   restart unless-stopped, red y volumen de caché. Entrypoint vacío: sin migración
   implícita al arrancar el worker.
6. Configuración corriente actualizada atómicamente, modo privado conservado;
   validación final `config -q` correcta. El backup anterior sigue en el NAS.

La primera sonda auxiliar aislada olvidó sustituir el entrypoint de la imagen:
intentó resolver una BD sin red y falló antes de conectar. No se usó como evidencia
positiva ni tocó datos. Repetida con `--entrypoint python --network none`, correcta.

Consulta de sólo lectura del worker desplegado a 00:09:58 UTC: orden calculado
myscience, financejobs, irishjobs, ocho scrapers escolares, schuljobs, gastrojob,
stelle_admin, tes. No descargó fuentes ni despachó tareas. La próxima cosecha debe
acreditar progreso real; no se declara que todos los portales ya estén recuperados.

## Reversión de este checkpoint

En `/share/CACHEDEV1_DATA/Public/unification-e15-20260914` se conservan
`swissjob.point4-d89b6ee.before.yml` y `swissjob.point4-d89b6ee.yml`, privados.
Revertir requiere parada cálida y `up -d --no-deps worker` con el fichero anterior,
seguido de restaurar la configuración corriente y verificar Celery/API. No exige
downgrade ni borrar datos: no se activaron autoridades o esquemas nuevos.
No confundir esta reversión de imagen con el rollback POST-corte de feedback,
que sigue pendiente.

## Trabajo que sigue condicionando la retirada

Entrega duradera del perfil público (incluidos cambios y vaciado del CV), retorno
del estado generado tras el corte de feedback, extracción escolar sin corpus
local, paridad/enlace histórico por fuente, scheduler nativo sin doble cosecha,
búsquedas y avisos. El despliegue de este checkpoint no cierra esas dependencias.
No se inicia punto 5 ni se certifica una calidad de matching no examinada.
