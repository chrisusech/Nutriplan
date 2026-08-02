"""La receta de un plato concreto, y la clave con la que se comparte.

Dos personas a las que les tocó el mismo plato con los mismos alimentos
necesitan la misma explicación. Por eso la receta se guarda por `dish_key` y no
por menú: se escribe una vez y sirve para todo el mundo, que es lo que hace
viable una cuota gratuita de IA.
"""

import hashlib
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_STEPS = 8
MAX_STEP_CHARS = 240
LOCALE = "es-CO"


def dish_key(template_id: str | None, food_ids: list[UUID]) -> str:
    """Identidad del plato: la plantilla más sus alimentos, sin importar el orden.

    Los gramos quedan fuera a propósito. "Pollo con arroz" se prepara igual con
    150 g que con 180: meterlos en la clave partiría la caché en variantes que
    dicen exactamente lo mismo.
    """
    parts = [template_id or "", LOCALE, *sorted(str(f) for f in food_ids)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


class DishRecipe(BaseModel):
    """Cómo se prepara. Sin cifras: los gramos los pone el plan, no la receta."""

    model_config = ConfigDict(extra="forbid")

    dish_key: str
    template_id: str | None = None
    name_es: str = Field(max_length=160)
    ingredients: list[str] = Field(default_factory=list, max_length=12)
    steps: list[str] = Field(default_factory=list, max_length=MAX_STEPS)
    prep_minutes: int | None = Field(default=None, ge=1, le=180)
    difficulty: str | None = None
    tips: str | None = Field(default=None, max_length=400)
    source: str = "ai"  # yaml | ai


class GeneratedRecipe(BaseModel):
    """Lo que la IA devuelve. Deliberadamente más pequeño que `DishRecipe`:
    la identidad del plato la pone el código, no el modelo."""

    model_config = ConfigDict(extra="forbid")

    steps: list[str] = Field(min_length=1, max_length=MAX_STEPS)
    prep_minutes: int = Field(ge=1, le=180)
    difficulty: str = Field(max_length=20)
    tips: str = Field(default="", max_length=400)
