# Punto 4: operador de traspaso de las diez búsquedas

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
