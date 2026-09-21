# Punto 4 — censo completo de Remotive antes del corte

**Sólo lectura y preparación; ningún productor desactivado.**

## Identidades y CDC

- R5: 35 ofertas activas no duplicadas; 35 encarnaciones activas en
  `legacy:remotive`, cero identidades faltantes/adicionales, cero pendientes CDC,
  ninguna vacante presentable sin canónica. Scopes nativos habilitados: cero.
- Base pública: 10 activas. Nueve coinciden directamente con R5; la décima existe
  en R5 como inactiva/duplicada. La comparación por hash parecía perder una;
  por URL exacta también quedaba una sin resolver y seis resolvían a clones.
  **No se insertó nada a partir de esa observación incompleta.**
- Se siguieron las cadenas `duplicate_of` existentes en R5 con detección de ciclo
  y límite de profundidad, bajo lectura repetible. Resultado: **10/10** referencias
  lógicas resueltas a una vacante core activa, no fusionada y con canónica; una
  mediante duplicado. Cero ciclos, ausentes o ambigüedades en esa resolución.
  No es una nueva decisión de dedup ni una afirmación de equivalencia por URL.
- Identidades conservadas sólo en el NAS privado bajo
  `unification-e15-20260914/remotive-preactivation.346fd36/`.
  No se exportaron datos personales, contraseñas ni hashes de autenticación.

## Tercer productor: Portfolio

Configuración ejecutada: `background_schedulers_enabled=true`, cosecha diaria
habilitada y digest habilitado. Remotive consulta **14 categorías**, límite 200
por petición y caché máxima 200. Por tanto el corte de los dos workers SwissJob
**no basta**: Portfolio seguiría descargando por arranque/cron y peticiones.

La política global de títulos de SwissJob no existe en ese recolector. No aplicar
`legacy_title_filter=true` a una sustitución del conjunto Portfolio sin verificar
su cobertura. Tampoco apagar todos los schedulers: incluye avisos y tareas que
todavía deben conservarse. El digest actual lee cachés locales, una dependencia
que debe resolverse antes de vaciarlas o dejarlas caducar.

### Corrección preparada en Portfolio

Commit privado local `0f7ac3f` (sin push):

- `LEGACY_DISABLED_SOURCES` con nombres exactos validados; default `[]`.
- Guarda previa a sesión HTTP y mutación de caché en el funnel común.
- Bypasses Jobicy/JSearch también sujetos a la misma regla.
- Lecturas de caché y otras fuentes conservadas; estado efectivo en
  `/health/deep` de administrador. Requiere drenaje/reinicio: no cancela en vuelo.
- Once regresiones rojas antes del fix; después **38 dirigidas verdes**.
  Suite completa: **2.011 passed, 1 skipped**, 198,14 s. Después se añadió una
  comprobación de health (incluida en las 38 dirigidas); no se atribuye a esa
  corrida completa. Formato black y `git diff --check` limpios.

**No desplegado ni activado todavía.** El commit previo ajeno `26b752a` cambia un
default de modelo CV; comprobar la configuración efectiva y preservar el
comportamiento al preparar cualquier imagen nueva. No desplegarlo por accidente.

## Continuación segura

1. Resolver cobertura por categorías/admisión y lectores locales de Portfolio;
   no dar por equivalente el adaptador SwissJob por compartir URL del portal.
2. Desplegar la guarda validada, drenar todos los productores de esta fuente,
   instalar las listas de retirada y repetir la comparación CDC final.
3. Sólo entonces activar el scope y verificar persistencia, idempotencia,
   frescura y lecturas/acciones servidas. Conservar recuperación por fuente.

Mientras tanto se pueden preparar fuentes exclusivas de SwissJob, sin esta
dependencia del cache/digest Portfolio. WorkingNomads tiene paridad del adaptador
44/44 documentada, pero aún requiere ensayo en copia y canary: no se declara
transferida por esta recomendación.
