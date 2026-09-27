# Prompt para lanzar la auditoría instrumentada

Pégalo **tal cual** como primer mensaje de una sesión abierta en el directorio del
proyecto. Es el mismo texto para los cinco: lo que cambia es el contrato que lee.

---

Ejecuta la auditoría instrumentada de este proyecto.

Protocolo completo en `docs/AUDITORIA_INSTRUMENTADA.md`. Contrato a ejecutar en
`INVARIANTES.md`. Lee los dos enteros antes de empezar y no los resumas.

Lo que NO es esta tarea: no busques problemas, no revises código, no propongas
mejoras, no ordenes nada por gravedad. Comprueba las afirmaciones del contrato, una
por una, en orden.

Reglas que no se negocian:

1. El veredicto de cada invariante sale del CÓDIGO DE SALIDA del comando
   (`comando; echo "salida=$?"`). Nunca de leer o contar líneas de la salida.
2. Cada invariante necesita su CONTROL NEGATIVO ejecutado: rompe a propósito lo que
   vigila, comprueba que se pone rojo, y restaura. Una fila sin control negativo
   ejecutado vale `CHECK_ROTO`, no `CUMPLE`.
3. Anota la versión de cada herramienta que uses, junto al resultado.
4. Ejecuta los comandos desde la RAÍZ del proyecto salvo que el contrato diga otra
   cosa. Un check lanzado desde el directorio equivocado no da rojo: da una mentira.
5. No edites ningún fichero mientras corre una comprobación larga, y anota el
   `git rev-parse --short HEAD` (o «sin repo») con cada resultado.
6. Sólo tres veredictos son escribibles: `CUMPLE`, `NO_CUMPLE`, `CHECK_ROTO`.
   `CHECK_ROTO` significa que el check no se pudo ejecutar, y es un defecto DE LA
   AUDITORÍA: se arregla reescribiendo el check, no pidiendo opinión a nadie.
7. Prohibido escribir «requiere revisión», «necesita un análisis más profundo»,
   «conviene que alguien lo valore», «no concluyente», «posible problema» o
   «fuera de mi alcance». Si algo no se puede expresar como comando + valor
   esperado, una línea en `PARA_EL_CONTRATO` y sigues.
8. Un intento por check. Si falla por el entorno, es `CHECK_ROTO` con el error
   pegado: no reintentes tres veces ni busques una ruta alternativa.
9. Si más de un tercio de los checks sale `CHECK_ROTO`, para y dilo: el contrato
   está desalineado con el proyecto y seguir sólo produce ruido.

Varios invariantes del contrato ya vienen anotados como rojos, con la medición y la
fecha. **No los des por buenos**: vuelve a ejecutarlos. Si alguno ha cambiado de
estado, eso es el hallazgo.

Entrega, y nada más:

- La línea base: SHA, estado del árbol, versión de cada herramienta.
- La tabla: `| ID | Afirmación | Comando | Esperado | Obtenido | Salida | Control
  negativo | Veredicto |`, una fila por invariante, en el orden del contrato.
- `PARA_EL_CONTRATO`: lo que viste de paso y no se puede expresar como comando
  todavía.

Escríbelo en `AUDITORIA-<fecha>.md` en la raíz del proyecto.
