"""Derivados del color de marca, compartidos por el PDF y la web.

Vivía duplicado: `_soft_of` en el renderer PDF y `soft_of` en el presenter web,
con la misma fórmula y dos implementaciones que podían separarse. Vive aquí (y no
en el presenter) porque un adapter no debe importar de la UI: eso invertiría la
dependencia hexagonal.
"""

_FALLBACK_SOFT = "#FBF7F4"


def _rgb(hex_color: str) -> tuple[int, int, int] | None:
    c = hex_color.lstrip("#")
    if len(c) != 6:
        return None
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except ValueError:
        return None


def mix_white(hex_color: str, amount: float) -> str:
    """Mezcla el color con blanco: amount=0 lo deja igual, amount=1 lo blanquea."""
    rgb = _rgb(hex_color)
    if rgb is None:
        return _FALLBACK_SOFT
    mixed = tuple(round(x + (255 - x) * amount) for x in rgb)
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def soft_of(hex_color: str) -> str:
    """El tinte suave del color de marca (fondos de cabecera, chips)."""
    return mix_white(hex_color, 0.9)


def tint_of(hex_color: str) -> str:
    """Un tinte intermedio, para el rayado de filas de la rejilla."""
    return mix_white(hex_color, 0.82)


# Color por macro. Vive aquí, y no en el presenter web, para que el PDF pinte los
# mismos colores que la app: antes la paleta era exclusiva de la web y el PDF
# salía en gris. El presenter le añade encima el icono de Material Symbols, que el
# PDF no puede usar (haría falta embeber otra webfont entera por cinco glifos).
MACRO_COLORS: dict[str, dict[str, str]] = {
    "kcal": {"label": "Calorías", "unit": "kcal", "color": "#F2704F", "soft": "#FCE9E3"},
    "protein_g": {"label": "Proteína", "unit": "g", "color": "#E8607A", "soft": "#FBE5EC"},
    "carb_g": {"label": "Carbos", "unit": "g", "color": "#E0A537", "soft": "#FBF0DA"},
    "fat_g": {"label": "Grasas", "unit": "g", "color": "#B08968", "soft": "#F3EBE3"},
}
