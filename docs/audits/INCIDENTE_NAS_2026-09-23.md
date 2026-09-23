# Incidente — el NAS entero se quedó sin contenedores (2026-09-23)

**Alcance:** los **42 contenedores** del NAS parados durante ~30 minutos, entre
ellos toda la producción de SwissJob (core, BFF, worker, postgres, frontend),
el Portfolio y servicios ajenos al proyecto. **No lo causó ningún despliegue.**

## Qué pasó, con las horas

| Hora (UTC) | Hecho |
|---|---|
| 16:02:38–44 | **Todos** los contenedores reciben SIGTERM y, los que no salen, SIGKILL a los ~5 s. Los 42, en seis segundos: `swissjob-*`, `portfolio_*`, `novafeed_*`, `kavita`, `beszel`, `seshat`, `tinymediamanager` |
| 16:02–16:16 | Nadie los vuelve a levantar. `restart: unless-stopped` en todos, y aun así siguen parados |
| ~16:16 | Despliego `portfolio-backend:board-483fad0`. El contenedor entra en bucle: `alembic … could not translate host name "db"` — la base no existía porque **su contenedor estaba parado** |
| 16:16–16:20 | Restauro la imagen anterior. Sigue sin arrancar: el diagnóstico apuntaba a mi imagen y era falso |
| ~16:20 | `docker info` → `running=0, stopped=42`. `ps` → **`dockerd` con 15 min de vida**. El daemon se reinició a las 16:02 |
| 16:20–16:30 | Arranque por capas: bases → cachés → core → aplicaciones → resto |
| 16:30 | 22 contenedores en pie; `core-api` `ready`/`17e2b9e`/`authoritative: true`; BFF 200 |

## Causa

El **daemon de Docker se reinició** a las 16:02 UTC (`dockerd` con 15 minutos
de vida cuando el resto del sistema llevaba 4 días 6 h). Al pararse el daemon,
todos los contenedores reciben SIGTERM; al volver, **no se relanzaron**.

Lo que **queda descartado** con evidencia:

- **No fue OOM**: `OOMKilled=false` en los 42; `MemAvailable` 2,3 GB libres.
- **No fue un reinicio del NAS**: `uptime` 4 días 6:47 sin cortes.
- **No fue el despliegue**: `compose -p portfolio stop backend` no puede parar
  `kavita` ni `beszel`, y los `exit=0` ordenados son un apagado del daemon.
- **No fue disco lleno** en el volumen de datos: 441 GB libres (76 %).

Lo que **no está atribuido**: por qué se reinició el daemon. Sospecha anotada,
no confirmada: el volumen de sistema de QTS (`/`) está al **84 %, con 64 MB
libres**. Container Station vive ahí. No hay prueba de causalidad; hace falta
mirar el registro del sistema de QNAP, que no es accesible por SSH con este
usuario.

## Dos lecciones, una de ellas mía

**1. El diagnóstico apuntó al último cambio, y el último cambio era inocente.**
Vi un contenedor en bucle justo después de desplegar y asumí que la causa era
mi imagen. Revertí —correcto con lo que sabía— y el servicio **siguió sin
arrancar**, que es lo que delató el error. La pregunta que lo habría resuelto
en un minuto, y que ahora va la primera del runbook: **¿está en pie lo que ese
contenedor necesita?** `docker ps` completo antes de `docker logs`.

**2. `restart: unless-stopped` no es alta disponibilidad.** Está en los 42
contenedores y no levantó ninguno. Si el daemon se reinicia y los deja
parados, la única red que queda es que alguien mire. Es el hallazgo H10 de la
auditoría del 23-09 —sin supervisor que actúe sobre `unhealthy` ni sobre
`stopped`— visto en producción y no en un documento.

## Comprobaciones tras restaurar

| Qué | Resultado |
|---|---|
| Contenedores | **22 en pie** (los que corrían antes) |
| `core-api` | `ready`, `core0051`, `release 17e2b9e`, `authoritative: true` |
| Cosecha | **15/17 scopes con 0 fallos**; 2 de `nav_arbeidsplassen` con `consecutive_failures=1` por el corte — por debajo del umbral, la siguiente ventana los limpia |
| CDC | **0 pendientes** |
| Slots | `jobhunt_shadow_r5_rehearsal` activo; el huérfano `jobhunt_shadow` sigue inactivo (A19-01) |
| BFF SwissJob y Portfolio | 200 en su ruta de salud |

**Pérdida de datos: ninguna observada.** Lo que sí se perdió es **~30 minutos
de servicio** y, con ellos, la ventana de cosecha que tocara en ese tramo.

## Qué hacer con esto

1. **Mirar el registro del sistema de QNAP** de las 16:02 UTC del 23-09 (desde
   la interfaz, no por SSH) para atribuir el reinicio del daemon.
2. **El volumen de sistema al 84 % con 64 MB libres** merece atención por sí
   solo, con independencia de si causó esto.
3. **Supervisión**: hoy nada avisa de que 42 contenedores estén parados. Es el
   paquete T13 de la auditoría (healthchecks de workers + supervisor que actúe
   sobre `unhealthy`), al que este incidente añade un caso que no estaba
   contemplado: **parados**, no enfermos.
4. **Orden de arranque**, por si se repite: bases (`swissjob-postgres`,
   `portfolio_db`, `novafeed_db`) → cachés (`*redis*`) → core
   (`swissjob-core-api-r5`, `-worker-r5`, `-capture-r5`) → aplicaciones
   (`swissjob-backend`, `-worker`, `-frontend`, `-erasure-cdc`,
   `portfolio_backend`, `portfolio_ngrok`, `novafeed_api`) → el resto.
   Con `docker start <nombre>`, que preserva la configuración exacta; nunca un
   `compose up` global.
