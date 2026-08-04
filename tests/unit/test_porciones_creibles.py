"""Porciones creíbles: el motor no ofrece 9 tortillas aunque las kcal cierren."""

from uuid import uuid4

from nutriplan.domain.models import FoodCategory, FoodItem, UnitGranularity
from nutriplan.domain.portioning import can_cover_carb


def _tortilla(**extra: object) -> FoodItem:
    data = dict(
        id=uuid4(),
        source="curated",
        name_es="tortilla de maíz",
        category=FoodCategory.CARB,
        kcal_100g=218,
        protein_100g=5.7,
        carb_100g=44.6,
        fat_100g=2.9,
        default_unit_g=30,
        unit_granularity=UnitGranularity.WHOLE,
        unit_name="tortilla",
        portion_max_g=90,  # 3 tortillas
    )
    data.update(extra)
    return FoodItem(**data)  # type: ignore[arg-type]


def _arroz() -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="curated",
        name_es="arroz blanco cocido",
        category=FoodCategory.CARB,
        kcal_100g=130,
        protein_100g=2.7,
        carb_100g=28.2,
        fat_100g=0.3,
        portion_max_g=500,
    )


def test_tres_tortillas_bastan_para_un_desayuno_chico() -> None:
    # ~40 g de carbo → ~90 g de tortilla = 3 unidades, justo en el tope.
    assert can_cover_carb(_tortilla(), carb_target_g=40.0)


def test_nueve_tortillas_no_se_ofrecen_en_un_almuerzo_grande() -> None:
    # ~130 g de carbo pedirían ~290 g ≈ 9–10 tortillas → fuera del plato real.
    assert not can_cover_carb(_tortilla(), carb_target_g=130.0)


def test_el_arroz_si_cubre_un_almuerzo_grande() -> None:
    assert can_cover_carb(_arroz(), carb_target_g=130.0)


def test_sin_tope_la_tortilla_podria_servir_demasiado() -> None:
    """Sin portion_max_g el techo global (600 g) sí dejaría pasar 9 tortillas."""
    assert can_cover_carb(_tortilla(portion_max_g=None), carb_target_g=130.0)
