# Contrato de centralización total de la cosecha

Estado: **RATIFICADO — ejecución abierta**
Fecha de ratificación: **2026-10-13**
Última revisión: **2026-10-14**
Deuda asociada: **DT-144**

## 1. Propósito

Este documento es la fuente canónica para completar la arquitectura originalmente
acordada entre `jobhunt-core`, SwissJob y ReactPortfolio. Resuelve la desviación
por la que Core es autoridad de parte del corpus mientras los dos productos aún
conservan cosechadores propios.

El objetivo contractual es:

> Toda fuente de ofertas operativa y autorizada se cosecha, normaliza,
> deduplica y persiste exclusivamente en Core. SwissJob y ReactPortfolio
> consumen el corpus común mediante Core y no se conectan directamente a los
> portales, no guardan sus credenciales ni ejecutan cosechas locales.

SwissJob y ReactPortfolio siguen siendo productos diferentes. Cada uno conserva
su interfaz, autenticación, reglas de presentación y experiencia de usuario. El
estado funcional que comparten con Core debe permanecer aislado por usuario y
consumer; centralizar la cosecha no autoriza a mezclar preferencias, candidaturas
o notificaciones entre productos.

## 2. Carácter vinculante y precedencia

Este contrato fija el destino y la definición de terminado. Los detalles de
implementación pueden evolucionar, pero no pueden convertir una cosecha local en
estado final aceptable.

En esta materia, el orden de precedencia es:

1. Este contrato y sus comprobaciones ejecutables.
2. Una ADR posterior que lo sustituya expresamente siguiendo la sección 12.
3. La memoria vigente y la deuda técnica.
4. Planes, backlogs, auditorías y actas históricas.

Una nota de progreso, un cierre anterior o el nombre `core_primary` no acreditan
el cumplimiento si sigue existiendo un productor local, una mezcla Core + local
o una conexión directa desde un BFF a un portal.

El propietario ha fijado GitHub Free como único forge. Todo el trabajo se
realiza en ramas de feature, `main` permanece intacta hasta que DT-144 esté
completa y la fusión final requiere su aprobación explícita. El control se
completa con historial Git, CI en cada push y un bloqueo que impide desplegar
cualquier SHA distinto del `github/main` vigente. No se instala un segundo
forge ni se presenta este flujo como una protección de rama que GitHub Free no
ofrece al repositorio privado.

## 3. Estado que motivó la reapertura

La fotografía reconciliada el 13 de octubre de 2026 muestra una centralización
parcial:

- Core tiene sus fuentes y scopes propios, pero eso no demuestra que posea todas
  las fuentes de ambos productos.
- ReactPortfolio conserva once fuentes propias y una composición de catálogo
  que permite combinar Core con fuentes locales.
- SwissJob conserva registros de proveedores y scrapers, incluidos productores
  escolares.
- Jobicy se cosecha de forma independiente en ambos productos. Core dispone de
  adaptador nativo, pero no de una fuente o scope productivo que lo ejecute.

Estos hechos reabren la centralización como **DT-144**. Los cierres anteriores
siguen siendo evidencia válida para su alcance, pero no equivalen al cierre de
este contrato.

Los recuentos son una línea base, no una lista cerrada. La Fase 0 debe detectar
fuentes registradas, dinámicas, programadas o configuradas que no aparezcan en
esta fotografía.

## 4. Invariantes no negociables

Al terminar la migración deben cumplirse simultáneamente estas reglas:

1. Core es el único propietario de la captura externa de ofertas.
2. Cada fuente tiene como máximo un productor publicador activo.
3. Toda fuente nueva se implementa directamente en Core.
4. SwissJob y ReactPortfolio no contienen schedulers, workers o registros de
   cosecha activos.
5. Los BFF no poseen credenciales de portales ni realizan tráfico hacia ellos.
6. No existe fallback, mezcla o modo operativo Core + catálogo local.
7. La normalización, identidad, deduplicación, procedencia y vigencia se
   resuelven en Core.
8. Los consumidores observan el mismo corpus canónico, sujeto solo a permisos,
   filtros y reglas de producto explícitas.
9. Los datos y efectos de usuario permanecen aislados por usuario y consumer.
10. Una fuente retirada se marca como tal; no se conserva código activo sin una
    fuente operativa y autorizada.

Se pueden modificar endpoints, cadencias, orden de las oleadas y detalles de un
adaptador cuando haya evidencia técnica. No se pueden modificar mediante una
decisión ordinaria la propiedad exclusiva de Core, la retirada de productores
locales ni la definición de terminado.

### 4.1. Reglas imperativas de ejecución

