# PostgreSQL: aislamiento del proceso padre — 20-09-2026

## Incidente observado (UTC)

Antes de activar feedback o sustituir las imágenes del core/BFF, PostgreSQL
registró a las 03:45:25.672 la salida del proceso 21968 por señal 13 (Broken
pipe), terminó sus otros procesos y entró en recuperación. Volvió a admitir
conexiones a las 03:48:11.656. El contenedor no se reinició y no consta OOM.
La sincronización recorrió, entre otros, ficheros de `pg_logical/snapshots`.
No se avanzó ningún slot ni se restauraron/borraron datos para recuperarlo.

La identidad exacta del proceso 21968 no quedó registrada: **no se atribuye
como hecho probado el origen de esa señal**. Se identificó y reprodujo una
vulnerabilidad operacional compatible con el patrón observado.

## Reproducción sin datos de usuario

Dos contenedores vacíos del NAS, misma imagen PostgreSQL de producción
(`9da94faaaa3a5193705fab822a480cd328e116213b66581196fdb2b864c2904d`),
sin red ni puertos, PGDATA en tmpfs, usuario/base `probe`, límites 192 MiB y
0,5 CPU. Una shell hija huérfana termina con código 2 (no se mata PostgreSQL):

- Sin `--init`: a las 03:56:28 PostgreSQL, PID 1, interpreta esa salida como
  la de un hijo suyo y provoca recuperación de toda la instancia.
- Con `--init`: el proceso init recoge la salida ajena; PostgreSQL sigue
  disponible y no registra recuperación.

Ambos contenedores de prueba se retiraron; sólo se descartaron bases sintéticas
vacías. El cuerpo está conservado en `scripts/probe_postgres_orphan_isolation.sh`,
con guardas de nombre y usuario/base; **no ejecutar en bases reales ni copias**.

Fundamento: [documentación de Docker sobre init](https://docs.docker.com/reference/cli/docker/container/run/)
y [reproducción publicada del mecanismo en PostgreSQL](https://www.cybertec-postgresql.com/en/docker-sudden-death-for-postgresql/).

## Corrección y límites

`init: true` y healthcheck exec (`CMD`, no shell). No cambia versión de PG,
esquema, autenticación, retención, volumen ni políticas de matching. La
configuración NAS se compara contra el contenedor real y se guarda privadamente
antes del cambio. Se detectaron tres variables core ajenas a PostgreSQL añadidas
al compose común desde su creación: no se introducen durante este arreglo.
La validación del compose renderizado exige que ningún otro servicio cambie.

La protección evita esta clase de fallos por procesos huérfanos; no demuestra
ausencia de otros motivos de recuperación y no sustituye backups ni supervisión.
El punto 4 sigue abierto; este documento no certifica retirada de productores.

## Verificación de despliegue

Aplicada en el compose NAS original; recreación sólo de `postgres`, sin build,
pull ni retirada de volúmenes. Apagado limpio 04:14:32 UTC; disponible
04:14:56.418. PostgreSQL es ahora PID 7 bajo init. Verificación automática:
misma imagen, variables efectivas, montajes, alias de red, reinicio y puertos;
`Init=true`, healthy. Los tres marcadores Docker sin `=` son variables
explícitamente no definidas, no valores nuevos. Core permanece en core0049.

Captura CDC reconectada y slot activo a las 04:15:01; core API y BFF saludables.
Original, candidata, configuración renderizada e inspecciones antes/después
quedan en el directorio privado `postgres-init.TMUqw3` del NAS. No se copian
esas evidencias con secretos a git ni al ordenador.
