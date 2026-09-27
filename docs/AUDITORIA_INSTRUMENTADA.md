# Auditoría instrumentada

> **Reutilizable en cualquier proyecto.** Este fichero no menciona nada de
> SwissJobHunter: se copia tal cual y sólo cambia la tabla de `INVARIANTES.md`.
> Vive en `docs/` y no en `.ai/prompts/` porque ese directorio está en el
> `.gitignore` de este repo — una definición que va a viajar no puede existir
> únicamente en un disco.

Esta auditoría **no busca problemas: comprueba afirmaciones**. La diferencia no es
de estilo. «¿Hay vulnerabilidades?» exige juicio, y un modelo ante una pregunta de
juicio o improvisa o pide ayuda. «Ejecuta `X`, debe salir con código 0» no exige
nada: se ejecuta y se anota.

Por eso esta auditoría la ejecuta **un solo modelo, de principio a fin**, sin
delegar ninguna parte. No porque se le prohíba pedir ayuda, sino porque **no queda
ningún paso en el que la ayuda signifique algo**.

Léela entera antes de empezar. No la resumas ni la reordenes.

---

## 1. Qué produce, y qué NO produce

**Produce** una tabla de hechos. Una fila por invariante:

```
| ID | Afirmación | Comando | Esperado | Obtenido | Salida | Veredicto |
```

**No produce**: gravedad, prioridad, recomendaciones, ni una lista de «lo más
urgente». Ordenar por importancia es juicio, y el juicio está fuera de alcance a
propósito — quien lee la tabla decide en diez minutos con los hechos delante.

Si terminas escribiendo un párrafo que empieza por «recomiendo» o «lo más grave
es», te has salido del encargo.

---

## 2. Vocabulario cerrado de veredictos

Sólo estas tres palabras son escribibles en la columna de veredicto:

- **`CUMPLE`** — el comando se ejecutó y su salida fue la esperada.
- **`NO_CUMPLE`** — el comando se ejecutó y su salida NO fue la esperada.
- **`CHECK_ROTO`** — el comando no se pudo ejecutar, o su resultado no es
  interpretable. **Esto es un defecto de la auditoría, no del proyecto.** Se
  arregla reescribiendo el check, no pidiendo opinión a nadie.

`CHECK_ROTO` es la pieza central del diseño. Sustituye a «no puedo determinarlo»,
que es la frase con la que una auditoría se deshace. Convierte el «no sé» en una
tarea concreta y propia: arreglar el check.

### Prohibido escribir, en cualquier parte del informe

> «requiere revisión» · «necesita un análisis más profundo» · «conviene que
> alguien lo valore» · «no concluyente» · «posible problema» · «podría ser» ·
> «señalo la incertidumbre» · «fuera de mi alcance»

Si una comprobación no se puede expresar como **comando + valor esperado**, no
pertenece a esta auditoría. Anótala aparte, en una lista llamada
`PARA_EL_CONTRATO`, y sigue. Convertirla en invariante es un trabajo posterior y
distinto.

---

## 3. Las siete mecánicas obligatorias

Cada una existe porque su ausencia produjo un fallo real y documentado. No son
buenas prácticas: son cicatrices.

### 3.1 El veredicto sale del CÓDIGO DE SALIDA, nunca de leer la salida

```sh
comando; echo "salida=$?"
```

Nunca `comando | grep -c error`, nunca «no veo errores en la salida». Un
formateador cambia su formato y tu recuento pasa a contar cero para siempre.

> **Incidente**: se declaró «lint en 0» contando las líneas que empezaban por una
> ruta de fichero. La salida por defecto de la herramienta no las lleva. Había
> cuatro errores vivos y el informe decía cero.

### 3.2 Todo check necesita un CONTROL NEGATIVO ejecutado y registrado

Antes de creer que un check pasa, hay que demostrar que **puede fallar**. Se rompe
a propósito lo que el check vigila, se comprueba que sale `NO_CUMPLE`, y se
restaura.

En la tabla, cada fila lleva su control negativo. Una fila sin control negativo
ejecutado vale `CHECK_ROTO`, no `CUMPLE`.