Estas reglas se aplican a todas las fases, migraciones, refactorizaciones,
revisiones y escrituras de código relacionadas con DT-144:

1. **Nunca asumir nada; comprobarlo todo antes de decidir.** Toda afirmación que
   afecte a una decisión debe contrastarse con el estado vigente del código, la
   configuración, las migraciones, las pruebas, los datos o el despliegue que
   corresponda. Una documentación histórica, un nombre de bandera o una
   coincidencia aparente no son prueba suficiente. Si algo no puede comprobarse,
   se registra como desconocido o bloqueo explícito y no se presenta como hecho.
2. **Trazar el recorrido completo afectado.** Antes de modificar una pieza se
   comprueban sus entradas, llamadores, consumidores, persistencia, efectos
   laterales, configuración de producción y mecanismo de recuperación. La
   decisión y su evidencia deben quedar en el checkpoint de DT-144.
3. **Respetar siempre los principios arquitectónicos.** Todo cambio debe
   preservar responsabilidad única (SRP), cohesión alta, acoplamiento bajo,
   legibilidad y consistencia con los patrones válidos del proyecto.
4. **Resolver conjuntamente arquitectura y YAGNI.** Si parece existir tensión
   entre esos principios y YAGNI, se elige la solución mínima, completa y clara
   que satisfaga ambos. No se crea una abstracción, interfaz, wrapper, capa,
   repositorio o servicio únicamente para aparentar el cumplimiento de un
   principio. Primero se reutilizan los límites y utilidades existentes.
5. **Optimizar cada migración y cada código nuevo.** Se busca la mayor eficiencia
   razonable en CPU, memoria, red, almacenamiento, procesos y mantenimiento con
   el mínimo código posible, sin perder ni degradar funcionalidades, seguridad,
   validación, accesibilidad, observabilidad o recuperación.
6. **Medir antes y después.** Una optimización no se acepta por intuición: debe
   tener línea base, prueba comparable y resultado documentado. Se prioriza
   eliminar trabajo, código y duplicación; no se introducen microoptimizaciones
   ni complejidad sin un cuello de botella comprobado.
7. **Dejar la mínima prueba útil.** Toda lógica no trivial modificada debe quedar
   cubierta con el mecanismo de pruebas existente y con evidencia proporcional
   al riesgo. Las migraciones deben ser incrementales, idempotentes cuando
   corresponda y disponer de corte y recuperación verificables.

El cumplimiento de estas reglas es una puerta de revisión. Un cambio que no
aporte la evidencia aplicable o que añada complejidad sin necesidad demostrada
no puede avanzar a despliegue.

## 5. Inventario y estados de migración

Habrá un único inventario legible por máquinas para todas las fuentes. No se
duplicará la misma política en registros independientes. Cada entrada contendrá,
como mínimo:

- nombre canónico y alias;
- producto y módulo que la cosechan actualmente;
- propietario actual y propietario objetivo;
- scheduler o trigger;
- credenciales y secretos requeridos, sin incluir sus valores;
- tipo de transporte: API, feed, HTML o navegador;
- identidad externa y estrategia de deduplicación;
- última ejecución correcta y evidencia;
- scope de Core;
- estado y motivo de bloqueo, cuando exista.

Estados permitidos:

```text
local_pending -> core_shadow -> core_primary -> local_removed
```

`retired` es un estado terminal alternativo para una fuente que ya no es
operativa o autorizada. No equivale a migrarla y exige evidencia de la decisión.

Reglas del inventario:

- `local_pending` solo puede disminuir.
- `core_primary` y `local_removed` solo pueden avanzar.
- Ninguna fuente puede quedar sin propietario durante una transición.
- Dos productores pueden consultar temporalmente la misma fuente únicamente en
  `core_shadow`; solo uno puede publicar al corpus utilizado por los productos.
- Un retroceso requiere el rollback temporal definido en la sección 11.

## 6. Plan de ejecución

### Fase -1 — blindaje del objetivo

- Publicar este contrato como documento canónico.
- Crear el inventario único de propietarios y estados.
- Añadir comprobaciones CI del inventario y de las invariantes estructurales.
- Ejecutar CI en cada rama de trabajo y conservar `main` sin cambios hasta la
  aprobación explícita de la fusión final por el propietario.
- Prohibir nuevas dependencias, credenciales o conexiones de portales en los
  BFF.
- Añadir un bloqueo de despliegue ante fuentes duplicadas, sin propietario, en
  regresión o cuando el SHA local no coincida exactamente con `github/main`.
