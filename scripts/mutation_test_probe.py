"""Prueba de mutacion de la sonda de aceptacion.

Existe porque los controles negativos de `probe_served_endpoints.py` ya
mintieron una vez: cinco casos que fallaban TODOS en la primera guarda, antes de
llegar a la que decian probar, y `self_test()` seguia verde tras desactivar las
guardas de cardinalidad y de total.

Esto lo hace refutable: desactiva cada guarda una a una y exige que `self_test()`
deje de pasar. Un SUPERVIVIENTE es una guarda que ningun control ejercita.

    python3 scripts/mutation_test_probe.py    # desde la raiz del repo

Sin red, sin base de datos y sin ejecutar el `main()` de la sonda.
"""

import ast
import contextlib
import io
import json
from pathlib import Path

SRC = Path("scripts/probe_served_endpoints.py").read_text()
NEEDED = {
    "ProbeFailure",
    "_page_items",
    "_identities",
    "check_catalog",
    "check_matches",
    "fingerprint",
    "_roto",
    "self_test",
}


def recortar():
    t = ast.parse(SRC)
    t.body = [
        n
        for n in t.body
        if (isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in NEEDED)
        or (
            isinstance(n, ast.Assign)
            and any(getattr(x, "id", "").startswith("_") for x in n.targets)
        )
    ]
    return t


def guardas(tree):
    """Todo `if` que en su cuerpo lanza ProbeFailure."""
    fuera = []
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and any(
                isinstance(s, ast.Raise) and "ProbeFailure" in ast.dump(s)
                for s in n.body
            ):
                fuera.append((fn.name, n.lineno))
    return fuera


base = recortar()
objetivos = guardas(base)
print(f"guardas encontradas: {len(objetivos)}")

# 1. sin mutar, self_test pasa
ns = {"json": json}
exec(compile(base, "sonda", "exec"), ns)
with contextlib.redirect_stdout(io.StringIO()) as out:
    ns["self_test"]()
print("sin mutar:", out.getvalue().strip())

# 2. cada guarda desactivada debe romper self_test
supervivientes = []
for nombre, linea in objetivos:
    t = recortar()
    for fn in t.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name != nombre:
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.If) and n.lineno == linea:
                n.test = ast.Constant(False)
    ast.fix_missing_locations(t)
    ns2 = {"json": json}
    exec(compile(t, "mutante", "exec"), ns2)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            ns2["self_test"]()
    except BaseException:
        continue  # la mutacion rompio su control: correcto
    supervivientes.append(f"{nombre}:{linea}")

print(
    json.dumps(
        {
            "guardas": len(objetivos),
            "mutantes_detectados": len(objetivos) - len(supervivientes),
            "supervivientes": supervivientes,
        },
        indent=1,
    )
)
