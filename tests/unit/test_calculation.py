"""Golden values (calculados a mano) para el motor de cálculo."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from nutriplan.domain.calculation import (
    apply_overrides,
    bmr_mifflin_st_jeor,
    compute_targets,
)
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MacroFormula,
    MacroTargets,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig


def make_client(**overrides) -> Client:
    base = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Golden",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )
    base.update(overrides)
    return Client(**base)


def test_golden_female_lose_fat(nutrition_config) -> None:
    # BMR  = 10*62 + 6.25*165 - 5*30 - 161 = 1340.25
    # TDEE = 1340.25 * 1.55 = 2077.3875 ; déficit = * 0.82 = 1703.46
    # piso = max(BMR 1340.25, mínimo mujer 1450) = 1450  →  manda el déficit
    # prot = 1.6 * 62 =  99.2 → 396.8 kcal
    # fat  = 0.8 * 62 =  49.6 → 446.4 kcal   (el 28% de kcal habría dado 53.0)
    # carb = (1703.46 - 396.8 - 446.4) / 4 = 215.1
    targets = compute_targets(make_client(), nutrition_config)
    assert targets.daily.kcal == pytest.approx(1703.5, abs=0.1)
    assert targets.daily.protein_g == pytest.approx(99.2, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(49.6, abs=0.1)
    assert targets.daily.carb_g == pytest.approx(215.1, abs=0.1)
    assert targets.daily.fiber_g == pytest.approx(23.8, abs=0.1)  # 1703.46/1000 * 14
    assert targets.config_version == nutrition_config.version


def test_golden_male_gain_muscle(nutrition_config) -> None:
    # BMR  = 10*80 + 6.25*180 - 5*25 + 5 = 1805 ; TDEE = 1805*1.725 = 3113.625
    # superávit = * 1.10 = 3424.99 ; piso = max(1805, 1700) = 1805 → manda el superávit
    # prot = 1.6 * 80 = 128.0 → 512 kcal
    # fat  = 0.8 * 80 =  64.0 → 576 kcal
    # carb = (3424.99 - 512 - 576) / 4 = 584.2
    client = make_client(
        sex=Sex.MALE,
        weight_kg=80.0,
        height_cm=180.0,
        age_years=25,
        goal=Goal.GAIN_MUSCLE,
        activity_level=ActivityLevel.ACTIVE,
    )
    targets = compute_targets(client, nutrition_config)
    assert targets.daily.kcal == pytest.approx(3425.0, abs=0.1)
    assert targets.daily.protein_g == pytest.approx(128.0, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(64.0, abs=0.1)
    assert targets.daily.carb_g == pytest.approx(584.2, abs=0.1)


def test_golden_female_maintain(nutrition_config) -> None:
    # BMR  = 700 + 1062.5 - 200 - 161 = 1401.5 ; TDEE = kcal = 1927.06 (mantener)
    # piso = max(1401.5, 1450) = 1450 → manda el TDEE
    # prot = 1.6 * 70 = 112.0 → 448 kcal
    # fat  = 0.8 * 70 =  56.0 → 504 kcal
    # carb = (1927.06 - 448 - 504) / 4 = 243.8
    client = make_client(
        weight_kg=70.0,
        height_cm=170.0,
        age_years=40,
        goal=Goal.MAINTAIN,
        activity_level=ActivityLevel.LIGHT,
    )
    targets = compute_targets(client, nutrition_config)
    assert targets.daily.kcal == pytest.approx(1927.1, abs=0.1)
    assert targets.daily.protein_g == pytest.approx(112.0, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(56.0, abs=0.1)
    assert targets.daily.carb_g == pytest.approx(243.8, abs=0.1)


def test_kcal_floor_catches_the_sedentary_deficit(nutrition_config) -> None:
    """Un % de déficit sobre un TDEE sedentario bajo se hunde solo.

    Mujer de 70 kg sedentaria: el -18% la dejaba en 1398 kcal, por debajo de su
    propio BMR (1420) y del mínimo de la guía (1450). Ahora manda el piso.
    """
    client = make_client(weight_kg=70.0, activity_level=ActivityLevel.SEDENTARY)
    targets = compute_targets(client, nutrition_config)
    assert targets.daily.kcal == pytest.approx(1450.0, abs=0.1)  # el piso, no 1398
    assert targets.daily.carb_g == pytest.approx(124.5, abs=0.1)


def test_never_below_bmr_even_when_the_floor_is_lower(nutrition_config) -> None:
    """Mujer de 80 kg: su BMR (1520) supera el mínimo por sexo (1450) y manda él."""
    client = make_client(weight_kg=80.0, activity_level=ActivityLevel.SEDENTARY)
    targets = compute_targets(client, nutrition_config)
    bmr = bmr_mifflin_st_jeor(Sex.FEMALE, 80.0, 165.0, 30)
    assert bmr == pytest.approx(1520.25, abs=0.1)
    assert targets.daily.kcal == pytest.approx(bmr, abs=0.1)  # ni un kcal menos


def test_manual_kcal_below_the_floor_is_rejected(nutrition_config) -> None:
    """El piso no se negocia, ni escribiendo el número a mano."""
    formula = MacroFormula(kcal_override=1200.0)
    with pytest.raises(CalculationError, match="no es sostenible"):
        compute_targets(make_client(), nutrition_config, formula=formula)


def test_fat_scales_with_body_weight_not_with_the_deficit(nutrition_config) -> None:
    """El g/kg de grasa no depende de las kcal — que es el punto de usarlo.

    Con el método del % de kcal, la misma clienta pasaba de 0.85 g/kg (mantener)
    a 0.62 g/kg si le apretabas el déficit. Aquí no se mueve.
    """
    low = compute_targets(make_client(activity_level=ActivityLevel.SEDENTARY), nutrition_config)
    high = compute_targets(make_client(activity_level=ActivityLevel.VERY_ACTIVE), nutrition_config)
    assert low.daily.kcal < high.daily.kcal  # kcal muy distintas...
    assert low.daily.fat_g == high.daily.fat_g == pytest.approx(49.6, abs=0.1)  # ...misma grasa
    assert low.daily.fat_g / 62.0 == pytest.approx(0.8, abs=0.01)


def test_carb_floor_rejects_a_day_that_cannot_be_built(nutrition_config) -> None:
    """El carbo cierra, así que absorbe todo el exceso de proteína+grasa.

    Es una guarda de imposibilidad, no de opinión: los planes low-carb son
    legítimos. Aquí, una clienta de 100 kg con proteína y grasa al tope no deja
    NINGÚN carbohidrato — el día no se puede armar de ninguna forma.
    """
    client = make_client(
        weight_kg=100.0,
        height_cm=160.0,
        age_years=40,
        activity_level=ActivityLevel.SEDENTARY,
    )
    # kcal = 1639 (su BMR) ; proteína 220 g + grasa 100 g = 1780 kcal → carbo negativo
    formula = MacroFormula(protein_g_per_kg=2.2, fat_g_per_kg=1.0)
    with pytest.raises(CalculationError, match="bajo el piso"):
        compute_targets(client, nutrition_config, formula=formula)


def test_missing_age_raises(nutrition_config) -> None:
    with pytest.raises(CalculationError):
        compute_targets(make_client(age_years=None, birthdate=None), nutrition_config)


def test_overrides_replace_and_are_recorded(nutrition_config) -> None:
    targets = compute_targets(
        make_client(), nutrition_config, overrides={"protein_g": 130.0, "kcal": 1600.0}
    )
    assert targets.daily.protein_g == 130.0
    assert targets.daily.kcal == 1600.0
    assert targets.overrides == {"protein_g": 130.0, "kcal": 1600.0}
    # el reparto por comida se recalcula sobre los macros finales
    assert sum(m.protein_g for m in targets.per_meal.values()) == pytest.approx(130.0, abs=1.0)


def test_overrides_all_macros_recompute_kcal() -> None:
    daily = MacroTargets(kcal=2000, protein_g=100, carb_g=200, fat_g=60)
    result = apply_overrides(daily, {"protein_g": 120.0, "carb_g": 180.0, "fat_g": 50.0})
    assert result.kcal == pytest.approx(120 * 4 + 180 * 4 + 50 * 9, abs=0.1)


def test_unknown_override_rejected() -> None:
    daily = MacroTargets(kcal=2000, protein_g=100, carb_g=200, fat_g=60)
    with pytest.raises(CalculationError):
        apply_overrides(daily, {"sodio_mg": 2300})


@pytest.mark.parametrize(
    "field, bad",
    [
        ("protein_g_per_kg", 3.0),  # sobre 2.2
        ("protein_g_per_kg", 1.2),  # bajo 1.6
        ("fat_g_per_kg", 0.5),  # bajo 0.8
        ("fat_g_per_kg", 1.5),  # sobre 1.0
    ],
)
def test_config_rejects_g_per_kg_outside_the_strategy(nutrition_config, field, bad) -> None:
    """La estrategia está en el schema: un typo en el YAML no llega al cliente."""
    values = nutrition_config.model_dump()
    values[field] = dict.fromkeys(values[field], bad)
    with pytest.raises(ValidationError, match="fuera del rango"):
        NutritionConfig(**values)


def test_empty_formula_reproduces_base(nutrition_config) -> None:
    """Una fórmula vacía da exactamente el cálculo por objetivo (procedencia)."""
    base = compute_targets(make_client(), nutrition_config)
    with_empty = compute_targets(make_client(), nutrition_config, formula=MacroFormula())
    assert with_empty.daily == base.daily


def test_formula_g_per_kg_drives_macros(nutrition_config) -> None:
    # 62 kg · proteína 1.6 g/kg = 99.2 g ; grasa 0.8 g/kg = 49.6 g ; carbo cierra kcal
    formula = MacroFormula(protein_g_per_kg=1.6, fat_g_per_kg=0.8)
    targets = compute_targets(make_client(), nutrition_config, formula=formula)
    assert targets.daily.protein_g == pytest.approx(99.2, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(49.6, abs=0.1)
    assert targets.formula.protein_g_per_kg == 1.6
    # el carbo cierra el invariante energético contra las kcal del objetivo
    recomposed = (
        targets.daily.protein_g * 4 + targets.daily.carb_g * 4 + targets.daily.fat_g * 9
    )
    assert recomposed == pytest.approx(targets.daily.kcal, abs=1.0)


def test_manual_kcal_override(nutrition_config) -> None:
    formula = MacroFormula(protein_g_per_kg=2.0, fat_g_per_kg=1.0, kcal_override=1800.0)
    targets = compute_targets(make_client(), nutrition_config, formula=formula)
    assert targets.daily.kcal == 1800.0
    assert targets.daily.protein_g == pytest.approx(124.0, abs=0.1)  # 2.0 * 62
