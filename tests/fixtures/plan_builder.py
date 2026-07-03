"""Constructor de un PlanCycle fijo y determinista para tests de render."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from nutriplan.adapters.food.usda_importer import load_curated_foods
from nutriplan.domain.models import (
    Branding,
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealFoodPortion,
    MealSlot,
    PlanCycle,
    PlanPhase,
)

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "foods" / "curated_foods.csv"
_NS = uuid5(NAMESPACE_URL, "nutriplan/tests")

MENU = {
    MealSlot.BREAKFAST: [("huevo entero", 100), ("arepa de maíz", 70), ("aguacate", 50)],
    MealSlot.SNACK_AM: [("yogur griego natural", 170), ("banano", 120)],
    MealSlot.LUNCH: [("pechuga de pollo", 150), ("arroz blanco cocido", 120)],
    MealSlot.SNACK_PM: [("queso fresco", 60), ("manzana", 180)],
    MealSlot.DINNER: [("tilapia", 150), ("batata cocida", 60)],
}


def catalog_by_name() -> dict[str, FoodItem]:
    return {f.name_es: f for f in load_curated_foods(CSV_PATH)}


def _computed(portions: list[tuple[FoodItem, float]]) -> MacroTargets:
    def total(attr: str) -> float:
        return round(sum(getattr(f, attr) * g / 100 for f, g in portions), 1)

    return MacroTargets(
        kcal=total("kcal_100g"),
        protein_g=total("protein_100g"),
        carb_g=total("carb_100g"),
        fat_g=total("fat_100g"),
    )


def build_fixed_plan() -> tuple[PlanCycle, dict, Branding]:
    foods = catalog_by_name()
    days = []
    for day_index in range(7):
        meals = []
        day_portions: list[tuple[FoodItem, float]] = []
        for slot, items in MENU.items():
            portions = [(foods[name], grams) for name, grams in items]
            day_portions.extend(portions)
            meals.append(
                MealEntry(
                    slot=slot,
                    portions=[
                        MealFoodPortion(food_id=f.id, grams=g) for f, g in portions
                    ],
                    computed=_computed(portions),
                    free_salad=slot in (MealSlot.LUNCH, MealSlot.DINNER),
                )
            )
        days.append(DayPlan(day_index=day_index, meals=meals, totals=_computed(day_portions)))

    plan = PlanCycle(
        id=uuid5(_NS, "plan"),
        tenant_id=uuid5(_NS, "tenant"),
        client_id=uuid5(_NS, "client"),
        targets_id=uuid5(_NS, "targets"),
        phase=PlanPhase.FIRST_15,
        days=days,
        config_version="2026.07.01",
        prompt_version="plan_generation.v1",
        model="claude-sonnet-5",
        input_hash="f1x3dh45h0000",
        created_at=datetime(2026, 7, 1, 12, 0, tzinfo=UTC),
    )
    branding = Branding(
        tenant_name="Valeria Fit",
        handle="@valeria.fit",
        primary_color="#2E7D32",
        accent_color="#F9A825",
    )
    food_lookup = {f.id: f for f in foods.values()}
    return plan, food_lookup, branding
