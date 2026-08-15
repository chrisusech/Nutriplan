"""Las métricas dichas en cristiano.

Las tablas guardan identificadores (`dish_rated`, `lose_fat`,
`proteina_carbo_ensalada`) porque son estables y agregables. Quien abre la
consola no tiene por qué saberse ninguno: aquí se cambian por la frase que
describe lo que pasó.
"""

from typing import Any

from nutriplan.domain.meal_template import MealCatalog

Filas = list[dict[str, Any]]


def dish_names(catalog: MealCatalog) -> dict[str, str]:
    """Id de plantilla → el nombre del plato tal como se lee en el menú."""
    return {t.id: t.name for t in catalog.templates}


def _label(valor: Any, labels: dict[str, str]) -> Any:
    """La etiqueta si la hay; si no, el identificador tal cual.

    Devolver el crudo y no un "otro" es a propósito: un valor sin traducir se ve
    en pantalla y se traduce, en vez de esconderse detrás de una palabra vaga.
    """
    if valor in (None, ""):
        return "Sin dato"
    return labels.get(str(valor), valor)


def relabel(filas: Filas, **campos: dict[str, str]) -> Filas:
    """Cambia los identificadores de esos campos por su frase."""
    return [
        {**fila, **{c: _label(fila.get(c), etiquetas) for c, etiquetas in campos.items()}}
        for fila in filas
    ]


def with_dish_name(filas: Filas, nombres: dict[str, str]) -> Filas:
    """Añade el nombre del plato al lado de su plantilla."""
    return [{**f, "plato": _label(f.get("plantilla"), nombres)} for f in filas]
