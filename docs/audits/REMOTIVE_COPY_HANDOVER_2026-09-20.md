# Remotive — ensayo de identidad en copia NAS

**Resultado: ensayo de sink aprobado; fuente NO activada en producción.**
No equivale a cierre del punto 4 ni a aceptación del feed servido.

## Entorno y ejecución

- Imagen limpia `swissjob-core:point4-7724037`.
- Copia aislada `swissjob-f-rehearsal-20260919`, base `core_copy`; sin puertos ni
  red externa. Cliente efímero comparte sólo el namespace de red de esa copia.
- Guardas del ensayo: URL fija `...@127.0.0.1:5432/core_copy`, comprobación de
  `current_database()`, timeout SQL 60 s y fuente `remotive` inicialmente ausente.
- Una petición pública independiente a Remotive, `limit=200`; cuerpo preservado
  en el NAS. Parseo posterior mediante el adaptador productivo sobre ese cuerpo,
  sin otra descarga. No se copiaron datos privados al ordenador.
- Helper y cuerpo en el directorio privado del ensayo NAS:
  `/share/CACHEDEV1_DATA/Public/swissjob-f-rehearsal.goIBte/`
  (`point4_remotive_copy_probe.py`, `remotive-public-body.json`).
- Registro agregado local: `/tmp/point4-remotive-copy-probe.log`.

El primer intento del helper falló por su ruta de importación, antes de descargar
o ingerir. Con `PYTHONPATH=/app`, la comprobación se ejecutó completamente.

## Resultado medido

| Comprobación | Resultado |
|---|---:|
| Cuerpo público | 155.621 bytes |
| Ofertas parseadas | 17 |
| Admitidas, ventana 7 días + refresco de conocidas | 11 |
| Vacantes históricas reutilizadas | 6 |
| Canónica presente en las admitidas | 11/11 |
| Segunda pasada: ids y punteros canónicos idénticos | sí |
| Rollback final; fuente creada ya no existe | sí |

SHA256 del cuerpo:
`0cd7acba69236d6c16354a39512d05cb1522e234c4b28d4bcd17413027a49061`.
Coincide con la respuesta de la prueba de paridad del 19-09: las siete columnas
canónicas comparadas entonces no tenían diferencias entre adaptadores.

El histórico se resolvió por URL exacta y cadena de fusiones antes de la ingesta.
Para cada oferta con histórico, la vacante elegida por el sink debe pertenecer a
ese conjunto: no basta con que exista algún UUID nuevo. Todos los cambios de
fuente/scope/listings/revisiones/generación quedaron en UNA transacción revertida.

## Pendiente para el corte real

Planificador nativo y exclusión global desplegados; impedir nuevos despachos
Remotive en todos sus productores anteriores y drenar los que estén en vuelo;
registrar el corte de CDC; provisionar scope con ventana explícita; primera
cosecha real y repetición; comprobar ofertas nativas por el BFF, feedback,
candidaturas/documentos, salud y frescura. No retirar las demás fuentes por este
resultado. La copia conserva su caducidad original: 21-09 a las 20:00 UTC.
