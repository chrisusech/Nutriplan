"""Si el pool liked no alcanza un slot, se completa con el mínimo del universo."""

from uuid import uuid4

from nutriplan.application.food_pool import supplement_pool_for_targets
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig


def _food(
    name: str,
    *,
    category: FoodCategory,
    carb: float,
    protein: float = 5,
    fat: float = 1,
    slots: list[MealSlot],
    granularity: UnitGranularity = UnitGranularity.GRAMS,
    max_g: float | None = None,
    tags: list[str] | None = None,
) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        name_es=name,
        category=category,
        kcal_100g=carb * 4 + protein * 4 + fat * 9,
        protein_100g=protein,
        carb_100g=carb,
        fat_100g=fat,
        source="test",
        unit_granularity=granularity,
        portion_max_g=max_g,
        meal_slots=slots,
        tags=tags or [],
    )


def test_si_solo_hay_arepa_se_anade_avena_para_desayuno_alto(
    nutrition_config: NutritionConfig,
) -> None:
    """Arepa topeada no cubre 128 g de carbo; el universo sí (avena)."""
    cfg = nutrition_config.for_slots([MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER])
    arepa = _food(
        "arepa de maíz",
        category=FoodCategory.CARB,
        carb=46,
        slots=[MealSlot.BREAKFAST],
        granularity=UnitGranularity.HALF,
        max_g=140,
    )
    avena = _food(
        "avena en hojuelas",
        category=FoodCategory.CARB,
        carb=60,
        slots=[MealSlot.BREAKFAST],
    )
    huevo = _food(
        "huevo entero",
        category=FoodCategory.PROTEIN,
        carb=1,
        protein=13,
        slots=[MealSlot.BREAKFAST, MealSlot.DINNER],
    )
    pollo = _food(
        "pechuga de pollo",
        category=FoodCategory.PROTEIN,
        carb=0,
        protein=31,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    arroz = _food(
        "arroz blanco cocido",
        category=FoodCategory.CARB,
        carb=28,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    daily = MacroTargets(kcal=2900, protein_g=140, carb_g=428, fat_g=72)
    out = supplement_pool_for_targets(
        [arepa, huevo, pollo, arroz],
        [arepa, huevo, pollo, arroz, avena],
        daily=daily,
        config=cfg,
    )
    assert "avena en hojuelas" in {f.name_es for f in out}


def test_con_un_solo_carbo_de_almuerzo_se_anade_otro_para_no_repetirlo_siete_dias(
    nutrition_config: NutritionConfig,
) -> None:
    """Una sola opción viable no es una semana, es el mismo plato siete veces.

    Le pasó a un cliente de 2.900 kcal: su almuerzo pedía 128 g de carbo y de su
    lista solo el arroz llegaba —la papa se topea en 100 g—, así que el motor
    tenía que elegir entre repetir arroz o servir platos que la validación tumba.
    """
    # Las cinco comidas: el almuerzo pide 128 g de carbo (el 30% de 428).
    cfg = nutrition_config
    arroz = _food(
        "arroz blanco cocido", category=FoodCategory.CARB, carb=28, slots=[MealSlot.LUNCH]
    )
    papa = _food("papa cocida", category=FoodCategory.CARB, carb=20, slots=[MealSlot.LUNCH])
    pasta = _food("pasta cocida", category=FoodCategory.CARB, carb=31, slots=[MealSlot.LUNCH])
    pollo = _food(
        "pechuga de pollo",
        category=FoodCategory.PROTEIN,
        carb=0,
        protein=31,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    avena = _food(
        "avena en hojuelas",
        category=FoodCategory.CARB,
        carb=60,
        slots=[MealSlot.BREAKFAST, MealSlot.DINNER],
    )
    daily = MacroTargets(kcal=2900, protein_g=140, carb_g=428, fat_g=72)
    out = supplement_pool_for_targets(
        [arroz, papa, pollo, avena],
        [arroz, papa, pasta, pollo, avena],
        daily=daily,
        config=cfg,
    )
    assert "pasta cocida" in {f.name_es for f in out}


def test_no_suplementa_miel_como_parche_de_carbo(
    nutrition_config: NutritionConfig,
) -> None:
    cfg = nutrition_config.for_slots([MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER])
    arepa = _food(
        "arepa",
        category=FoodCategory.CARB,
        carb=46,
        slots=[MealSlot.BREAKFAST],
        granularity=UnitGranularity.HALF,
        max_g=140,
    )
    miel = _food(
        "miel",
        category=FoodCategory.CARB,
        carb=82,
        slots=[MealSlot.BREAKFAST],
    )
    avena = _food(
        "avena en hojuelas",
        category=FoodCategory.CARB,
        carb=60,
        slots=[MealSlot.BREAKFAST],
    )
    pollo = _food(
        "pechuga",
        category=FoodCategory.PROTEIN,
        carb=0,
        protein=31,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    arroz = _food(
        "arroz",
        category=FoodCategory.CARB,
        carb=28,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    daily = MacroTargets(kcal=2900, protein_g=140, carb_g=428, fat_g=72)
    out = supplement_pool_for_targets(
        [arepa, pollo, arroz],
        [arepa, miel, avena, pollo, arroz],
        daily=daily,
        config=cfg,
    )
    names = {f.name_es for f in out}
    assert "avena en hojuelas" in names
    assert "miel" not in names


def test_si_solo_hay_proteina_se_anaden_dos_grasas_y_dos_carbos(
    nutrition_config: NutritionConfig,
) -> None:
    """María: 22 carnes y nada más. Sin aceite el solver se queda corto de grasa."""
    cfg = nutrition_config.for_slots(
        [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.SNACK_PM, MealSlot.DINNER]
    )
    pollo = _food(
        "pechuga de pollo",
        category=FoodCategory.PROTEIN,
        carb=0,
        protein=31,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    aceite = _food(
        "aceite de oliva",
        category=FoodCategory.FAT,
        carb=0,
        protein=0,
        fat=100,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    aguacate = _food(
        "aguacate",
        category=FoodCategory.FAT,
        carb=9,
        protein=2,
        fat=15,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    avena = _food(
        "avena en hojuelas",
        category=FoodCategory.CARB,
        carb=60,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH],
    )
    arroz = _food(
        "arroz blanco cocido",
        category=FoodCategory.CARB,
        carb=28,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    yogur = _food(
        "yogur griego",
        category=FoodCategory.DAIRY,
        carb=4,
        protein=10,
        slots=[MealSlot.SNACK_PM, MealSlot.BREAKFAST],
    )
    banano = _food(
        "banano",
        category=FoodCategory.FRUIT,
        carb=23,
        protein=1,
        slots=[MealSlot.SNACK_PM, MealSlot.BREAKFAST],
    )
    daily = MacroTargets(kcal=1450, protein_g=110, carb_g=145, fat_g=48)
    out = supplement_pool_for_targets(
        [pollo],
        [pollo, aceite, aguacate, avena, arroz, yogur, banano],
        daily=daily,
        config=cfg,
    )
    names = {f.name_es for f in out}
    grasas = [f for f in out if f.category is FoodCategory.FAT]
    carbos = [f for f in out if f.category is FoodCategory.CARB]
    assert len(grasas) >= 2
    assert len(carbos) >= 2
    assert "banano" in names
    assert "yogur griego" not in names


def test_catalina_sin_gluten_no_recibe_pasta_ni_crackers(
    nutrition_config: NutritionConfig,
) -> None:
    cfg = nutrition_config.for_slots(
        [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.SNACK_PM, MealSlot.DINNER]
    )
    pollo = _food(
        "pechuga de pollo",
        category=FoodCategory.PROTEIN,
        carb=0,
        protein=31,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    pasta = _food(
        "pasta cocida",
        category=FoodCategory.CARB,
        carb=31,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
        tags=["gluten"],
    )
    avena = _food(
        "avena en hojuelas",
        category=FoodCategory.CARB,
        carb=60,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH],
    )
    yuca = _food(
        "yuca cocida",
        category=FoodCategory.CARB,
        carb=38,
        slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    aceite = _food(
        "aceite de oliva",
        category=FoodCategory.FAT,
        carb=0,
        protein=0,
        fat=100,
        slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    daily = MacroTargets(kcal=1450, protein_g=110, carb_g=145, fat_g=48)
    out = supplement_pool_for_targets(
        [pollo],
        [pollo, pasta, avena, yuca, aceite],
        daily=daily,
        config=cfg,
        restrictions=["no_gluten"],
    )
    names = {f.name_es for f in out}
    assert "pasta cocida" not in names
    assert "avena en hojuelas" in names or "yuca cocida" in names
