"""Tests de build_selection_prompt con hábitos del cliente."""

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.application.plan_cache import compute_input_hash, plan_cache_key
from nutriplan.application.plan_prompt import build_selection_prompt
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    FoodCategory,
    FoodItem,
    Goal,
    MacroTargets,
    NutritionTargets,
    Sex,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.taste import RatedDish, build_taste_profile

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
# El id del alimento entra en la clave de caché: si cada llamada inventa uno,
# la comparación de claves mide el uuid y no lo que se quiere medir.
_MISMA_COMIDA = uuid4()


def _targets() -> NutritionTargets:
    return NutritionTargets(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        daily=MacroTargets(kcal=1500, protein_g=100, carb_g=150, fat_g=50),
        per_meal={},
        method="test",
        config_version="v1",
        computed_at=datetime.now(UTC),
    )


def _cliente() -> Client:
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


def _food(**kwargs: object) -> FoodItem:
    base: dict[str, object] = {
        "id": uuid4(),
        "source": "USDA",
        "name_es": "huevo entero",
        "category": FoodCategory.PROTEIN,
        "kcal_100g": 143,
        "protein_100g": 12.6,
        "carb_100g": 0.7,
        "fat_100g": 9.5,
        "default_unit_g": 50.0,
        "unit_name": "huevo",
        "unit_granularity": UnitGranularity.WHOLE,
    }
    base.update(kwargs)
    return FoodItem(**base)  # type: ignore[arg-type]


def test_build_selection_prompt_includes_habits(nutrition_config: NutritionConfig) -> None:
    prompt = build_selection_prompt(
        _targets(),
        [_food()],
        nutrition_config,
        habits="Desayuno arepa con huevo; cena liviana.",
    )
    assert "HÁBITOS DEL CLIENTE" in prompt
    assert "arepa con huevo" in prompt


def test_build_selection_prompt_omits_empty_habits(nutrition_config: NutritionConfig) -> None:
    prompt = build_selection_prompt(_targets(), [_food()], nutrition_config, habits="  ")
    assert "HÁBITOS DEL CLIENTE" not in prompt


def test_el_prompt_de_seleccion_lleva_macros_y_densidad(
    nutrition_config: NutritionConfig,
) -> None:
    """Sin macros la IA elegía a ciegas y el menú salía básico o imposible."""
    prompt = build_selection_prompt(_targets(), [_food()], nutrition_config)
    assert "OBJETIVO DIARIO" in prompt
    assert "1500 kcal" in prompt
    assert "ORIENTACIÓN POR COMIDA" in prompt
    assert "P13/C1/G10/kcal143 por 100g" in prompt
    assert "1 huevo≈50g" in prompt
    assert "dish_name" in prompt


def test_lo_que_pidio_la_semana_pasada_entra_en_el_prompt(
    nutrition_config: NutritionConfig,
) -> None:
    """El bucle que hace que la semana 6 se parezca más a quien la come."""
    pollo = _food(name_es="pechuga de pollo")
    taste = build_taste_profile(
        [
            RatedDish("t1", "k1", "Tilapia al vapor", 1),
            RatedDish("t1", "k1", "Tilapia al vapor", 2),
            RatedDish("t2", "k2", "Pollo al limón", 5),
        ],
        avoid_food_ids=[pollo.id],
        adjustments=["menos fritos"],
    )

    prompt = build_selection_prompt(
        _targets(), [pollo], nutrition_config, aliases={"f0": pollo.id}, taste=taste
    )

    assert "LO QUE YA SABEMOS DE ESTA PERSONA" in prompt
    assert "Pollo al limón" in prompt
    assert "Tilapia al vapor" in prompt
    assert "no volver a ver: f0" in prompt
    assert "menos fritos" in prompt


def test_a_quien_no_ha_dicho_nada_no_se_le_inventa_un_gusto(
    nutrition_config: NutritionConfig,
) -> None:
    prompt = build_selection_prompt(
        _targets(), [_food()], nutrition_config, taste=build_taste_profile([])
    )
    assert "LO QUE YA SABEMOS DE ESTA PERSONA" not in prompt


