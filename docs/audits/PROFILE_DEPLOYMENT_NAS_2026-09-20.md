# Punto 4 — despliegue de entrega de perfiles (en curso)

20-09-2026. **No dar por activada la entrega ni cerrado el punto 4**.
Código probado: BFF `a93dcbc`, core `bac5e78`; ensayo completo e imágenes en
[PROFILE_DELIVERY_NAS_2026-09-20.md](PROFILE_DELIVERY_NAS_2026-09-20.md).

## Checkpoint confirmado

- Fuente productiva `swissjobhunter`: migración `b46e1230a901 → c57f2341b012`
  aplicada con la imagen candidata y la configuración real, tras backup de la
  fuente. Alembic terminó con salida 0. Consulta posterior: dos snapshots,
  ambos versión 1, contenido presente y delivered_version=0.
- Entrega apagada: todavía funcionan las imágenes previas (BFF E.15, core E.15
  / core0047, worker público d89b6ee). No se transfirió autoridad de ningún
  perfil. El nuevo trigger sólo conserva cambios futuros de los escritores.
- No se activó feedback core ni se retiró fuente de ofertas/colegios alguna.

## Preparación privada, aún no aplicada a contenedores

Directorio NAS `unification-e15-20260914/profile-preactivation.1jTCVK`, privado:
`core.before.yml`, `swissjob.before.yml`, candidatas y entornos de migración.
No versionar/copiar esos ficheros al ordenador: contienen credenciales.

Las candidatas pasan Compose `config -q`. Cambian las tres imágenes core y
la imagen del BFF; el worker público queda en d89b6ee. La candidata del BFF
explicita `CORE_PROFILE_SYNC_ENABLED=false`. Se retiró únicamente el override
runtime de `RELEASE_SHA` antiguo de los servicios core: ahora la identidad
procede del marcador y del ENV horneados de la nueva imagen. La primera guarda
de preparación detectó ese override y abortó antes de escribir candidatas.

Configuraciones corrientes intactas, iguales byte a byte a las copias previas:

- Core: `15949e404549d16cb47abd1ff6c396094fb2acefae7ce57b24f9dd3b66ceaf5f`.
- SwissJob: `193f291536fea2a5492f4ae562d93f10b1ca704f87e7475875dc73d7eb622025`.

## Backup y siguiente condición

`source.dump`: backup completo de la base fuente, generado dentro del NAS;
291.131.577 bytes y catálogo legible con `pg_restore -l`. No confundir esa
comprobación de formato con un nuevo ensayo de restore: el ensayo estricto y
funcional se hizo sobre las copias privadas previas.
`core-schema.dump`: generación todavía en curso. No usar ni dar por completo
hasta salida 0 y verificación. Sólo incluye el esquema jobhunt; su restauración
aislada requiere preparar extensión vector/pg_trgm y roles según el runbook.

Pendiente: terminar y comprobar ese backup; drenar procesos core, migrar a
core0049 y sustituir **todos** sus escritores antes de activar entregas;
actualizar BFF con flag OFF, verificar salud y configuración; habilitar,
drenar y contrastar contenido/versión de los dos perfiles sin exponer sus datos.
No revertir a un binario sin fencing tras transferir autoridad. El transporte
HTTP, la recuperación hacia delante y la reconstrucción del feed ya se ensayaron
en aislamiento; falta confirmar el canary vivo.