- Incorporar a la revisión final la evidencia exigida por las reglas de la
  sección 4.1, incluida la justificación de código o abstracciones nuevas y las
  mediciones de cualquier optimización declarada.

**Puerta de salida:** CI operativo en las ramas de feature, inventario y
ratchets activos, y ambos despliegues rechazando el SHA de trabajo mientras no
sea `github/main`. La migración se desarrolla y valida en esas ramas; no se
fusiona ni despliega antes de completar DT-144 y obtener la aprobación final.

### Fase 0 — inventario productivo verificable

- Enumerar registros estáticos y dinámicos, tareas, crons, workers, rutas
  manuales, cachés, colas, tablas, secretos y conexiones salientes.
- Contrastar configuración de repositorio con procesos y scopes desplegados.
- Medir por fuente volumen, campos, vigencia, duplicados, frecuencia y última
  ejecución correcta.
- Identificar consumidores del catálogo local y dependencias de escritura.
- Clasificar cada fuente como operativa, averiada, deshabilitada, no autorizada
  o retirada.

**Puerta de salida:** toda fuente descubierta tiene una entrada y un propietario.

### Fase 1 — completar la frontera de ingestión de Core

Se reutilizará la infraestructura existente. Solo se añadirán capacidades que
una fuente real necesite:

- contrato normalizado de oferta y procedencia;
- identidad y deduplicación idempotentes;
- scopes, cadencias, salud, métricas y aislamiento de fallos;
- pruebas deterministas con respuestas guardadas;
- worker de renderizado dentro de la frontera operativa de Core únicamente para
  fuentes que demuestren necesitar navegador.

No se construirá otro framework, bus o servicio paralelo si los puertos actuales
de Core cubren el caso.

### Fase 2 — piloto Jobicy

Jobicy valida el procedimiento porque actualmente es el duplicado confirmado:

1. Crear y probar sus scopes en Core.
2. Ejecutar Core en sombra sin publicar duplicados.
3. Comparar volumen, campos, vigencia, identidad y errores con ambos productores
   locales.
4. Corregir las diferencias en la frontera común.
5. Detener de forma coordinada los productores de ReactPortfolio y SwissJob.
6. Activar Core como único publicador.
7. Verificar ambos productos durante al menos dos ciclos correctos.
8. Eliminar código, configuración y credenciales locales tras la observación.

### Fase 3 — APIs y feeds públicos

Migrar individualmente las fuentes públicas confirmadas por el inventario. Entre
las candidatas conocidas están RemoteOK, Himalayas, SwissTechJobs, ICTJobs,
SwissDevJobs y Job-Room.

La lista no se considera completa hasta cerrar la Fase 0.

### Fase 4 — APIs autenticadas

Migrar Adzuna, JSearch y cualquier otra API autenticada confirmada. Los secretos
se instalarán en Core y se retirarán de ambos productos. La falta de una
credencial válida se documentará como bloqueo; no se declarará la fuente
centralizada ni operativa.

### Fase 5 — fuentes HTML

Evaluar y migrar las fuentes HTML confirmadas, entre ellas las candidatas
DynamiteJobs, JobScout24, GastroJob, TES, Schuljobs, MyScience, Stelle Admin,
ProZ y Remote.co.

Antes de trasladar código se comprobarán disponibilidad, autorización y
funcionamiento. Un scraper muerto se retira con evidencia en vez de copiarse a
Core. El navegador se usará solo cuando una petición HTTP normal no cubra el
portal.

### Fase 6 — productores escolares

- Incorporar los colectores `swiss_schools_*` confirmados al límite de ingestión
  escolar existente en Core.
- Centralizar programación, identidad, deduplicación, salud y persistencia.
- Convertir cualquier refresco manual de los productos en una orden o proxy a
  Core, nunca en una ejecución local.
- Retirar navegadores y productores escolares de los BFF.

### Fase 7 — retirada de ReactPortfolio

Cuando su última fuente sea `core_primary`:

- eliminar `CompositeCatalog` y cualquier combinación Core + local;
- retirar registros, workers, schedulers, cachés y actualizadores de portales;
- retirar rutas que desencadenen una cosecha local;
- retirar secretos y clientes de portales;
- servir todas las consultas de ofertas desde Core.

ReactPortfolio conserva su experiencia de usuario y las operaciones de producto
que no sean captura externa.

### Fase 8 — retirada de SwissJob

Cuando su última fuente sea `core_primary`:

- retirar `daily_harvest` y las cadenas locales de descarga;
- retirar registros de providers y scrapers;
- eliminar persistencia local de nuevas ofertas y secretos de portales;
- conservar las migraciones históricas necesarias para reproducibilidad;
- mantener autenticación, CV, correo, matching, candidaturas y demás funciones
  de producto mediante las autoridades ya acordadas.