def test_dos_gustos_distintos_no_reciben_el_menu_de_la_cache(
    nutrition_config: NutritionConfig,
) -> None:
    """Sin el gusto en el hash, «no más pescado» devolvía el mismo menú."""
    targets = _targets()
    comida = [_food()]
    args = (targets, "v1", "plan_generation.v4", comida)
    igual = compute_input_hash(
        _cliente(), *args, taste=build_taste_profile([], adjustments=["menos fritos"])
    )
    distinto = compute_input_hash(_cliente(), *args, taste=build_taste_profile([]))
    assert igual != distinto


def test_marcar_la_nevera_no_devuelve_el_menu_de_la_cache(
    nutrition_config: NutritionConfig,
) -> None:
    """Decir "esto ya lo tengo" cambia el menú, así que tiene que cambiar la
    clave: si no, generar después de marcarlo sirve el plan de antes y parece que
    el botón no hace nada."""
    args = (_targets(), "v1", "plan_generation.v4", [_food(id=_MISMA_COMIDA)])
    cliente = _cliente()
    assert compute_input_hash(cliente, *args) != compute_input_hash(
        cliente, *args, on_hand=frozenset({_MISMA_COMIDA})
    )


def test_a_la_ia_se_le_cuenta_lo_que_ya_hay_en_la_nevera(
    nutrition_config: NutritionConfig,
) -> None:
    prompt = build_selection_prompt(
        _targets(), [_food()], nutrition_config, on_hand=["arroz blanco cocido"]
    )
    assert "YA LO TIENE EN CASA" in prompt
    assert "arroz blanco cocido" in prompt
    assert "nunca a costa de la variedad" in prompt


def test_a_quien_no_ha_marcado_nada_no_se_le_menciona_la_nevera(
    nutrition_config: NutritionConfig,
) -> None:
    assert "YA LO TIENE EN CASA" not in build_selection_prompt(
        _targets(), [_food()], nutrition_config
    )


def test_una_semana_nueva_no_devuelve_el_menu_de_la_anterior(
    nutrition_config: NutritionConfig,
) -> None:
    """Quien se mantiene estable recibiría siempre el plan guardado."""
    args = (_targets(), "v1", "plan_generation.v4", [_food()])
    cliente = _cliente()
    assert compute_input_hash(cliente, *args, week_start=date(2026, 8, 10)) != compute_input_hash(
        cliente, *args, week_start=date(2026, 8, 17)
    )


def _clave(nutrition_config: NutritionConfig, **motor: object) -> str:
    """La misma persona y la misma comida; lo único que cambia es el motor."""
    return plan_cache_key(
        client=_cliente(),
        targets=_targets(),
        config=nutrition_config,
        allowed=[_food(id=_MISMA_COMIDA)],
        variant=0,
        catalog=None,
        week_start=date(2026, 8, 10),
        taste=None,
        **motor,  # type: ignore[arg-type]
    )


def test_el_plan_se_guarda_con_la_misma_clave_con_la_que_se_busca(
    nutrition_config: NutritionConfig,
) -> None:
    """Con la IA encendida la caché no acertaba nunca: dos claves distintas.

    Quien guardaba añadía el crítico a la versión del prompt y quien buscaba no,
    así que `find_by_input_hash` fallaba siempre y regenerar un plan aprobado
    chocaba contra la unicidad de (tenant, input_hash, variant).
    """
    con_ia = {"llm": MockLLMClient(), "select_foods": True, "refine_names": True}
    assert _clave(nutrition_config, **con_ia) == _clave(nutrition_config, **con_ia)


def test_encender_el_critico_cambia_el_menu_y_por_tanto_la_clave(
    nutrition_config: NutritionConfig,
) -> None:
    """El mismo perfil con y sin pasada crítica no produce el mismo menú."""
    sin_critico = _clave(
        nutrition_config, llm=MockLLMClient(), select_foods=True, refine_names=False
    )
    con_critico = _clave(
        nutrition_config, llm=MockLLMClient(), select_foods=True, refine_names=True
    )
    assert sin_critico != con_critico


def test_sin_ia_la_clave_no_menciona_al_critico(nutrition_config: NutritionConfig) -> None:
    """En modo offline el crítico no entra: no hay pasada que invalidar."""
    apagado = _clave(nutrition_config, llm=None, select_foods=True, refine_names=True)
    assert apagado == _clave(nutrition_config, llm=None, select_foods=False, refine_names=False)
