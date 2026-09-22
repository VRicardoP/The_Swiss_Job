# Verificación del cambio de idioma y del recorrido real — punto 5

Fecha: 2026-09-22, aproximadamente 20:09–20:14 UTC.
HEAD revisado: `f607bb4`; cambio funcional: `f331c0d`.

## Veredicto

**APPROVE del cambio acotado de memoización como mitigación.** No se ha
demostrado una regresión funcional grave introducida por ese cambio.
**PUNTO 5 ABIERTO: no se certifica la aceptación integral de rendimiento.**

La caché reduce trabajo repetido y está desplegada. No resuelve la primera
carga, no convierte la detección en determinista ni corrige la pérdida del
campo `language` entre capas. La sonda endurecida todavía tiene controles
negativos que pasan por una causa distinta de la anunciada.

Revisión con `code-review` y `yagni`: no cambiar funcionalidad ni ampliar la
arquitectura para resolver estos hallazgos. Esta sesión sólo verifica.

## Confirmaciones ejecutadas

- NAS: BFF `swissjob-backend:point5-f331c0d`, sin reinicios Docker.
- SHA-256 del archivo `services/translation_service.py` desplegado idéntico al
  local: `69e96a5e77a1d0c6f6e43baf4023efa03ab5554884f4a808173a6cb6837205ec`.
- Core: `ready`, `core0051`, API `cf260b1`, `authoritative=true`. Worker y captura
  siguen en `51be757`, como ahora declara el acta.
- Última salud observada: 19:14 UTC, `alertas=[]`, 17 scopes habilitados con estado.
- Frontend del repositorio: `MatchPage.jsx:40` pide `useMatchResults(3000, 0)`;
  `hooks/useMatch.js:16` envía `translate=false`.
- El frontend utiliza el conjunto recibido para categorías, contadores,
  watchlist, orden por urgencia y métricas. Reducir 3000 a 20 sin conservar esas
  operaciones cambiaría el comportamiento; no es una corrección suficiente.

Canario HTTP autenticado sobre un perfil existente, token efímero sólo en
memoria, sin traducción, sin escribir feedback y sin imprimir datos personales:

| Petición | Tiempo | HTTP | Items / total |
|---|---:|---:|---:|
| `/api/v1/match/results?limit=20&offset=0&translate=false` | 1,226 s | 200 | 20 / 1800 |
| `/api/v1/match/results?limit=3000&offset=0&translate=false` | 7,837 s | 200 | 1800 / 1800 |

IDs únicos y los primeros 20 IDs del recorrido completo iguales y en el mismo
orden que la respuesta corta. Una muestra por forma: **no son p50/p95**.
No se ejecutó la sonda publicada íntegra porque también invoca traducción con
LLM; se usaron las dos lecturas anteriores sin llamadas facturables deliberadas.

## Hallazgos

### P2 — Tres controles negativos no prueban su condición

Archivo: `scripts/probe_served_endpoints.py:78–81`; afirmación que depende de
ellos: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md:111`.

Los casos de total incoherente, cardinalidad distinta y total distinto usan
`results`, pero `check_matches` exige `data`. Los tres fallan antes de alcanzar
el control correspondiente: `no list of results; keys=['results', 'total']`.

Prueba de mutación ejecutada en memoria: se quitaron únicamente las guardas de
cardinalidad y total. `self_test()` siguió devolviendo los cinco controles
detectados, mientras la función mutada aceptaba 20 elementos con `total=3`.
Además, la función original acepta `data=[{}]*20, total=1800`: no valida las
identidades que promete su docstring. No es una corrupción observada del feed;
es un defecto de su instrumento de aceptación.

**Corrección mínima:** usar `data` en esos tres casos; partir de un cuerpo válido
y romper una sola propiedad por caso. Comprobar el motivo esperado del rechazo,
añadir un control positivo y hacer que eliminar cada guarda rompa su prueba.
Validar presencia/unicidad de `job_hash` y comparar IDs, orden, scores y longitud
real en la prueba de equivalencia. No exigir campos irrelevantes a cada ruta.

Reproducción sin red, base de datos ni ejecución del `main` de la sonda:

```sh
python3 - <<'PY'
import ast, json
from pathlib import Path
t = ast.parse(Path('scripts/probe_served_endpoints.py').read_text())
names = {'ProbeFailure', 'check_catalog', 'check_matches', 'self_test'}
t.body = [n for n in t.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
          and n.name in names]
ns = {'json': json}
exec(compile(t, 'probe', 'exec'), ns)
try:
    ns['check_matches']({'results': [{}]*20, 'total': 3}, expected_len=20)
except ns['ProbeFailure'] as e:
    print(e)  # falla por clave, no por total
print(ns['check_matches']({'data': [{}]*20, 'total': 1800}, expected_len=20))
fn = next(n for n in t.body if getattr(n, 'name', None) == 'check_matches')
for n in ast.walk(fn):
    if isinstance(n, ast.If) and n.lineno in (63, 66, 68):
        n.test = ast.Constant(False)  # guardas en f607bb4
