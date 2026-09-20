# Punto 4 — recuperación compatible de feedback (ensayo aislado)

20-09-2026. **Ensayo aprobado; escritor productivo todavía local.**
No equivale al corte de producción ni al cierre del punto 4.

## Qué se verificó

Copias `source_copy`/`core_copy`, PostgreSQL sin red ni puertos publicados,
dentro del NAS. Imágenes exactas desplegadas: core y BFF `point4-bac5e78`.
No se exportaron datos ni credenciales. Ningún correo/candidatura real.

1. Reaplicado el plan sellado de feedback sobre la copia: **112 cambios**,
   veredicto verified. El ensayo previo ya acreditaba apply/replay/revert
   PRE-activación y equivalencia material de la restauración.
2. Creada una vacante sintética por el sink, en un scope deshabilitado de ensayo;
   comprobado que NO existe Job correspondiente en la copia fuente.
3. BFF real, con su autenticación y routers mediante ASGI (sin lifespan ni
   schedulers); cliente core HTTP real a una API aislada en loopback del mismo
   namespace. Credencial temporal de esa copia, nunca la productiva.
4. Sobre el UUID exclusivamente core: rechazo, guardado, clear, nuevo guardado
   y señal implícita HTTP 200. El otro usuario rechaza la misma vacante sin
   cambiar la decisión del primero. Guardados totales 1 y 18.
5. Freeze: POST explícito, DELETE y POST implícito sin auth → 503 antes de auth;
   `/health/feedback` informa frozen/core. Lecturas autenticadas siguen vivas.
6. Reinicio real del proceso API y nuevo proceso BFF, mismas imágenes/autoridad:
   el guardado exclusivamente nativo continúa accesible, separado por usuario.
7. Core inaccesible simulado con un puerto vacío del namespace aislado:
   escritura BFF → 503, SIN fallback; al restablecer destino, decisión intacta.
8. Hash de TODAS las filas `match_results` fuente idéntico antes/después de cada
   fase. Cero escritura dual. La credencial se revoca al finalizar; API temporal
   se detiene y elimina. La copia y el fixture conservan el vencimiento ya
   registrado del ensayo (21-09 20:00 UTC), no se prorroga.

La primera puesta en marcha auxiliar usó un UID incompatible con los permisos
del código core y falló antes de servir; se repitió con el usuario `core` de la
imagen. El cliente BFF necesitó `PYTHONPATH=/app`. No se contaron como pruebas
positivas ni requirieron modificar imágenes o código funcional.

## Qué significa recuperación POST-corte

La retirada de productores NO exige volver a repartir la autoridad del feedback.
Se recupera el servicio con una imagen compatible manteniendo core como escritor:
congelar escrituras, drenar, conservar BD y eventos, recrear la versión compatible,
verificar marcas nuevas/lecturas, abrir escrituras. No se restaura un snapshot
PRE-corte encima del estado nuevo ni se desactiva CORE_FEEDBACK_ENABLED.

El retorno a un escritor legacy que sólo conoce MD5 NO está implementado para
vacantes exclusivamente core. **No llamarlo rollback soportado.** El comando
`feedback_cutover revert` sigue siendo exclusivamente PRE-activación. Esta cota
no puede ocultarse en el procedimiento operativo ni en un rollback de imagen.
Si se revierte una fuente de cosecha, el feedback conserva su autoridad core.

Esto prueba reinicio y fallo de transporte, no cualquier corrupción arbitraria de
BD ni un rollback a E.15. La referencia compatible es la imagen bac5e78 exacta;
E.15 no entiende las autoridades nuevas de perfiles ni feedback escolar.

## Reproducción

Scripts versionados:
`scripts/rehearse_feedback_recovery_prepare.py` (prepare/revoke) y
`scripts/rehearse_feedback_recovery_bff.py` (exercise/verify).
Sólo aceptan las URLs hardcodeadas de las copias. Directorio de evidencia privado
montado en `/evidence`; plan previo `feedback-plan.private.json` requerido.
Prepare: usuario NAS 1000, CORE_DATABASE_URL apuntando a core_copy.
API auxiliar: usuario de la imagen, puerto 18080 loopback, sin puerto publicado.
BFF: usuario app, PYTHONPATH=/app, namespace de PostgreSQL aislado, evidencia RO.
Entre exercise y verify reiniciar la API. No ejecutar contra producción.

No hay cambio funcional después de las suites **core 1.499 passed** y
**BFF 2.459 passed, 3 skipped, 4 xfailed**. Este ensayo no se suma a esos conteos.
