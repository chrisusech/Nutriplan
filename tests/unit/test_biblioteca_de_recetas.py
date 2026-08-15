"""La biblioteca de recetas: lo que la app aprende a cocinar y devuelve al menú.

Tres historias encadenadas: un plato se resuelve y queda con sus macros, la
gente lo califica y sube de nota, y esa nota hace que vuelva a proponerse a
quien pueda comerlo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from nutriplan.adapters.meals.recipe_catalog_store import (
    RecipeCatalogError,
    append_recipe,
    load_recipe_catalog,
)
from nutriplan.application.dish_recipes import reference_portion
from nutriplan.application.generate_plan import build_selection_prompt
from nutriplan.application.recipe_library import (
    curated_from,
    food_token,
    is_promotable,
)
from nutriplan.domain.dish_recipe import DishRecipe, dish_key
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
    NutritionTargets,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.proven import proven_dishes

CATALOGO = """
version: "0.1.0"
recipes:
  - id: ya_estaba
    name_es: Algo que ya estaba
    foods: [huevo_entero]
    steps:
      - Hazlo.
"""


def _food(nombre: str = "pechuga de pollo", **kwargs: object) -> FoodItem:
    base: dict[str, object] = {
        "id": uuid4(),
        "source": "USDA",
        "name_es": nombre,
        "category": FoodCategory.PROTEIN,
        "kcal_100g": 165.0,
        "protein_100g": 31.0,
        "carb_100g": 0.0,
        "fat_100g": 3.6,
        "unit_granularity": UnitGranularity.GRAMS,
    }
    base.update(kwargs)
    return FoodItem(**base)  # type: ignore[arg-type]


def _receta(**kwargs: object) -> DishRecipe:
    base: dict[str, object] = {
        "dish_key": uuid4().hex[:32],
        "name_es": "Pollo al limón con arroz",
        "steps": ["Dora el pollo.", "Sirve con el arroz."],
        "source": "ai",
        "food_ids": [],
        "rating_avg": None,
        "rating_count": 0,
    }
    base.update(kwargs)
    return DishRecipe(**base)  # type: ignore[arg-type]


# --- Los macros los pone el código ------------------------------------------


def test_una_receta_guarda_de_que_esta_hecha_y_cuanto_alimenta() -> None:
    """El modelo escribe los pasos; las cifras las calcula el código."""
    pollo = _food("pechuga de pollo")
    arroz = _food(
        "arroz blanco",
        category=FoodCategory.CARB,
        kcal_100g=130.0,
        protein_100g=2.7,
        carb_100g=28.0,
        fat_100g=0.3,
    )
    comida = MealEntry(
        slot=MealSlot.LUNCH,
        items=[
            MealItem(food_id=pollo.id, grams=150.0, position=0),
            MealItem(food_id=arroz.id, grams=100.0, position=1),
        ],
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
    )

    ids, gramos, macros = reference_portion(comida, {pollo.id: pollo, arroz.id: arroz})

    assert set(ids) == {pollo.id, arroz.id}
    assert gramos[str(pollo.id)] == 150.0
    assert macros is not None
    assert macros.kcal == pytest.approx(165 * 1.5 + 130, abs=0.5)
    assert macros.protein_g == pytest.approx(31 * 1.5 + 2.7, abs=0.5)


def test_un_plato_sin_gramos_no_inventa_macros() -> None:
    comida = MealEntry(
        slot=MealSlot.LUNCH,
        items=[],
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
        is_free_meal=True,
    )
    ids, gramos, macros = reference_portion(comida, {})
    assert (ids, gramos, macros) == ([], {}, None)


def test_el_mismo_plato_con_otros_gramos_sigue_siendo_el_mismo_plato() -> None:
    """La clave deja fuera los gramos: si no, la caché se partiría en variantes."""
    a, b = uuid4(), uuid4()
    assert dish_key("t1", [a, b]) == dish_key("t1", [b, a])


# --- Promover al catálogo humano --------------------------------------------


def test_una_receta_bien_valorada_se_puede_promover() -> None:
    assert is_promotable(_receta(rating_avg=4.5, rating_count=3))


def test_una_receta_con_una_sola_nota_todavia_no() -> None:
    """Una nota alta con un voto es la opinión de alguien, no la del plato."""
    assert not is_promotable(_receta(rating_avg=5.0, rating_count=1))


def test_una_receta_mal_valorada_no_se_promueve() -> None:
    assert not is_promotable(_receta(rating_avg=2.0, rating_count=9))


def test_una_receta_ya_curada_no_se_vuelve_a_curar() -> None:
    assert not is_promotable(_receta(source="curated", rating_avg=5.0, rating_count=9))


def test_al_promover_se_nombran_los_alimentos_como_los_nombra_el_yaml() -> None:
    pollo = _food("Pechuga de pollo")
    receta = _receta(food_ids=[pollo.id], rating_avg=4.5, rating_count=3)

    entrada = curated_from(receta, {pollo.id: pollo})

    assert entrada.foods == [food_token(pollo)]
    assert entrada.name_es == "Pollo al limón con arroz"
    assert entrada.steps


def test_una_receta_que_no_sabe_de_que_esta_hecha_no_se_puede_promover() -> None:
    """Las de antes de que existieran los macros: se completan al servirse."""
    with pytest.raises(ValidationError):
        curated_from(_receta(food_ids=[]), {})


def test_promover_escribe_en_el_yaml_y_se_puede_releer(tmp_path: Path) -> None:
    ruta = tmp_path / "catalog.yaml"
    ruta.write_text(CATALOGO, encoding="utf-8")
    pollo = _food("pechuga de pollo")
    entrada = curated_from(_receta(food_ids=[pollo.id]), {pollo.id: pollo})

    assert append_recipe(ruta, entrada) is True

    recargado = load_recipe_catalog(ruta)
    assert len(recargado.recipes) == 2
    assert any(r.name_es == "Pollo al limón con arroz" for r in recargado.recipes)
    assert yaml.safe_load(ruta.read_text(encoding="utf-8"))["version"] == "0.1.0"


def test_promover_dos_veces_no_duplica_la_receta(tmp_path: Path) -> None:
    ruta = tmp_path / "catalog.yaml"
    ruta.write_text(CATALOGO, encoding="utf-8")
    pollo = _food("pechuga de pollo")
    entrada = curated_from(_receta(food_ids=[pollo.id]), {pollo.id: pollo})

    append_recipe(ruta, entrada)
    assert append_recipe(ruta, entrada) is False
    assert len(load_recipe_catalog(ruta).recipes) == 2


def test_un_catalogo_ilegible_no_se_sobreescribe(tmp_path: Path) -> None:
    """Antes de escribir se lee: un YAML roto se queda roto, no se empeora."""
    ruta = tmp_path / "catalog.yaml"
    ruta.write_text("recipes: [{", encoding="utf-8")
    pollo = _food()
    with pytest.raises(RecipeCatalogError):
        append_recipe(ruta, curated_from(_receta(food_ids=[pollo.id]), {pollo.id: pollo}))


# --- Y de vuelta al menú ----------------------------------------------------


def _con_nota(nota: float, votos: int, ids: list[UUID]) -> DishRecipe:
    return _receta(rating_avg=nota, rating_count=votos, food_ids=ids)


def test_los_platos_probados_solo_incluyen_los_bien_valorados() -> None:
    a, b = uuid4(), uuid4()
    elegidos = proven_dishes([_con_nota(4.8, 5, [a]), _con_nota(2.0, 5, [b])], {a, b})
    assert len(elegidos) == 1
    assert elegidos[0].rating_avg == 4.8


def test_no_se_le_propone_un_plato_con_un_alimento_que_no_puede_comer() -> None:
    """Proponerlo sería pedirle al modelo que rompa su propio esquema."""
    permitido, vetado = uuid4(), uuid4()
    elegidos = proven_dishes([_con_nota(5.0, 4, [permitido, vetado])], {permitido})
    assert elegidos == []


def test_los_platos_probados_salen_del_mejor_al_peor() -> None:
    a = uuid4()
    elegidos = proven_dishes([_con_nota(4.2, 9, [a]), _con_nota(4.9, 2, [a])], {a})
    assert [d.rating_avg for d in elegidos] == [4.9, 4.2]


def test_el_prompt_le_ensena_al_modelo_los_platos_que_ya_salieron_bien(
    nutrition_config: NutritionConfig,
) -> None:
    pollo = _food("pechuga de pollo")
    probados = proven_dishes([_con_nota(4.6, 3, [pollo.id])], {pollo.id})

    prompt = build_selection_prompt(
        _targets(),
        [pollo],
        nutrition_config,
        aliases={"f0": pollo.id},
        proven=probados,
    )

    assert "PLATOS PROBADOS" in prompt
    assert "Pollo al limón con arroz" in prompt
    assert "4.6★" in prompt


def test_sin_platos_probados_el_prompt_no_lleva_la_seccion(
    nutrition_config: NutritionConfig,
) -> None:
    prompt = build_selection_prompt(_targets(), [_food()], nutrition_config)
    assert "PLATOS PROBADOS" not in prompt


def test_un_plato_probado_cuyo_alias_no_esta_a_la_vista_no_se_menciona(
    nutrition_config: NutritionConfig,
) -> None:
    """Nombrarlo sin decir con qué ids reproducirlo solo confunde al modelo.

    Pasa de verdad: al modelo se le enseña un catálogo recortado, y un plato
    probado puede llevar un alimento que esta vez no está en la lista corta.
    """
    pollo = _food("pechuga de pollo")
    otro = _food("lomo de cerdo")
    probados = proven_dishes([_con_nota(4.6, 3, [pollo.id])], {pollo.id})

    prompt = build_selection_prompt(
        _targets(), [otro], nutrition_config, aliases={"f0": otro.id}, proven=probados
    )
    assert "PLATOS PROBADOS" not in prompt


def _targets() -> NutritionTargets:
    return NutritionTargets(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        daily=MacroTargets(kcal=1800, protein_g=120, carb_g=180, fat_g=60),
        per_meal={},
        config_version="v1",
        computed_at=datetime.now(UTC),
    )