ast.fix_missing_locations(t)
exec(compile(t, 'mutant', 'exec'), ns)
ns['self_test']()  # sigue verde sin las tres guardas
PY
```

### P2 — Diagnóstico incompleto: persistir idioma no basta para servirlo

Ubicaciones: `jobhunt_core/api/schemas.py:37`, `jobhunt_core/api/v1.py:240`,
`backend/services/matching/core_client.py:302`.

**Preexistente; no introducido por la memoización.** La afirmación de que la
ausencia observada se explica sólo por los normalizadores no está demostrada:
el contenido canónico admite `language`, pero `VacancyDTO` no lo declara,
`_vacancy_dtos` no lo transmite y `_job_view` no lo asigna a `CoreJobView`.

Reproducciones ejecutadas localmente:

- Normalizador aislado con `{title: 'Software Engineer', language: 'de'}`:
  canónica `language='de'`; al construir/serializar `VacancyDTO`, campo ausente.
- `_job_view({'title':'Software Engineer','language':'de'}, 'core').language`:
  **None**, incluso recibiendo explícitamente el dato.

**Corrección mínima:** transportar el campo opcional por toda la cadena y probar
canónica → API → BFF → respuesta del router. Cuando venga un idioma válido,
una prueba con detector que lance debe demostrar que no se invoca. Cubrir idioma
ausente y tipos inválidos. Sólo después medir qué fuentes realmente necesitan
rellenarlo; no reingerir ni reembeder todo el corpus por suposición.

Un recuento de cobertura de idioma en el NAS fue cancelado por el límite de
sentencia de 5 s. No se amplió el límite ni se reintentó esa consulta pesada.
Por tanto, esta revisión **no cuantifica** la cobertura canónica productiva.

### P2 — La documentación promete pureza que el detector no tiene

Archivo: `backend/services/translation_service.py:364–369`.

Se afirma «función pura», «no cambia ni una respuesta» y «el resultado de un
mismo título no cambia». Contradice el comportamiento medido y reconocido por
el propio test. En proceso local independiente de pytest, con 30 vaciados de
caché para `Sviluppatore software`: **en=21, sv=6, it=3**. La distribución exacta
no es estable ni es un oráculo de corrección lingüística.

**Corrección mínima:** documentar «reutiliza la primera respuesta por clave
mientras permanezca en la caché de este proceso». No confundir estabilidad
temporal con acierto ni garantizar equivalencia entre workers/reinicios.
La decisión sobre detección reproducible e idioma desconocido debe probarse
separadamente; una semilla por sí sola tampoco garantiza un idioma correcto.

### P2 — La carga principal no carece de criterio de experiencia de usuario

Ubicación: `docs/audits/ACTA_CIERRE_PUNTO5_2026-09-22.md:295–298`.

`MatchPage` usa precisamente la carga de 3000 como lectura ordinaria. Su
descubrimiento no convierte el recorrido en una exportación excepcional sin
presupuesto. La predeclaración ya fija lectura habitual y pantalla útil; se
puede rediseñar la carga conservando su semántica, pero no excluirla a posteriori.

**Corrección mínima:** conservar los objetivos predeclarados para la pantalla
principal y medir su recorrido real. Si se acuerda otro presupuesto, hacerlo
como cambio explícito de requisito, no como ausencia previa de requisito.
La traducción opcional sí debe permanecer medida y presupuestada aparte.

Subsiste también la frase «el mínimo ... es trabajo propio» en el acta `:25`,
contraria a su corrección posterior: retirar esa atribución residual.

## Pruebas y mordida

En el contenedor local del BFF, con su base de pruebas, una suite en serie:

```sh
docker compose exec -T backend python -m pytest \
  tests/test_language_detection_cost.py tests/test_translation_service.py \
  tests/test_resolve_language.py tests/test_matching_contract.py \
  tests/test_match.py -q
```

**100 passed en 26,23 s.** No se reejecutaron las 2515 pruebas completas.
Una invocación previa tenía el nombre incorrecto `test_translation.py`: no
recogió ni ejecutó pruebas; se corrigió el comando anterior.

Mordida semántica sin modificar el árbol: se cargaron padre y cambio desde
`git show`, se sustituyó `_resolve_language` por un contador y se hicieron cinco
llamadas con idéntico título. Afirmar una sola detección falla en el padre
(`underlying_calls=5`) y pasa en `f331c0d` (`underlying_calls=1`).

Caché fría/caliente local, 40 títulos de prueba distintos: 40 misses inicialmente
y 40 hits al repetirlos. Se midieron 0,3169 s y menos de 0,0001 s respectivamente.
No se extrapola ese tiempo al NAS ni a los títulos reales. La primera carga y
las expulsiones siguen pagando cada miss; no se vació ninguna caché productiva.

## Orden mínimo restante

1. Corregir las sondas y demostrar sus controles negativos por causa, no sólo
   que lanzan alguna excepción. Así las siguientes medidas son refutables.
2. Reparar el transporte de idioma antes de atribuir la ausencia al corpus;
   probar frontera completa con metadato válido, ausente e inválido.
3. Resolver la carga de la pantalla conservando categorías, conteos, watchlist,
   orden y estados. No reducir el límite sin preservar esas operaciones.
4. Completar la matriz fría/caliente y de extremo a extremo ya predeclarada,
   en copia para carga/reinicio/escrituras, y canario acotado en NAS después.

No se cambió código funcional ni configuración. Sin commits, push, reinicios,
traducción LLM deliberada, avisos de prueba ni datos fabricados en producción.
Los cambios ajenos existentes en el árbol se conservaron.
