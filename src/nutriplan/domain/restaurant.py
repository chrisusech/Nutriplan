"""Catálogo de platos de calle: macros fijos, kcal = 4P+4C+9G.

No es una receta de casa. Quien come fuera elige un plato de esta tabla y el
código recuadra el resto del día contra lo que queda del presupuesto.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nutriplan.domain.calculation import energy_kcal
from nutriplan.domain.models import MacroTargets, MealEntry

OUT_PREFIX = "out:"


class RestaurantDish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    portion: str = Field(default="1 porción", max_length=80)
    protein_g: float = Field(ge=0, le=200)
    carb_g: float = Field(ge=0, le=300)
    fat_g: float = Field(ge=0, le=150)

    @property
    def kcal(self) -> float:
        return energy_kcal(self.protein_g, self.carb_g, self.fat_g)

    def macros(self, servings: float = 1.0) -> MacroTargets:
        n = max(servings, 0.0)
        p, c, g = self.protein_g * n, self.carb_g * n, self.fat_g * n
        return MacroTargets(
            kcal=energy_kcal(p, c, g),
            protein_g=round(p, 1),
            carb_g=round(c, 1),
            fat_g=round(g, 1),
        )


class Restaurant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=24)
    name: str = Field(min_length=1, max_length=80)
    dishes: list[RestaurantDish] = Field(default_factory=list)

    def dish(self, dish_id: str) -> RestaurantDish | None:
        return next((d for d in self.dishes if d.id == dish_id), None)


class RestaurantCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "0.0.0"
    restaurants: list[Restaurant] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> RestaurantCatalog:
        seen: set[str] = set()
        for resto in self.restaurants:
            if resto.id in seen:
                raise ValueError(f"restaurante duplicado: {resto.id}")
            seen.add(resto.id)
            dishes: set[str] = set()
            for dish in resto.dishes:
                if dish.id in dishes:
                    raise ValueError(f"plato duplicado en {resto.id}: {dish.id}")
                dishes.add(dish.id)
        return self

    def restaurant(self, restaurant_id: str) -> Restaurant | None:
        return next((r for r in self.restaurants if r.id == restaurant_id), None)

    def find(self, restaurant_id: str, dish_id: str) -> tuple[Restaurant, RestaurantDish] | None:
        resto = self.restaurant(restaurant_id)
        if resto is None:
            return None
        dish = resto.dish(dish_id)
        if dish is None:
            return None
        return resto, dish

    def search(self, query: str, *, limit: int = 40) -> list[tuple[Restaurant, RestaurantDish]]:
        needle = query.strip().lower()
        hits: list[tuple[Restaurant, RestaurantDish]] = []
        for resto in self.restaurants:
            for dish in resto.dishes:
                hay = f"{resto.name} {dish.name}".lower()
                if not needle or needle in hay:
                    hits.append((resto, dish))
                if len(hits) >= limit:
                    return hits
        return hits


def out_template_id(restaurant_id: str, dish_id: str) -> str:
    return f"{OUT_PREFIX}{restaurant_id}.{dish_id}"[:60]


def parse_out_template(template_id: str | None) -> tuple[str, str] | None:
    if not template_id or not template_id.startswith(OUT_PREFIX):
        return None
    body = template_id[len(OUT_PREFIX) :]
    if "." not in body:
        return None
    resto, dish = body.split(".", 1)
    return resto, dish


def is_eating_out(meal: MealEntry) -> bool:
    return parse_out_template(meal.template_id) is not None


__all__ = [
    "OUT_PREFIX",
    "Restaurant",
    "RestaurantCatalog",
    "RestaurantDish",
    "is_eating_out",
    "out_template_id",
    "parse_out_template",
]
