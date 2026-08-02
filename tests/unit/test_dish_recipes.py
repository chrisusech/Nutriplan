"""Las recetas de plato y su caché compartida.

Lo que se protege aquí es la economía: con cientos de personas sobre catorce
plantillas, pedirle al modelo lo que ya está escrito quema la cuota gratis en
una tarde.
"""

from pathlib import Path
from uuid import uuid4

from tests.fixtures.plan_builder import build_fixed_plan

from nutriplan.application.dish_recipes import ingredient_lines, recipes_for_week
from nutriplan.domain.dish_recipe import DishRecipe, dish_key
from nutriplan.domain.errors import LLMError

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


class _RepoEnMemoria:
    def __init__(self) -> None:
        self.almacen: dict[str, DishRecipe] = {}
        self.escrituras = 0

    async def get_many(self, keys: list[str]) -> dict[str, DishRecipe]:
        return {k: v for k, v in self.almacen.items() if k in keys}

    async def add(self, recipe: DishRecipe, *, model: str, prompt_version: str) -> None:
        self.almacen[recipe.dish_key] = recipe
        self.escrituras += 1


class _LLMQueCuenta:
    def __init__(self, *, falla: bool = False) -> None:
        self.llamadas = 0
        self.falla = falla

    async def extract(self, *, system: str, text: str, schema: type, model: str):  # type: ignore[no-untyped-def]
        self.llamadas += 1
        if self.falla:
            raise LLMError("sin cuota")
        return schema.model_validate({
            "steps": ["Calienta la sartén.", "Cocina y sirve."],
            "prep_minutes": 10, "difficulty": "fácil", "tips": "",
        })

    async def select_plan(self, **kw):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def pop_usage(self) -> dict[str, int]:
        return {"calls": self.llamadas}


def _semana_con_platos():  # type: ignore[no-untyped-def]
    """Una semana donde todos los almuerzos son el mismo plato."""
    plan, foods, _ = build_fixed_plan()
    meals = []
    for day in plan.days:
        for meal in day.meals:
            ids = [i.food_id for i in meal.items if i.food_id]
            meals.append(meal.model_copy(update={
                "template_id": f"tpl_{meal.slot.value}",
                "dish_name": f"Plato de {meal.slot.value}",
                "dish_key": dish_key(f"tpl_{meal.slot.value}", ids),
            }))
    return meals, foods


# --- La caché ---------------------------------------------------------------


async def test_siete_veces_el_mismo_plato_es_una_sola_receta() -> None:
    """Se resuelve por plato distinto, no por comida."""
    meals, foods = _semana_con_platos()
    repo, llm = _RepoEnMemoria(), _LLMQueCuenta()

    await recipes_for_week(
        meals=meals, catalog=foods, repo=repo, llm=llm,
        prompts_dir=PROMPTS, model="m",
    )
    # 35 comidas en la semana, pero solo un puñado de platos distintos
    assert llm.llamadas < len(meals)
    assert llm.llamadas == repo.escrituras


async def test_la_segunda_persona_con_el_mismo_plato_no_gasta_una_llamada() -> None:
    """*El* test de la caché: es lo que hace viable la cuota gratis."""
    meals, foods = _semana_con_platos()
    repo, llm = _RepoEnMemoria(), _LLMQueCuenta()

    await recipes_for_week(meals=meals, catalog=foods, repo=repo, llm=llm,
                           prompts_dir=PROMPTS, model="m")
    primera_tanda = llm.llamadas
    assert primera_tanda > 0

    await recipes_for_week(meals=meals, catalog=foods, repo=repo, llm=llm,
                           prompts_dir=PROMPTS, model="m")
    assert llm.llamadas == primera_tanda, "el segundo menú no debe llamar al modelo"


async def test_el_mismo_plato_con_gramos_distintos_comparte_receta() -> None:
    """Pollo con arroz se prepara igual con 150 g que con 180."""
    _, foods = _semana_con_platos()
    ids = list(foods)[:2]
    assert dish_key("tpl", ids) == dish_key("tpl", list(reversed(ids)))


def test_platos_distintos_no_comparten_receta() -> None:
    assert dish_key("tpl_a", [uuid4()]) != dish_key("tpl_b", [uuid4()])


# --- Sin IA -----------------------------------------------------------------


async def test_sin_ia_la_receta_sale_del_catalogo_escrito_a_mano() -> None:
    """El modo offline también explica cómo se prepara cada plato."""
    meals, foods = _semana_con_platos()
    repo = _RepoEnMemoria()
    estaticas = {f"tpl_{m.slot.value}": ["Paso escrito a mano."] for m in meals}

    out = await recipes_for_week(
        meals=meals, catalog=foods, repo=repo, llm=None,
        prompts_dir=PROMPTS, model="", static=estaticas,
    )
    assert out
    assert all(r.source == "yaml" for r in out.values())
    assert all(r.steps == ["Paso escrito a mano."] for r in out.values())


async def test_el_catalogo_gana_a_la_ia_para_no_gastar_cuota() -> None:
    meals, foods = _semana_con_platos()
    repo, llm = _RepoEnMemoria(), _LLMQueCuenta()
    estaticas = {f"tpl_{m.slot.value}": ["Paso a mano."] for m in meals}

    await recipes_for_week(meals=meals, catalog=foods, repo=repo, llm=llm,
                           prompts_dir=PROMPTS, model="m", static=estaticas)
    assert llm.llamadas == 0


async def test_si_el_modelo_falla_el_menu_se_queda_sin_receta_pero_entero() -> None:
    """Una receta que falta no es un fallo: se muestran los ingredientes."""
    meals, foods = _semana_con_platos()
    repo, llm = _RepoEnMemoria(), _LLMQueCuenta(falla=True)

    out = await recipes_for_week(meals=meals, catalog=foods, repo=repo, llm=llm,
                                 prompts_dir=PROMPTS, model="m")
    assert out == {}
    assert repo.escrituras == 0


async def test_una_comida_libre_no_lleva_receta() -> None:
    meals, foods = _semana_con_platos()
    libres = [m.model_copy(update={"is_free_meal": True}) for m in meals]
    out = await recipes_for_week(meals=libres, catalog=foods, repo=_RepoEnMemoria(),
                                 llm=_LLMQueCuenta(), prompts_dir=PROMPTS, model="m")
    assert out == {}


# --- Los ingredientes los pone el plan, no la IA ----------------------------


def test_los_ingredientes_llevan_los_gramos_que_calculo_el_codigo() -> None:
    """La IA escribe los pasos; las cantidades salen del solver."""
    meals, foods = _semana_con_platos()
    desayuno = meals[0]
    lineas = ingredient_lines(desayuno, foods)

    assert lineas
    assert any("g)" in line or " g" in line for line in lineas)