### Fase 9 — limpieza y simplificación

- Borrar código desconectado solo después de comprobar sus consumidores.
- Retirar dependencias, imágenes, permisos, colas, cachés y tablas sin uso.
- Consolidar documentación duplicada en este contrato, la memoria y el índice
  técnico.
- Medir la reducción de código, procesos, secretos y conexiones externas.

### Fase 10 — aceptación y cierre productivo

- Ejecutar pruebas unitarias por adaptador y pruebas de contrato Core/BFF.
- Probar deduplicación, idempotencia, reintentos y fallo parcial de una fuente.
- Probar aislamiento de usuarios y consumers.
- Probar los recorridos principales de ambos frontends.
- Observar varios ciclos reales y demostrar ausencia de tráfico directo desde
  los BFF hacia portales.
- Asociar el cierre a los SHA exactos desplegados de los tres componentes.

## 7. Procedimiento obligatorio por fuente

Toda fuente debe completar, sin saltos, esta lista:

1. Entrada completa en el inventario.
2. Adaptador y pruebas en Core.
3. Scope desplegado inicialmente sin autoridad de publicación.
4. Comparación de salida contra el productor vigente.
5. Validación de identidad, deduplicación y enlace con el histórico.
6. Plan de corte y rollback revisado.
7. Drenaje y desactivación del productor local.
8. Activación de Core como único publicador.
9. Dos o más ciclos correctos y pruebas desde ambos productos.
10. Periodo de observación proporcional a la cadencia de la fuente.
11. Eliminación del código, secretos y configuración locales.
12. Actualización de DT-144 con evidencia y siguiente paso exacto.

No basta con que exista un adaptador ni con que una bandera se llame
`core_primary`.

### 7.1. Metodología verificable de ejecución y relevo

Cada portal se trabaja como una unidad vertical completa y repetible. No se
empieza el siguiente ocultando una diferencia sin explicar en el actual:

1. **Preflight Git y producción.** Leer rama, upstream, remotos, SHA de ambos
   `main`, worktrees y cambios ajenos. Conservar los cambios preexistentes y
   trabajar sólo en las ramas DT-144 de GitHub Free.
2. **Trazado real de extremo a extremo.** Localizar productor, registro,
   scheduler, rutas, caché, persistencia, credenciales, manifiesto, tráfico
   saliente y consumidores. Contrastar código y configuración con el runtime;
   un módulo presente no demuestra que esté activo.
3. **Línea base reproducible.** Capturar una respuesta real saneada, registrar
   fecha, parámetros, volumen, huellas SHA-256, identidad, campos, orden,
   duplicados y comportamiento ante vacío/fallo. No versionar secretos ni datos
   personales innecesarios.
4. **Core mínimo.** Reutilizar el provider, normalizador, sink, agregado y
   provisionador existentes; añadir sólo la capacidad que el contrato real del
   portal exige. El scope nace deshabilitado y no toca producción.
5. **Pruebas por capas.** Cubrir entrada válida, envoltorio inválido, vacío,
   fallo parcial, paginación, identidad, raw, campos visibles, idempotencia y
   provisión. Ejecutar después el doble ciclo sobre PostgreSQL aislado.
6. **Paridad explicada.** Comparar campo a campo y comprobar volumen, vigencia,
   orden funcional y deduplicación. Toda diferencia se corrige o se documenta
   como mejora deliberada con evidencia; nunca se acepta por intuición.
7. **Consumidor y apagado local.** Mantener las rutas públicas mediante el
   adaptador Core compartido, desconectar registros/imports locales, bloquear
   backend y worker mediante el manifiesto y reducir el ratchet. El código
   antiguo queda desconectado sólo como rollback hasta la observación final.
8. **Validación y publicación.** Ejecutar pruebas focales, suites completas,
   comprobaciones estructurales y diff. Crear commits estrechos, publicar sólo
   las ramas feature y comprobar todos los jobs de CI de GitHub.
9. **Checkpoint antes de avanzar.** Registrar estados, archivos, resultados,
   SHA, CI, evidencia, diferencias, rollback, cambios ajenos preservados y el
   siguiente comando o edición exactos. Si el trabajo queda a medias, marcarlo
   expresamente como tal.

Por decisión del propietario, las maniobras productivas de los puntos 3 y 7 a
11 de la sección 7 se ensayan de forma aislada durante el desarrollo, pero se
ejecutan agrupadas sólo después de terminar todas las fuentes y aprobar la
fusión final. Hasta entonces el inventario sigue describiendo producción y los
`main` no cambian.

