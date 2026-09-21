# Punto 4: operador de traspaso de las diez búsquedas

## Corte aplicado y verificado — 21-09, 15:56 UTC

**Autoridad core, diez búsquedas habilitadas; freeze retirado.** Release
a0fb403/core0050. Lectura del router BFF desplegado: 1 + 9 búsquedas,
todos los IDs y valores iguales al snapshot sellado, fuente sin modificar.
Ejecutor manual legacy: las diez llamadas rechazadas por `core_authority`,
con freeze desactivado y tripwire antes de cualquier consulta/envío local.

Captura real después del drenaje: 277 pendientes conservados, 92 ofertas
faltantes recuperadas mediante el sink; corpus de referencia 40.214 y
401.863 observaciones (10 × 40.214 − 277). Plan aplicado y replay=0 antes de
habilitar; configuración de diez ejecuciones y recuperación hacia delante.
Fuente/snapshot/plan/recibos permanecen privados en NAS,
`unification-e15-20260914/search-cutover.a0fb403/`.
Sello del snapshot `f275805d5ddd77392c200c5512d445b9bcea010c6de862c11cceb2680ac1c552`;
plan `d534781c3f0569f16b48376c0bf7c9efe3bd072093e0a432bcd0606f624f4e95`.

Beat real: barridos 15:45/15:50 UTC correctos (1,18/2,17 s), cero fallos y
cero búsquedas vencidas. NO se ha probado una entrega natural todavía:
primer vencimiento estimado 22-09 ~07:10 UTC, sujeto a edición del usuario.
No forzar avisos reales para validar. La prueba de inbox fue un sobre
inválido (422 sin insert); la recuperación con outbox fue en copia aislada.
Los workers/core capture están activos y las escrituras BFF habilitadas.
Una primera captura abortó por statement_timeout; el reintento conservando
las guardas pasó. No se amplió el timeout ni se ignoró el error.

**No usar revert preactivación ahora.** Ante problema: deshabilitar/drenar
ejecución core, conservar observaciones/outbox/contadores y reparar hacia
delante; no reactivar el escritor anterior ni pisar actividad posterior.
Próximo trabajo: productores nativos. No esperar al aviso natural para ello.

## Desplegado — 21-09 15:10 UTC; autoridad todavía local

Core API/worker/capture, BFF y worker público ejecutan **a0fb403**, sin
reinicios. Readiness: ready/core0050/a0fb403/authoritative=true. Celery pong.
Feed servido por CoreMatching: 20 por página, totales 1.800/1.799; entrega
de perfiles sin pendientes. Upgrade: diez búsquedas idénticas antes/después;
cero execution/observations y scopes nativos. **No hay corte de búsquedas:**
ejecución core apagada, routing local intacto. R5 legacy, Portfolio y :prod
no cambiados. Ninguna fuente retirada ni aviso de prueba enviado.

Ensayo de recuperación en copia: diez búsquedas reales, un pendiente controlado,
primera ejecución=1, replay tras desactivar/reactivar=0; contadores/outbox
conservados, revert pre-corte rechazado tras nueva actividad. Rollback exterior
restaura datos y core0049. 845,55 s. No certifica los 277 pendientes vivos:
falta captura fresca. La copia PostgreSQL pasó de 384 MiB a 1 GiB por esperas
de disco; CPU sigue a 0,5. Caducidad intacta: 20:00 UTC. No es benchmark de prod.

Imágenes core/BFF/worker: sha256 `53ea1ebe6687…`, `54a9239bcb42…`,
`13965d53de8d…`. Configuraciones, env y backups de esquema/búsquedas privados
en NAS `search-preactivation.a0fb403`. Workers anteriores: salida 0, sin OOM
ni purga. Inbox alcanzable y token comprobado con sobre inválido: 422 antes
de insertar. Próximo: freeze → captura fresca → seed/plan/apply → autoridad
única → canary servido. No repetir auditoría general ni suites de código intacto.

## Preparación anterior (histórico)

Estado: preparado y probado; **no es un acta de corte productivo**. Ningún
productor nativo activado ni fuente retirada en esta entrega.

## Evidencia reutilizable

- Core `725be1a`: suite completa **1.615 passed**, 889,33 s.
- BFF con captura posterior al drenaje y exportador privado: **2.508 passed,
  4 xfailed, 5 warnings**, 328,52 s.
