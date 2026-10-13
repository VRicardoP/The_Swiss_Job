"""ADR-08: el paquete NO importa internals del backend legacy (chequeo estático)."""

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]

# Módulos top-level del backend legacy (viven en backend/, fuera de este paquete).
# Lista ampliada (auditoría Opus): también utils/schemas/main/crud/api/scripts.
FORBIDDEN = re.compile(
    r"^\s*(?:from|import)\s+"
    r"(?:services|models|tasks|routers|config|database|celery_app|core|providers"
    r"|scrapers|utils|schemas|main|crud|api|scripts|logging_config|conftest)\b"
)


def test_no_legacy_imports():
    offenders = []
    for py in PKG.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if FORBIDDEN.match(line):
                offenders.append(f"{py.relative_to(PKG)}:{i}: {line.strip()}")
    assert not offenders, "Imports legacy prohibidos:\n" + "\n".join(offenders)


def test_la_regla_de_almacenabilidad_vive_en_un_solo_sitio():
    """G8-P3-2/G8-P3-3, el cierre COMO CLASE. «Carácter que Postgres no puede
    almacenar» va por su sexta aparición y las cinco anteriores se cerraron
    caso a caso, cada una en el sitio donde reventó; la quinta añadió por fin
    un guard compartido y aun así dejó fuera un vector, porque la PRUEBA
    seguía escrita a mano dentro de él.

    La regla es ahora una sola función, `deps.ensure_text_storable`, y este
    chequeo estático impide que vuelva a repartirse: ningún otro módulo de
    `api/` puede llevar su propio literal tóxico. Se mira el AST y NO el
    texto, para que una docstring que EXPLIQUE la clase (que es justo lo que
    se quiere fomentar) no cuente como reparto. Los caminos de IMPORT quedan
    fuera a propósito: allí la respuesta correcta es la contraria —coerción,
    porque una migración no puede perder una fila por un byte (G1-P2-1)— y
    por eso el límite es el paquete `api/`, no el core entero."""
    import ast

    def _toxico(valor: str) -> bool:
        return "\x00" in valor or any(0xD800 <= ord(c) <= 0xDFFF for c in valor)

    fuera = []
    for py in sorted((PKG / "api").glob("*.py")):
        if py.name == "deps.py":
            continue
        arbol = ast.parse(py.read_text(encoding="utf-8"))
        docs = {
            id(n.body[0].value)
            for n in ast.walk(arbol)
            if isinstance(
                n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and n.body
            and isinstance(n.body[0], ast.Expr)
            and isinstance(n.body[0].value, ast.Constant)
        }
        for nodo in ast.walk(arbol):
            if (
                isinstance(nodo, ast.Constant)
                and isinstance(nodo.value, str)
                and id(nodo) not in docs
                and _toxico(nodo.value)
            ):
                fuera.append(f"{py.name}:{nodo.lineno}: {nodo.value!r}")
    assert not fuera, (
        "la prueba de almacenabilidad se ha vuelto a repartir; el sitio es "
        "deps.ensure_text_storable:\n" + "\n".join(fuera)
    )
