"""Formato de presentación: colores de macro y etiquetas de día.

Cómo se *dice* una porción no está aquí sino en `domain/portion_label`: esa
frase también la escribe la biblioteca de recetas, que no puede depender de
la web.
"""

DAY_LABELS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
DAY_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

MACRO_COLORS: dict[str, dict[str, str]] = {
    "kcal": {"label": "Calorías", "unit": "kcal", "color": "#F2704F", "soft": "#FCE9E3"},
    "protein_g": {"label": "Proteína", "unit": "g", "color": "#E8607A", "soft": "#FBE5EC"},
    "carb_g": {"label": "Carbos", "unit": "g", "color": "#E0A537", "soft": "#FBF0DA"},
    "fat_g": {"label": "Grasas", "unit": "g", "color": "#B08968", "soft": "#F3EBE3"},
}

_BRAND_FALLBACK = (242, 109, 91)


def mix_white(hex_color: str, amount: float) -> str:
    """Mezcla con blanco: amount=0 lo deja igual, amount=1 lo blanquea."""
    c = (hex_color or "").lstrip("#")
    try:
        rgb = tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        rgb = _BRAND_FALLBACK
    return "#" + "".join(f"{round(x + (255 - x) * amount):02X}" for x in rgb)


def soft_of(hex_color: str, mix: float = 0.87) -> str:
    """Tinte suave de la marca, para fondos de chip y cabecera."""
    return mix_white(hex_color, mix)
