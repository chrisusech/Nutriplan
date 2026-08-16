"""María a 1450 kcal con solo proteína: el piso de grasas desbloquea el motor."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tests.fixtures.foods import catalog_foods

from nutriplan.adapters.llm.offline_engine import build_offline_engine
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.application.food_pool import supplement_pool_for_targets
from nutriplan.application.generate_plan import generate_cycle
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    FoodCategory,
    Goal,
    MacroTargets,
    MealSlot,
    NutritionTargets,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig

ROOT = Path(__file__).resolve().parents[2]


async def test_un_perfil_tipo_maria_a_1450_kcal_con_solo_proteina_genera(
    nutrition_config: NutritionConfig,
) -> None:
    catalog = catalog_foods()
    protein = [f for f in catalog if f.category is FoodCategory.PROTEIN][:12]
    slots = [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.SNACK_PM, MealSlot.DINNER]
    cfg = nutrition_config.for_slots(slots)
    daily = MacroTargets(kcal=1450, protein_g=110, carb_g=145, fat_g=48)
    allowed = supplement_pool_for_targets(protein, catalog, daily=daily, config=cfg)
    assert sum(1 for f in allowed if f.category is FoodCategory.FAT) >= 2
    assert sum(1 for f in allowed if f.category is FoodCategory.CARB) >= 2

    client = Client(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="María",
        sex=Sex.FEMALE,
        age_years=27,
        height_cm=163,
        weight_kg=54,
        activity_level=ActivityLevel.LIGHT,
        goal=Goal.LOSE_FAT,
        meal_slots=slots,
    )
    targets = NutritionTargets(
        id=uuid4(),
        tenant_id=client.tenant_id,
        client_id=client.id,
        daily=daily,
        per_meal={},
        config_version=cfg.version,
        computed_at=datetime.now(UTC),
    )
    meals = load_meal_catalog(
        ROOT / "data" / "meals" / "food_classes.yaml",
        ROOT / "data" / "meals" / "meal_templates.yaml",
    )
    cycle = await generate_cycle(
        client=client,
        targets=targets,
        allowed=allowed,
        config=cfg,
        llm=None,
        offline_engine=build_offline_engine,
        prompts_dir=ROOT / "prompts",
        model="engine-v1",
        input_hash="maria-1450-test",
        catalog=meals,
    )
    assert len(cycle.days) == 7