> **Incidente**: una opción del linter *sustituía* el conjunto de reglas en vez de
> ampiarlo, así que todas las anotaciones parecían innecesarias. El check «pasaba»
> midiendo otra cosa.
>
> **Y el motivo de fondo**: un modelo que memoriza el parche produce «todo verde»
> convincente. Un «todo verde» memorizado **no sobrevive** a la obligación de
> demostrar que cada check sabe ponerse rojo. Esta mecánica es el antídoto, y es la
> razón por la que esta auditoría puede correr sin un segundo modelo vigilando.

### 3.3 La VERSIÓN de cada herramienta es parte de la evidencia

Se anota `herramienta --version` junto al resultado, siempre.

> **Incidente**: la imagen traía la versión 0.16.8 y la integración continua fija
> la 0.15.14. Cuarenta y ocho errores que el CI no ve, y media hora persiguiendo
> fantasmas.

### 3.4 Todo comando que la DOCUMENTACIÓN promete se EJECUTA

No se lee: se ejecuta. Si un documento dice «recupéralo con este comando», se
lanza ese comando exacto.

> **Incidentes, dos**: el comando canónico de pruebas llevaba una opción de un
> complemento que **nunca estuvo instalado** — fallaba al arrancar, no al probar. Y
> un documento de archivo ofrecía un comando de recuperación que apuntaba a un
> repositorio retirado semanas antes.

### 3.5 Nunca concluir por METADATOS; comparar CONTENIDO

Nombre, fecha y tamaño no son evidencia de nada. Dos ficheros con el mismo nombre
pueden ser documentos distintos.

> **Incidente**: dos ficheros parecían copias fosilizadas por nombre y fecha.
> Contenían 68 entradas que no existían en ningún otro sitio. Borrarlos habría
> destruido contenido sin sustituto.

### 3.6 Árbol CONGELADO y SHA anotado junto a cada resultado

No se edita nada mientras corre una comprobación larga. Cada resultado se anota
con el `git rev-parse --short HEAD` del momento.

> **Incidente**: tres suites completas quedaron inservibles por editar ficheros
> con las pruebas en marcha. El resultado describía un árbol que ya no existía.

### 3.7 El check apunta a LO QUE SE ROMPE, no a su vecino

Si el fallo está en cómo se construye una cadena, el check tiene que mirar esa
cadena. Cubrir el endpoint que la recibe no es cubrir la cadena.

> **Incidente**: todas las pruebas cubrían el endpoint del stream; ninguna la forma
> de la URL. Un consumidor quedó mandando el parámetro antiguo, el servidor
> respondía 422, y una barra de progreso no avanzó durante días con la suite en
> verde.

---

## 4. Procedimiento

1. **Lee `INVARIANTES.md`** del proyecto. Es el contrato. Si no existe, la
   auditoría no se puede ejecutar: dilo y para. **No lo inventes** — redactarlo
   exige juicio y es un trabajo distinto.
2. **Anota la línea base**: `git rev-parse --short HEAD`, `git status --short`, y la
   versión de cada herramienta que vas a usar.
3. **Por cada invariante, en orden**: ejecuta su comando, anota el código de salida,
   ejecuta su control negativo, restaura, anota el veredicto.
4. **No te desvíes.** Si al pasar ves algo que huele mal y no está en el contrato,
   una línea en `PARA_EL_CONTRATO` y sigues. No investigues; eso es otra tarea.
5. **Entrega la tabla**, la línea base, y `PARA_EL_CONTRATO`. Nada más.

### Presupuesto y parada

- Un intento por check. Si falla por el entorno (falta un contenedor, no hay red),
  es `CHECK_ROTO` con el error pegado. No se reintenta tres veces ni se busca una
  ruta alternativa.
- Si más de un tercio de los checks sale `CHECK_ROTO`, **para y dilo**. El contrato
  está desalineado con el proyecto y seguir sólo produce ruido.

---

## 5. El bucle que la hace fuerte

Esta auditoría es excelente en **regresión y conformidad**, y ciega ante lo
**desconocido**. Un invariante que nadie escribió no se comprueba.

Eso se compensa con una regla: **cada incidente se convierte en un invariante
permanente**. El descubrimiento ocurre una vez, por el medio que sea —una
sospecha, un número que no cuadra, un usuario que se queja—; a partir de ahí es
mecánico para siempre.

Por eso `PARA_EL_CONTRATO` no es un cajón de sastre: es la entrada del bucle. Cada
corrida de esta auditoría debería dejar el contrato un poco más grande que como lo
encontró.
