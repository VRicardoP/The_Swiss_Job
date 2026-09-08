# E.8 — documentos SwissJob y fin de paginación de fuentes

## Alcance real

Implementación y comprobaciones **locales**. No se ha cambiado el routing vivo,
desplegado core0043 ni retirado ningún productor del NAS en esta sesión.
No es el cierre de los cinco trabajos ni una certificación de calidad.

| Trabajo solicitado | Resultado de esta sesión | Lo que falta para cerrarlo |
| --- | --- | --- |
| Documentos compartidos | SwissJob: conservación, journal, autoridad fresca, API de recuperación, biblioteca/PDF | Inbox SwissJob; migrador histórico; erase/export integral; ensayo con escrituras posteriores; despliegue y flip |
| Colegios | Autoridades actuales conservadas, sin apagar fuentes | Maestra core, migración de configuración/contactos/ofertas/alertas/candidaturas y canary |
| Productores y retirada legacy | Corrección local del EOF WordPress en Portfolio | Resolver 404, portar/activar sustitutos y comprobar cobertura antes de retirar cada productor |
| Preservación y recuperación | Snapshot de documento, borrado del journal por propietario, downgrade que rechaza pérdidas | Coordinación de erase core/BFF/entregas, exportación completa, backups y rollback después del corte |
| Rendimiento y fiabilidad | Biblioteca acotada a 20, inferencia/HTTP sin transacción; PDF real sintético y prueba móvil | Carga representativa en NAS y presupuesto de escrituras/LLM/PDF con las autoridades finales |

La publicación del frontend Portfolio **ya no está bloqueada**. El usuario la
autorizó; `3f6d7bea5f41cf207c17a7cec1ce356ab6cb8c22` está en GitHub/main. La API
de checks confirma `test=success` y `Cloudflare Pages=success` el 09-09. Esto no
equivale a desplegar los cambios SwissJob descritos aquí.

## Documentos SwissJob

- `d5e9f3071b28`, hija de c4d8e2f60a17: elimina únicamente la FK de documento a
  oferta y materializa título/empresa originales. Conserva hash de referencia,
  UUID, contenido, idioma y fecha. La FK al usuario sigue con CASCADE.
- Downgrade toma locks antes de comprobar y rechaza documentos huérfanos o cuyo
  snapshot difiera del catálogo. No descarta contenido para hacer pasar el rollback.
- `e6fa04182c39`: journal de output preparado con UUID estable, propietario,
  vínculo core, hashes y recibo. ACK confirmado elimina la copia local de texto;
  conserva la identidad de operación. No purgar tombstones automáticamente.
- Reintento dentro de 23 horas desde el primer intento, inferior al recibo core
  de 24 horas. Fuera de ese intervalo: reconciliación explícita, nunca nueva key
  que pueda resucitar un documento ya borrado.
- Dos preparaciones simultáneas de la misma intención convergen en el primer
  cuerpo confirmado. Pueden consumir dos inferencias si ambas arrancan antes de
  preparar el journal; no se promete evitar ese coste, sí evitar dos entregas.
- Lectura fresca de routing: local/shadow/core_read siguen locales;
  core_primary/rollback_pending son exclusivamente core. No activar estos últimos
  antes de migrar. No hay fallback de una escritura core ambigua a local.
- Lock de routing antes del de usuario/persistencia; owner antes de mapping/journal.
  Ni generación ni HTTP mantienen la transacción abierta. Freeze y drenaje siguen
  siendo obligatorios para cambiar autoridad, vinculación o ejecutar rollback.
- `DOCUMENT_WRITES_FROZEN=true` bloquea mutaciones antes de auth y vuelve a
  comprobarse después de inferencia. `/health/documents` expone `writes`;
  `/health` y `/api/v1/health` conservan su respuesta anterior.
- POST generate core exige `operation_id`; operación preparada retorna 202 con
  estado/ID. GET `/documents/operations/{id}`, POST `.../{id}/retry` y GET
  `/documents/item/{id}` permiten recuperar sin oferta/CV/proveedor original.
- Biblioteca GET `/documents`: keyset de 20 documentos por página; funciona
  aunque la oferta desaparezca. Frontend `/documents`, con PDF existente reutilizado,
  caché por propietario y operación conservada en sessionStorage durante recargas.
  No se almacena el CV ni credenciales en ese estado de recuperación.

### Condición que NO debe confundirse con cierre GDPR

El borrado del usuario local elimina su journal local, pero no constituye una
confirmación síncrona del borrado remoto o de backups. La exportación actual del
perfil tampoco inventaría todos los documentos/entregas. Hay que cerrar ambos
contratos antes del flip documental; la prueba de un ACK tardío solo demuestra
que no se reconstruye un journal local cuyo propietario ya fue eliminado.

## Pruebas y mordidas

- Cinco regresiones de conservación/freeze/transacción: rojas en el código previo
  por las aserciones esperadas; no por import o dependencia ausente.
