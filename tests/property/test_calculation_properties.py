"""Propiedades del motor de cálculo (Hypothesis).

Para cualquier cliente válido:
  (a) sum(per_meal) == daily (±1 g / ±1 kcal por redondeo)
  (b) protein*4 + carb*4 + fat*9 ≈ kcal (±1%)
"""

from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nutriplan.domain.calculation import compute_targets
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import ActivityLevel, Client, Goal, Sex

clients = st.builds(
    Client,
    id=st.just(uuid4()),
    tenant_id=st.just(uuid4()),
    name=st.just("prop"),
    sex=st.sampled_from(Sex),
    birthdate=st.none(),
    age_years=st.integers(min_value=18, max_value=80),
    height_cm=st.floats(min_value=140, max_value=210),
    weight_kg=st.floats(min_value=40, max_value=150),
    goal=st.sampled_from(Goal),
    activity_level=st.sampled_from(ActivityLevel),
)


@given(client=clients)
def test_per_meal_sums_to_daily(client: Client, nutrition_config) -> None:
    try:
        targets = compute_targets(client, nutrition_config)
    except CalculationError:
        return  # macros que no cierran se rechazan explícitamente, no se adivinan
    for macro in ("kcal", "protein_g", "carb_g", "fat_g"):
        total = sum(getattr(m, macro) for m in targets.per_meal.values())
        assert total == pytest.approx(getattr(targets.daily, macro), abs=1.0)


@given(client=clients)
def test_energy_consistency(client: Client, nutrition_config) -> None:
    try:
        targets = compute_targets(client, nutrition_config)
    except CalculationError:
        return
    daily = targets.daily
    energy = daily.protein_g * 4 + daily.carb_g * 4 + daily.fat_g * 9
    assert energy == pytest.approx(daily.kcal, rel=0.01)


@given(client=clients)
def test_macros_never_negative(client: Client, nutrition_config) -> None:
    try:
        targets = compute_targets(client, nutrition_config)
    except CalculationError:
        return
    daily = targets.daily
    assert min(daily.kcal, daily.protein_g, daily.carb_g, daily.fat_g) >= 0
