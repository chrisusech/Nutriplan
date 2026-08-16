"""Fila curada lista para `foods`, y la auditoría que la deja entrar o no."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, Field

from nutriplan.domain.models import (
    CookingMethod,
    FoodCategory,
    FoodItem,
    FoodState,
    MealSlot,
    UnitGranularity,
)

# El id sale de fdc_id+estado, no del nombre: renombrar no debe cambiar la identidad.
_NAMESPACE = uuid5(NAMESPACE_URL, "nutriplan/foods/usda")

# 18 % + 8 kcal de holgura: USDA redondea y usa factores propios; apretar más
# rechaza verduras buenas (espinaca: 2 kcal de redondeo ya son ~9 %).
_ATWATER_TOLERANCE = 0.18
_ATWATER_SLACK_KCAL = 8.0

# La fibra de USDA va dentro del carbo pero no aporta 4 kcal/g.
_KCAL_PER_G = {"protein": 4.0, "net_carb": 4.0, "fiber": 2.0, "fat": 9.0}

_SPANISH_HINTS = frozenset(
    "áéíóúñüaeiou",
)
_ENGLISH_GIVEAWAYS = (
    " with ",
    " without ",
    " and ",
    "cooked",
    "boiled",
    "roasted",
    " raw",
    "unprepared",
    "nfs",
)


class CatalogEntry(BaseModel):
    """Una fila del catálogo curado, tal como se guarda en el JSONL."""

    fdc_id: int | None = None
    # "derived" = dos filas comparten fdc_id a propósito (leche deslactosada).
    source: str = "USDA"
    name_es: str
    name_en: str | None = None
    category: FoodCategory
    kcal_100g: float = Field(ge=0)
    protein_100g: float = Field(ge=0)
    carb_100g: float = Field(ge=0)
    fat_100g: float = Field(ge=0)
    fiber_100g: float = Field(default=0.0, ge=0)
    sugar_100g: float = Field(default=0.0, ge=0)
    sodium_mg_100g: float = Field(default=0.0, ge=0)
    state: FoodState = FoodState.NOT_APPLICABLE
    cooking_method: CookingMethod | None = None
    yield_factor: float | None = None
    tags: list[str] = []
    aliases: list[str] = []
    meal_slots: list[MealSlot] = []
    slot_weights: dict[MealSlot, int] = {}
    default_unit_g: float | None = None
    unit_granularity: UnitGranularity = UnitGranularity.GRAMS
    unit_name: str | None = None
    portion_step_g: float | None = None
    portion_min_g: float | None = None
    portion_max_g: float | None = None
    engine_default: bool = False
    pair_key: str = ""
    is_free: bool = False
    free_text: str | None = None

    # Conserva el uuid5(name_es) de las filas que ya tienen FKs en la base.
    id_override: UUID | None = None

    @property
    def id(self) -> UUID:
        if self.id_override is not None:
            return self.id_override
        return entry_id(self.fdc_id, self.state, self.name_es)


def entry_id(fdc_id: int | None, state: FoodState, name_es: str) -> UUID:
    """Id estable. Ancla en USDA si la hay; si no, en el nombre."""
    seed = f"{fdc_id}|{state.value}" if fdc_id else f"manual|{name_es.strip().lower()}"
    return uuid5(_NAMESPACE, seed)


def to_food_item(entry: CatalogEntry) -> FoodItem:
    # None deja que FoodItem derive el paso de la granularidad.
    step: dict[str, Any] = (
        {} if entry.portion_step_g is None else {"portion_step_g": entry.portion_step_g}
    )
    return FoodItem(
        id=entry.id,
        tenant_id=None,
        source=entry.source,
        source_ref=str(entry.fdc_id) if entry.fdc_id else None,
        fdc_id=entry.fdc_id,
        name_es=entry.name_es,
        name_en=entry.name_en,
        category=entry.category,
        kcal_100g=entry.kcal_100g,
        protein_100g=entry.protein_100g,
        carb_100g=entry.carb_100g,
        fat_100g=entry.fat_100g,
        fiber_100g=entry.fiber_100g,
        tags=list(entry.tags),
        aliases=list(entry.aliases),
        default_unit_g=entry.default_unit_g,
        unit_granularity=entry.unit_granularity,
        unit_name=entry.unit_name,
        portion_min_g=entry.portion_min_g,
        portion_max_g=entry.portion_max_g,
        meal_slots=list(entry.meal_slots),
        slot_weights=dict(entry.slot_weights),
        is_free=entry.is_free,
        free_text=entry.free_text,
        state=entry.state,
        cooking_method=entry.cooking_method,
        yield_factor=entry.yield_factor,
        engine_default=entry.engine_default,
        **step,
    )


def write_jsonl(path: Path, entries: Iterable[CatalogEntry]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(entry.model_dump_json(exclude_none=False) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> Iterator[CatalogEntry]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield CatalogEntry.model_validate(json.loads(line))


def atwater_kcal(entry: CatalogEntry) -> float:
    """Las kcal que dan los macros, con la fibra contada aparte."""
    fiber = min(entry.fiber_100g, entry.carb_100g)
    net_carb = entry.carb_100g - fiber
    return (
        entry.protein_100g * _KCAL_PER_G["protein"]
        + net_carb * _KCAL_PER_G["net_carb"]
        + fiber * _KCAL_PER_G["fiber"]
        + entry.fat_100g * _KCAL_PER_G["fat"]
    )


def atwater_gap(entry: CatalogEntry) -> float:
    """Desvío relativo; 0 si cabe en el margen absoluto de redondeo."""
    diff = abs(atwater_kcal(entry) - entry.kcal_100g)
    if diff <= _ATWATER_SLACK_KCAL:
        return 0.0
    return diff / max(entry.kcal_100g, 1.0)


def looks_spanish(name: str) -> bool:
    lowered = name.lower()
    if any(giveaway in lowered for giveaway in _ENGLISH_GIVEAWAYS):
        return False
    return bool(set(lowered) & _SPANISH_HINTS)


def validate_entries(
    entries: Iterable[CatalogEntry],
) -> tuple[list[CatalogEntry], list[tuple[CatalogEntry, str]]]:
    """Separa lo apto de lo que va a cuarentena. No corrige filas dudosas."""
    ok: list[CatalogEntry] = []
    rejected: list[tuple[CatalogEntry, str]] = []
    seen_names: dict[str, int] = {}
    seen_ids: set[UUID] = set()

    for entry in sorted(entries, key=_preference):
        problem = _problem_with(entry, seen_names, seen_ids)
        if problem is None:
            seen_names[entry.name_es.strip().lower()] = 1
            seen_ids.add(entry.id)
            ok.append(entry)
        else:
            rejected.append((entry, problem))
    return ok, rejected


# Qué estado se prefiere cuando varias filas comparten nombre. El plan sirve
# gramos COCIDOS, así que la fila cocida es la que encaja en un plato; si además
# trae factor de rendimiento sabe decir cuánto comprar. El crudo va último:
# 150 g de arroz crudo no es una porción de nadie.
_ORDEN_ESTADO: dict[tuple[FoodState, bool], int] = {
    (FoodState.COOKED, True): 0,
    (FoodState.NOT_APPLICABLE, False): 1,
    (FoodState.NOT_APPLICABLE, True): 1,
    (FoodState.COOKED, False): 2,
    (FoodState.RAW, False): 3,
    (FoodState.RAW, True): 3,
}


def _preference(entry: CatalogEntry) -> tuple[int, int, int, int]:
    """Quién se queda con el nombre cuando varias filas piden el mismo.

    USDA tiene 859 filas de «beef»: la misma vaca despiezada de catorce maneras,
    y casi todas acaban con el mismo nombre de cocina. Gana la que una persona
    reconocería: del núcleo si la hay, servible antes que cruda, y entre iguales
    la de descripción MÁS CORTA — en USDA la longitud es especificidad, así que
    la corta es la genérica, que es la que la gente quiere decir.
    """
    return (
        0 if entry.engine_default else 1,
        _ORDEN_ESTADO[(entry.state, bool(entry.yield_factor))],
        len(entry.name_en or ""),
        entry.fdc_id or 0,
    )


def _problem_with(
    entry: CatalogEntry, seen_names: dict[str, int], seen_ids: set[UUID]
) -> str | None:
    name = entry.name_es.strip()
    if not name:
        return "sin nombre"
    if name.lower() in seen_names:
        return f"nombre repetido: {name}"
    if entry.id in seen_ids:
        return "id repetido"
    if not looks_spanish(name):
        return f"el nombre no parece español: {name}"
    if entry.is_free:
        return None if entry.free_text else "alimento libre sin texto para el plan"
    if entry.fiber_100g > entry.carb_100g + 0.5:
        return "más fibra que carbohidrato"
    if atwater_gap(entry) > _ATWATER_TOLERANCE:
        return f"los macros no cuadran con las kcal (desvío {atwater_gap(entry):.0%})"
    if entry.yield_factor is not None and not 0.3 <= entry.yield_factor <= 6.0:
        return f"factor de rendimiento imposible: {entry.yield_factor}"
    if entry.state is not FoodState.COOKED and entry.yield_factor is not None:
        return "solo un alimento cocido puede tener factor de rendimiento"
    if entry.default_unit_g is not None and not 1.0 <= entry.default_unit_g <= 500.0:
        return f"porción natural absurda: {entry.default_unit_g} g"
    if entry.unit_granularity is not UnitGranularity.GRAMS and not entry.default_unit_g:
        return "se cuenta por unidades pero no dice cuánto pesa una"
    if not entry.meal_slots:
        return "no encaja en ninguna comida"
    return None
