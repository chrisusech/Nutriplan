"""Cómo se dice una porción en español: «3 huevos enteros (150 g)».

No es presentación: la receta que se guarda en la biblioteca lleva estos mismos
ingredientes escritos, y la app los muestra tal cual. Vivía en la capa web, y
eso obligaba a `application` a importar la UI para escribir una receta — la
flecha justo al revés de la que manda.

Qué unidades tiene un alimento (`unit_granularity`, `unit_name`,
`default_unit_g`) es vocabulario del dominio, así que la frase que sale de ahí
también.
"""

from nutriplan.domain.models import FoodItem, UnitGranularity


def _fmt_count(n: float) -> str:
    """1 → '1', 0.5 → '½', 1.5 → '1½'."""
    whole, frac = divmod(round(n * 2), 2)
    half = "½" if frac else ""
    if whole == 0:
        return half or "0"
    return f"{whole}{half}"


def _plural(name: str, n: float) -> str:
    if n <= 1:
        return name
    return name + ("s" if name[-1:] in "aeiou" else "es")


def _plural_name(name: str, n: float) -> str:
    """Pluraliza un nombre compuesto manteniéndolo legible.

    La palabra cabeza siempre concuerda ('huevo de codorniz' → 'huevos de
    codorniz'). Sin 'de' es sustantivo+adjetivo ('huevo entero' → 'huevos
    enteros'), así que concuerda también la última; los tipos intermedios
    ('pan PITA integral') se quedan invariables.
    """
    if n <= 1:
        return name
    words = name.split()
    words[0] = _plural(words[0], n)
    if len(words) > 1 and " de " not in name:
        words[-1] = _plural(words[-1], n)
    return " ".join(words)


def natural_units(grams: float, food: FoodItem) -> str | None:
    """Etiqueta de unidades para alimentos contables ('3 huevos', '1 lata').

    Solo para `whole`/`half`; sus gramos ya vienen cuantizados por el solver, así
    que el conteo es exacto — nada de '≈ 5.5 und'. None en gramos libres.
    """
    if food.unit_granularity is UnitGranularity.GRAMS or not food.default_unit_g:
        return None
    n = grams / food.default_unit_g
    name = food.unit_name or "unidad"
    return f"{_fmt_count(n)} {_plural(name, n)}"


def portion_text(grams: float, food: FoodItem) -> str:
    """Texto de una porción, con la unidad natural al frente si aplica."""
    g = int(grams) if float(grams).is_integer() else round(grams, 1)
    label = natural_units(grams, food)
    if label is None:
        return f"{food.name_es.capitalize()} — {g} g"
    if food.unit_name and food.unit_name in food.name_es.lower():
        # El nombre suele traer más que la unidad ('huevo DE CODORNIZ'): se
        # muestra completo para no confundir un alimento con otro.
        n = grams / (food.default_unit_g or 1.0)
        head = f"{_fmt_count(n)} {_plural_name(food.name_es, n)}"
    else:
        head = f"{label} de {food.name_es}"
    return f"{head[:1].upper()}{head[1:]} ({g} g)"
