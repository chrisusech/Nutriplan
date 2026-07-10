"""Golden values (calculados a mano) para el motor de cálculo."""

from uuid import uuid4

import pytest

from nutriplan.domain.calculation import apply_overrides, compute_targets
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MacroFormula,
    MacroTargets,
    Sex,
)


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
    # BMR = 10*62 + 6.25*165 - 5*30 - 161 = 1340.25
    # TDEE = 1340.25 * 1.55 = 2077.3875 ; kcal = * 0.82 = 1703.46
    targets = compute_targets(make_client(), nutrition_config)
    assert targets.daily.kcal == pytest.approx(1703.5, abs=0.1)
    assert targets.daily.protein_g == pytest.approx(124.0, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(53.0, abs=0.1)
    assert targets.daily.carb_g == pytest.approx(182.6, abs=0.1)
    assert targets.config_version == nutrition_config.version


def test_golden_male_gain_muscle(nutrition_config) -> None:
    # BMR = 10*80 + 6.25*180 - 5*25 + 5 = 1805 ; TDEE = 1805*1.725 = 3113.625
    # kcal = * 1.10 = 3424.99
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
    assert targets.daily.protein_g == pytest.approx(160.0, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(106.6, abs=0.1)
    assert targets.daily.carb_g == pytest.approx(456.5, abs=0.1)


def test_golden_female_maintain(nutrition_config) -> None:
    # BMR = 700 + 1062.5 - 200 - 161 = 1401.5 ; TDEE = kcal = 1927.06
    client = make_client(
        weight_kg=70.0,
        height_cm=170.0,
        age_years=40,
        goal=Goal.MAINTAIN,
        activity_level=ActivityLevel.LIGHT,
    )
    targets = compute_targets(client, nutrition_config)
    assert targets.daily.kcal == pytest.approx(1927.1, abs=0.1)
    assert targets.daily.protein_g == pytest.approx(126.0, abs=0.1)
    assert targets.daily.fat_g == pytest.approx(60.0, abs=0.1)
    assert targets.daily.carb_g == pytest.approx(220.9, abs=0.1)


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
        apply_overrides(daily, {"fiber_g": 30})


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