- 82 pruebas documentales en verde antes de las últimas pruebas adicionales de
  concurrencia y sonda. Upgrade/backfill/downgrade real, con schemas de prueba
  aislados; paridad Alembic/ORM de columnas, índices, checks y FK verificada.
- Primera suite completa: **2337 passed, 4 xfailed, 1 failed**. El fallo fue
  añadir un campo a health. Se corrigió la implementación conservando el test:
  sonda documental separada, no relajación de la aserción.
- Frontend: **10 passed**, build y lint de los archivos afectados correctos.
- Navegador visible con frontera API simulada: retry tras recarga con mismo UUID,
  biblioteca, PDF, móvil 390×844 y borrado; cero errores de consola. No representa
  un ensayo de red/core/NAS vivos. El primer recorrido detectó el parseo JSON de
  HTTP 204 vacío; el fix tiene test y la segunda ejecución pasó completa.
- PDF sintético real de una página A4, 24.898 bytes, sin JavaScript:
  SHA-256 `75a25e51cf14534de78b35ba0bc74fcc999b2373c8caee0b91b66a66a49ec547`.
  Es una prueba de generación/descarga, no certificación visual de cualquier CV.

## Fuentes WordPress (Portfolio)

SwissTechJobs e ICTjobs contaban `rest_post_invalid_page_number` como fallo;
cinco EOF abrían el circuito. Reproducción: seis llamadas de página terminal
producían solo cinco peticiones, circuito abierto. Ambos casos rojos antes del fix.

El helper de red permite al adaptador clasificar un vacío esperado; la regla WP
solo acepta HTTP 400 + JSON objeto + código exacto + data.status=400, y se instala
únicamente para página >1. 400 desconocido/malformado, 403 y error en página 1
siguen contando como fallo. **97 pruebas dirigidas verdes** tras el arreglo.
No cambia endpoints, credenciales, ventanas de cosecha ni políticas de reintento
del resto de proveedores. Aún no desplegado en NAS.

## Secuencia siguiente y criterios de aceptación

1. Terminar suites completas en serie y guardar su resultado exacto.
2. Inbox de documentos, export/erase y reconciliación del journal; probar HTTP
   real con core y PG, además del doble de red de las pruebas locales.
3. Migrador explícito que preserve UUID/fechas/idioma/contexto/hash y distinga
   insertados de preexistentes. Ida/vuelta sobre copia fiel, incluyendo escrituras
   posteriores y pendientes. No restaurar globalmente el corpus compartido.
4. Release limpia core0043+BFF, scopes, freeze/drenaje, canary por perfil y flip
   documental. Mantener el escritor anterior recuperable y apagado, no concurrente.
5. Colegios por contrato completo y fuentes una a una; ningún scope directo core
   estaba habilitado en el último inventario NAS. CDC no sustituye al fetcher.
6. Ensayar carga representativa y recuperación, entonces cerrar F. Calidad exige
   holdout nuevo independiente; no usar 0.5856/0.7438 ni fabricar siete días.

YAGNI: mismo almacén/cliente/renderer PDF; sin cola, framework ni dependencia nueva.
Las correcciones pequeñas no autorizan omitir los ensayos operativos pendientes.

## Verificación final del checkpoint

- SwissJob BFF completo: **2341 passed, 4 xfailed, 5 warnings**, 277,70 s.
  La regresión de health está corregida. Warnings observados: deprecación 422 y
  corrutinas de fixtures de cosecha legacy no esperadas; no se ocultan como cero warnings.
- Tras añadir el listado global de operaciones pendientes: **8 pruebas dirigidas
  pasadas**, 3,77 s (workflow, concurrencia, biblioteca y health).
- Contrato HTTP/auth/core/PG real de ambos adaptadores: **4 passed**, 13,04 s.
  Incluye nueva lectura por ID y biblioteca SwissJob 20+1 con cursor real.
  Warning exclusivo de caché pytest no escribible en el contenedor de pruebas.
- Portfolio backend completo: **1965 passed, 1 skipped**, 195,56 s.
  Fix de fuentes guardado en **cefb70a**, solo local, sin push/despliegue.
- Frontend SwissJob final: **10 passed**, build y lint afectados correctos.
  PDF sigue siendo carga diferida (~976 kB sin comprimir); no aumenta el bundle
  inicial con el motor PDF ni se añade otra dependencia.
- Pendientes recuperables por usuario: GET /documents/operations muestra hasta
  20 con total, sin texto de CV; la biblioteca permite reintentar desde ese
  inventario incluso sin sessionStorage. Un vencimiento exige reconciliación.

Comandos principales (nunca suites simultáneas):

```sh
docker compose exec -T backend python -m pytest tests/ -q --tb=short --show-capture=no
# En ReactPortfolio/backend:
venv/bin/python -m pytest tests/ -q --tb=short --show-capture=no
# En SwissJob/frontend:
npx vitest run
npm run build
```