- Operador core: **14 passed**; contrato estático entre repos/capas: **2 passed**.
- Diez búsquedas reales en copia NAS: apply=10, replay=0, revert=10,
  revert-replay=0; IDs, preferencias, contadores e historial conservados;
  291,31 s. Ese ensayo de almacenamiento NO verificaba marcadores pendientes.
- Sonda viva posterior: 277 candidatos pendientes; 185 resolvían una vacante
  presentable compatible y 92 no existían en core. Se recuperaron esas 92 en
  la copia con el sink normal: IrishJobs 55, NAV 19, Arbeitnow 16, Jobgether 1,
  WorkingNomads 1. Replay=0 y rollback verificado, 135,18 s. Sin envíos ni
  escrituras productivas. Es una sonda, NO el snapshot congelado del corte.
- Credencial real BFF: lecturas 200 para ambos perfiles; scopes read/write
  de saved_searches vigentes. Destino HTTP configurado en el WORKER core
  (no en su API): comprobado sin mostrar token ni datos privados.

## Camino restante, sin repetir suites sobre código intacto

1. Ensayo conjunto del operador en copia: snapshot → seed → plan → apply →
   retry → revert, y recuperación tras activación conservando observaciones y
   outbox. No enviar avisos de prueba a usuarios. Mantener separadas las
   evidencias de almacenamiento, pendientes y entrega; no declararlas probadas
   juntas antes de ejecutar ese recorrido.
2. Release identificada y core0050, con ejecución deshabilitada y routing local.
   Las imágenes `point4-725be1a` ya construidas NO incluyen el operador ni la
   captura de reloj posterior; no usarlas como si fueran esta entrega.
3. Freeze real y drenaje: rutas de escritura, tareas de búsquedas, productores
   legacy de corpus y captura/cosecha core. Inspección actual de workers; no
   reutilizar nombres de nodos antiguos ni purgar colas.
4. Capturar de nuevo en NAS, con Redis real. Conciliar el conjunto ACTUAL, no
   asumir que siguen siendo 277/92. Conservar mapa explícito de las diez IDs.
5. Aplicar y verificar, cambiar autoridad sin solapamiento, activar un único
   ejecutor y comprobar búsquedas/avisos servidos. Después transferir fuentes.

## Comandos del operador (dentro del entorno apropiado)

Los paths siguientes son ejemplos dentro del directorio NAS privado montado
como `/work`; no guardar el contenido privado en el ordenador. Credenciales
sólo mediante entorno de servicio, jamás en argv o documentos.

```sh
# BFF, con SAVED_SEARCH_WRITES_FROZEN=true y productores drenados:
python -m scripts.capture_search_handover --out /work/snapshot.json

# Core: SOURCE_DATABASE_URL, SOURCE_REDIS_URL, CORE_DATABASE_URL,
# SEARCH_FREEZE_URL apuntan a los mismos servicios verificados.
python -m jobhunt_core.search_cutover seed --snapshot /work/snapshot.json --report /work/seed.json
python -m jobhunt_core.search_cutover plan --snapshot /work/snapshot.json --targets /work/targets.json --plan /work/plan.json --report /work/planned.json
python -m jobhunt_core.search_cutover apply --snapshot /work/snapshot.json --plan /work/plan.json --report /work/applied.json
python -m jobhunt_core.search_cutover apply --snapshot /work/snapshot.json --plan /work/plan.json --report /work/replayed.json
# Sólo antes de activar; después exige recuperación hacia delante:
python -m jobhunt_core.search_cutover revert --snapshot /work/snapshot.json --plan /work/plan.json --report /work/reverted.json
```

El snapshot sella contenido y Redis; cada operación revalida fuente bajo locks
y comprueba freeze. `seed` confirma por fuente, guardando un recibo privado
PREPARADO antes de cada commit. Si falla, inspeccionar recibos/estado y reintentar
con otro path de informe: no sobreescribir evidencia ni borrar corpus compartido.
Una reversión de búsquedas no elimina ofertas legítimas recuperadas.

La copia NAS temporal `swissjob-f-rehearsal-20260919` y su directorio privado
caducan el **21-09 a las 20:00 UTC**. Hay que retirar exclusivamente esos
recursos temporales al acabar, sin tocar producción ni ampliar el plazo
silenciosamente.