## 8. Comprobaciones ejecutables

Durante la migración, CI debe impedir:

- más de un publicador activo por fuente;
- fuentes sin propietario;
- retrocesos permanentes desde Core a local;
- registros nuevos de proveedores en los BFF;
- secretos o dependencias de scraping nuevos en los BFF;
- reactivación silenciosa de una fuente retirada.

En el estado final debe fallar además si encuentra:

- un registro local con productores activos;
- `CompositeCatalog` o una ruta equivalente Core + local;
- tareas de cosecha externa en SwissJob o ReactPortfolio;
- credenciales de portales en sus manifiestos;
- conexiones salientes de los BFF hacia fuentes de ofertas;
- escrituras locales de nuevas ofertas.

Las comprobaciones estáticas se acompañarán de sondas del despliegue vivo. Un
árbol Git correcto no demuestra por sí solo que producción ejecuta esas
revisiones.

## 9. Pruebas mínimas de aceptación

- Cada adaptador transforma respuestas válidas y rechaza entradas inválidas sin
  corromper el corpus.
- Repetir una cosecha no crea duplicados.
- Dos fuentes que representan la misma vacante respetan la identidad canónica y
  conservan su procedencia.
- El fallo o rate limit de una fuente no detiene las demás.
- SwissJob y ReactPortfolio ven la misma vacante canónica cuando sus filtros la
  incluyen.
- Los cambios de estado de un usuario o consumer no contaminan al otro.
- La retirada de un productor local no degrada búsquedas, detalles, matching,
  alertas ni candidaturas.
- Las rutas reales de ambos frontends funcionan con los productores locales
  detenidos.
- La observación de red confirma que solo Core contacta con los portales.

## 10. Definición de terminado

DT-144 solo puede cerrarse cuando se cumplan a la vez estas condiciones:

- el 100 % de las fuentes operativas y autorizadas está en `core_primary` o
  `local_removed` según el inventario;
- no quedan entradas en `local_pending` ni `core_shadow`;
- ReactPortfolio y SwissJob tienen cero productores externos activos;
- ninguno contiene secretos o tráfico de portales;
- no existe catálogo compuesto ni fallback local;
- toda oferta nueva entra exclusivamente por Core;
- ambos productos pasan sus recorridos sobre el corpus común;
- las sondas productivas confirman las mismas invariantes;
- el contrato, el inventario y los checks están asociados al SHA exacto
  aprobado, fusionado en GitHub y desplegado.

Las fuentes bloqueadas no desaparecen del recuento. Deben resolverse o retirarse
explícitamente con evidencia. La frase «alcance autorizado completado» no puede
sustituir esta definición de terminado.

## 11. Rollback de incidencia

Un productor local solo puede reactivarse de forma temporal para restaurar el
servicio durante una incidencia. El registro debe incluir:

- identificador de incidencia;
- fuente y revisiones afectadas;
- responsable;
- razón y evidencia;
- hora de inicio y caducidad;
- desactivación o drenaje previo del publicador de Core;
- criterio para volver a Core.

El rollback no cambia el propietario objetivo, no permite dos publicadores y no
puede convertirse en excepción permanente. Al caducar debe restaurarse Core o
mantenerse DT-144 abierta como bloqueo explícito.

## 12. Control de cambios

Los avances y correcciones técnicas no requieren reescribir el objetivo. Un
cambio que pretenda permitir cosecha local como estado final solo es válido si
una nueva ADR:

1. declara expresamente que sustituye este contrato;
2. identifica el requisito que obliga al cambio;
3. analiza impacto, seguridad, datos, operación y rollback;
4. obtiene aprobación explícita del propietario;
5. modifica simultáneamente la definición de terminado y los checks;
6. queda enlazada desde la memoria y DT-144.

Sin todos esos pasos, el cambio es una desviación y debe bloquearse.

## 13. Continuidad y actualización de la deuda

Después de cada fuente u oleada se añadirá a DT-144 un checkpoint con:

- fuentes y estados antes/después;
- módulos y configuración modificados;
- pruebas y resultados;
- SHA desplegados y evidencia productiva;
- secretos, procesos y código local retirados;
- riesgos o bloqueos reales;
- rollback disponible;
- siguiente paso exacto.

La documentación de avance nunca marcará el contrato como cerrado antes de
superar íntegramente la sección 10. Así, otro agente puede retomar el trabajo
desde DT-144 sin reinterpretar el objetivo ni repetir la auditoría completa.
