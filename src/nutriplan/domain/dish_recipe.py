"""La receta de un plato concreto, y la clave con la que se comparte.

Dos personas a las que les tocó el mismo plato con los mismos alimentos
necesitan la misma explicación. Por eso la receta se guarda por `dish_key` y no
por menú: se escribe una vez y sirve para todo el mundo, que es lo que hace
viable una cuota gratuita de IA.
"""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nutriplan.domain.models import MacroTargets

MAX_STEPS = 8
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
    """Cómo se prepara, y con qué da de comer.

    Los pasos son de la IA; todo lo demás —alimentos, gramos y macros— lo pone
    el código a partir del plato que la estrenó. Por eso los macros son "de
    referencia": describen la porción con la que se resolvió, no una promesa
    para quien la lea después con otras kcal.
    """

    model_config = ConfigDict(extra="forbid")

    dish_key: str
    template_id: str | None = None
    name_es: str = Field(max_length=160)
    ingredients: list[str] = Field(default_factory=list, max_length=12)
    steps: list[str] = Field(default_factory=list, max_length=MAX_STEPS)
    prep_minutes: int | None = Field(default=None, ge=1, le=180)
    difficulty: str | None = None
    tips: str | None = Field(default=None, max_length=400)
    source: str = "ai"  # curated | yaml | ai
    food_ids: list[UUID] = Field(default_factory=list)
    reference_grams: dict[str, float] = Field(default_factory=dict)
    macros: MacroTargets | None = None
    # Calidad agregada desde `dish_ratings`, no escrita por quien genera.
    rating_avg: float | None = None
    rating_count: int = 0
    times_served: int = 0


class GeneratedRecipe(BaseModel):
    """Lo que la IA devuelve. Deliberadamente más pequeño que `DishRecipe`:
    la identidad del plato la pone el código, no el modelo.

    `adequacy` es el veredicto culinario: con `reject` el caso de uso descarta
    la receta (soft) y deja solo ingredientes en la app.

    Todos los campos van con default concreto: algunos proveedores (Groq
    estricto, Gemini) exigen cada clave en el JSON; si falta `tips` revienta.
    """

    model_config = ConfigDict(extra="forbid")

    adequacy: Literal["pass", "warn", "reject"] = "pass"
    issue_codes: list[str] = Field(default_factory=list, max_length=4)
    critique_reason: str = ""
    # Nombre de plato cocinado (técnica + sabor). El código puede preferirlo
    # frente al generico del motor.
    name_es: str = Field(default="", max_length=80)
    steps: list[str] = Field(min_length=1, max_length=MAX_STEPS)
    prep_minutes: int = Field(default=10, ge=1, le=180)
    difficulty: str = "fácil"
    tips: str = ""
