"""Macros puestos por la persona: el plan usa exactamente esos números."""

from uuid import uuid4

import pytest

from nutriplan.application.user_macros import user_macro_plan
from nutriplan.domain.adapt_targets import USER_LOCK, is_admin_locked
from nutriplan.domain.calculation import compute_targets, energy_kcal
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import ActivityLevel, Client, Goal, Sex
from nutriplan.domain.nutrition_config import NutritionConfig


def _client() -> Client:
    return Client(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Ana",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )


def test_los_macros_del_perfil_son_los_que_come_el_plan(
    nutrition_config: NutritionConfig,
) -> None:
    client = _client()
    formula, overrides, warning = user_macro_plan(
        client=client,
        config=nutrition_config,
        kcal=2000,
        protein_g=140,
        carb_g=180,
        fat_g=80,
    )
    assert warning is None
    targets = compute_targets(client, nutrition_config, formula=formula, overrides=overrides)
    assert targets.daily.protein_g == 140
    assert targets.daily.carb_g == 180
    assert targets.daily.fat_g == 80
    assert targets.daily.kcal == energy_kcal(140, 180, 80)
    assert is_admin_locked(targets)
    assert (targets.overrides or {}).get(USER_LOCK)


def test_si_los_gramos_no_cuadran_con_las_kcal_se_avisa(
    nutrition_config: NutritionConfig,
) -> None:
    _formula, _overrides, warning = user_macro_plan(
        client=_client(),
        config=nutrition_config,
        kcal=3000,
        protein_g=150,
        carb_g=200,
        fat_g=80,
    )
    assert warning is not None
    assert "3000" in warning


def test_bajo_el_piso_no_se_guarda(nutrition_config: NutritionConfig) -> None:
    with pytest.raises(CalculationError, match="piso"):
        user_macro_plan(
            client=_client(),
            config=nutrition_config,
            kcal=800,
            protein_g=40,
            carb_g=40,
            fat_g=20,
        )


def test_si_pide_que_se_adapten_no_hay_candado(
    nutrition_config: NutritionConfig,
) -> None:
    _formula, overrides, _w = user_macro_plan(
        client=_client(),
        config=nutrition_config,
        kcal=2000,
        protein_g=140,
        carb_g=180,
        fat_g=80,
        adapt_with_weight=True,
    )
    assert USER_LOCK not in overrides


def test_gramos_cero_no_se_guardan(nutrition_config: NutritionConfig) -> None:
    with pytest.raises(CalculationError, match="mayores que cero"):
        user_macro_plan(
            client=_client(),
            config=nutrition_config,
            kcal=2000,
            protein_g=0,
            carb_g=200,
            fat_g=80,
        )


def test_unas_kcal_disparatadas_no_se_guardan(nutrition_config: NutritionConfig) -> None:
    with pytest.raises(CalculationError, match="rango"):
        user_macro_plan(
            client=_client(),
            config=nutrition_config,
            kcal=9000,
            protein_g=200,
            carb_g=800,
            fat_g=200,
        )
