"""Adaptación semanal de kcal: el código manda, no la IA."""

from uuid import uuid4

import pytest

from nutriplan.domain.adapt_targets import (
    ADMIN_LOCK,
    USER_LOCK,
    AdaptAction,
    decide_kcal_adaptation,
    is_admin_locked,
)
from nutriplan.domain.calculation import compute_targets, kcal_floor_for
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MacroFormula,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig


def make_client(**overrides: object) -> Client:
    base: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Ana",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=70.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )
    base.update(overrides)
    return Client(**base)  # type: ignore[arg-type]


def test_si_no_baja_de_peso_en_deficit_bajan_las_kcal(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client(goal=Goal.LOSE_FAT, weight_kg=70.0)
    base = compute_targets(client, nutrition_config)
    decision = decide_kcal_adaptation(
        client=client.model_copy(update={"weight_kg": 70.1}),
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=70.1,
        previous_kcal=base.daily.kcal,
        previous_formula=base.formula,
    )
    assert decision.action is AdaptAction.LOWER
    assert decision.delta_kcal == -nutrition_config.adaptation.step_kcal
    assert decision.new_kcal == base.daily.kcal - nutrition_config.adaptation.step_kcal
    assert decision.formula.kcal_override == decision.new_kcal


def test_si_baja_demasiado_rapido_en_deficit_suben_las_kcal(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client(goal=Goal.LOSE_FAT, weight_kg=70.0)
    base = compute_targets(client, nutrition_config)
    decision = decide_kcal_adaptation(
        client=client.model_copy(update={"weight_kg": 68.5}),
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=68.5,
        previous_kcal=base.daily.kcal,
        previous_formula=base.formula,
    )
    assert decision.action is AdaptAction.RAISE
    assert decision.delta_kcal == nutrition_config.adaptation.step_kcal
    assert decision.new_kcal > base.daily.kcal


def test_la_adaptacion_no_baja_del_piso_energetico(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client(goal=Goal.LOSE_FAT, weight_kg=70.0, sex=Sex.FEMALE)
    floor = kcal_floor_for(client, nutrition_config)
    # Ya está en el piso: bajar debería quedar en hold_clamped.
    decision = decide_kcal_adaptation(
        client=client,
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=70.2,
        previous_kcal=floor,
        previous_formula=MacroFormula(kcal_override=floor),
    )
    assert decision.new_kcal >= floor
    assert decision.action is AdaptAction.HOLD
    assert decision.reason_code == "hold_clamped"


def test_en_volumen_sin_subir_peso_suben_las_kcal(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client(goal=Goal.GAIN_MUSCLE, weight_kg=70.0)
    base = compute_targets(client, nutrition_config)
    decision = decide_kcal_adaptation(
        client=client,
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=70.0,
        previous_kcal=base.daily.kcal,
        previous_formula=base.formula,
    )
    assert decision.action is AdaptAction.RAISE
    assert decision.delta_kcal > 0


def test_lo_que_el_entrenador_puso_a_mano_el_lunes_no_lo_deshace(
    nutrition_config: NutritionConfig,
) -> None:
    """La báscula no sabe por qué se subieron esas kcal; quien las subió sí."""
    client = make_client(goal=Goal.LOSE_FAT, weight_kg=70.0)
    decision = decide_kcal_adaptation(
        client=client.model_copy(update={"weight_kg": 70.3}),
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=70.3,
        previous_kcal=2100.0,
        previous_formula=MacroFormula(kcal_override=2100.0),
        admin_locked=True,
    )
    assert decision.action is AdaptAction.HOLD
    assert decision.reason_code == "hold_admin_lock"
    assert decision.new_kcal == 2100.0
    assert decision.formula.kcal_override == 2100.0


def test_el_candado_de_la_persona_tambien_congela_las_kcal(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client()
    targets = compute_targets(client, nutrition_config, overrides={USER_LOCK: 1.0})
    assert is_admin_locked(targets)
    decision = decide_kcal_adaptation(
        client=client.model_copy(update={"weight_kg": 70.3}),
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=70.3,
        previous_kcal=targets.daily.kcal,
        previous_formula=targets.formula,
        admin_locked=True,
    )
    assert decision.action is AdaptAction.HOLD
    assert decision.new_kcal == targets.daily.kcal


def test_el_candado_se_ve_en_los_targets_guardados(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client()
    targets = compute_targets(client, nutrition_config, overrides={ADMIN_LOCK: 1.0})
    assert is_admin_locked(targets)
    assert not is_admin_locked(compute_targets(client, nutrition_config))


def test_el_candado_no_se_cuela_en_los_macros(
    nutrition_config: NutritionConfig,
) -> None:
    """Es una marca, no un gramo: el cálculo tiene que ignorarla."""
    client = make_client()
    sin_marca = compute_targets(client, nutrition_config)
    con_marca = compute_targets(client, nutrition_config, overrides={ADMIN_LOCK: 1.0})
    assert con_marca.daily == sin_marca.daily


def test_un_ajuste_mal_escrito_sigue_saltando(
    nutrition_config: NutritionConfig,
) -> None:
    """La lista blanca existe para que un typo no se guarde en silencio."""
    with pytest.raises(CalculationError):
        compute_targets(make_client(), nutrition_config, overrides={"kcals": 2000.0})


def test_si_va_en_ritmo_esperado_se_mantienen_las_kcal(
    nutrition_config: NutritionConfig,
) -> None:
    client = make_client(goal=Goal.LOSE_FAT, weight_kg=70.0)
    base = compute_targets(client, nutrition_config)
    decision = decide_kcal_adaptation(
        client=client.model_copy(update={"weight_kg": 69.5}),
        config=nutrition_config,
        previous_weight_kg=70.0,
        current_weight_kg=69.5,
        previous_kcal=base.daily.kcal,
        previous_formula=base.formula,
    )
    assert decision.action is AdaptAction.HOLD
    assert decision.delta_kcal == 0
    assert decision.new_kcal == base.daily.kcal
