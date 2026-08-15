"""El núcleo no sabe qué hay afuera.

`domain` y `application` no pueden importar `adapters` ni `ui`. Es la regla que
permite probar el cálculo sin base de datos y cambiar de proveedor de IA sin
tocar una fórmula — y se rompe sola, un import a la vez, si nadie la vigila.

Cuando este test se ponga rojo la salida no es añadir una excepción: es sacar
lo puro al núcleo (como `portion_label`) o poner un puerto (como
`OfflineEngineFactory`).
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "nutriplan"
PROHIBIDO = ("nutriplan.adapters", "nutriplan.ui")


def _imports(archivo: Path) -> list[tuple[int, str]]:
    arbol = ast.parse(archivo.read_text(encoding="utf-8"))
    encontrados: list[tuple[int, str]] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and nodo.module:
            encontrados.append((nodo.lineno, nodo.module))
        elif isinstance(nodo, ast.Import):
            encontrados.extend((nodo.lineno, alias.name) for alias in nodo.names)
    return encontrados


@pytest.mark.parametrize("capa", ["domain", "application", "ports"])
def test_el_nucleo_no_importa_ni_adaptadores_ni_pantallas(capa: str) -> None:
    culpables = [
        f"{archivo.relative_to(SRC)}:{linea} → {modulo}"
        for archivo in sorted((SRC / capa).rglob("*.py"))
        for linea, modulo in _imports(archivo)
        if modulo.startswith(PROHIBIDO)
    ]
    assert not culpables, "La flecha apunta al revés en: " + ", ".join(culpables)
