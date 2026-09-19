# Punto 4 — traspaso de productores y retirada legacy

Estado 20-09-2026: **preparación, no ejecutado**. Este documento no autoriza un
flip que incumpla las precondiciones. Producción sigue E.15/core0047. El objetivo
vigente del propietario es sólo cerrar este punto, no un nuevo examen de calidad.

## Condiciones antes de activar una fuente

1. Identificar TODOS sus productores (SwissJob público, R5, Portfolio y tareas
   manuales), su configuración, cola y horario efectivo. Registro no equivale
   a ejecución. Una fuente sin credencial o bloqueada no cuenta como saludable.
2. Verificar campos canónicos sobre los MISMOS datos públicos crudos, identidad
   y ventana de admisión. Conservar raw, errores parciales, fechas originales y
   enlaces de aplicación. Para fuentes WINDOW declarar `admission_window_days=7`;
   no inventar fecha a partir del momento de descarga.
3. Ensayar el enlace del histórico y la compatibilidad MD5→UUID. CH Media requiere
   reconciliación específica: el `externalId` ATS y la URL de aplicación se
   comparten entre posiciones. El nativo usa `id:<id de portal>` y `/stelle/<id>`.
   No escoger un histórico ambiguo por orden ni fusionar plazas por URL compartida.
4. Demostrar que las ofertas exclusivamente nativas se pueden consultar, guardar,
   rechazar y usar en candidaturas/documentos. El nuevo feedback no está activado;
   su ensayo de ida/vuelta actual es PRE-activación, no conserva por sí solo
   nuevas decisiones del usuario tras el corte. Resolver esa reversión antes.
5. Tener backup y restore estricto, esquema equivalente y ensayo con datos reales
   aislados. No exportar datos privados a un lugar sin autorización específica.
   El ensayo SwissJob realizado dentro del NAS no autoriza su copia al ordenador.

## Control por fuente (código local, pendiente de despliegue)

El BFF/worker admite listas JSON de nombres exactos:

```text
LEGACY_DISABLED_PROVIDERS=["thehub"]
LEGACY_DISABLED_SCRAPERS=["swiss_schools_iscs"]
```

Son ejemplos de sintaxis, NO una orden de activar esos cortes. Ambas listas
vacías conservan el comportamiento anterior. Nombres desconocidos impiden la
construcción del productor: nunca se ignora un typo. El catálogo de fuentes
conserva los nombres; el estado de providers indica `disabled (core handover)`.
El monitor legacy de colegios deja de reclamar actividad a scrapers transferidos;
comprobar en su lugar la salud efectiva del sustituto core, no silenciarla sin más.

Las listas son de ARRANQUE. No cancelan procesos que ya tenían una instancia ni
peticiones HTTP en vuelo. Secuencia obligatoria por fuente:

1. Mantener el scope nativo deshabilitado.
2. Evitar nuevos despachos legacy y drenar active/reserved/scheduled de las colas
   afectadas. No purgar la cola indiscriminadamente ni matar la tarea a mitad de
   persistencia. Conservar CV, avisos y otras tareas todavía necesarias.
3. Recrear cada productor correspondiente con la lista de retirada. Verificar
   configuración efectiva y que tanto el acceso individual como el barrido
   del registry no construyen la fuente. Comprobar que no quedó un worker viejo.
4. Drenar CDC de las últimas escrituras confirmadas y verificar contadores/errores
   antes de registrar el punto de corte. No borrar ni avanzar a mano un slot.
5. Activar únicamente el scope nativo validado. Ejecutar un lote; verificar
   estados del runner, raw/revisión/canónica, frescura, feed servido y acciones.
   Repetirlo para demostrar idempotencia. `partial`/429/403 no equivale a completo.
6. Registrar imagen, configuración (sin secretos), identidades, conteos y resultado.

## Reversión

Primero deshabilitar y drenar el escritor nativo; nunca reactivar legacy mientras
el nuevo pueda seguir escribiendo. No borrar el corpus compartido ni los nuevos
estados de usuario. La reanudación legacy exige restituir su estado/cursor con
procedencia verificada y preservar los datos posteriores al corte. Si esa
reconciliación no existe, mantener el corte sin ejecutar: el `revert` previo del
feedback no es un rollback POST-activación.

## Dependencias para retirar procesos completos

- Perfiles: el BFF público escribe `swissjobhunter`, pero la captura R5 lee otra
  base. Igualdad puntual de perfiles NO acredita sincronización de una edición.
  Resolver entrega duradera y autoridad antes de apagar CDC.
- CV: autocompletado y extracción siguen siendo necesarios; no retirar su worker
  por asociación con cosecha. Los resultados lentos deben revalidar sus entradas.
- Búsquedas, avisos y digest: conservar historial y demostrar quién los ejecuta
  después. La cadena diaria aún contiene embedding/dedup legacy aunque omita
  matching; listas vacías de cosecha no significan que el motor se haya retirado.
- Colegios: configuración/estado ya están en core, pero extracción y publicación
  de observaciones siguen pasando por productores de ambos BFF. No llamar a eso
  retirada completa mientras dependan de corpus o cursores locales.
- Conservar el histórico en sólo lectura y el plazo de recuperación ratificado;
  no eliminar tablas/volúmenes porque una suite sea verde.

## Evidencia disponible y límites

Core `65150ec`: **1.462 pruebas verdes**, incluidas NAV/TheHub. BFF `b19c865`:
**2.440 passed, 3 skipped, 4 xfailed**, tras 147 dirigidas. Suites en serie.
CH Media, PublicJobs, JSON y RSS tienen
evidencias locales en `docs/audits/NATIVE_*`. No equivalen al canary NAS.
TheHub: 42/42, tres páginas, cero diferencias en dos verificaciones públicas.
NAV: paridad de las 978 recibidas, híbrido incompleto y 429 confirmado; la
segunda descarga falló al inicio. Jobgether: 403 observado. No se eluden bloqueos
ni se declara cobertura completa. El cierre exige resolver o decidir
explícitamente el tratamiento operativo de las fuentes inaccesibles.
