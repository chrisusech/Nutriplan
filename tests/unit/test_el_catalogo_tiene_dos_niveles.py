"""Miles de alimentos en la base, unos pocos cientos en la pantalla.

El catálogo profundo existe para que el motor encuentre los macros de lo que
alguien pida por su nombre, no para que nadie lo navegue. Estos tests fijan las
dos mitades de esa promesa: que no se lista, y que sí se encuentra.
"""

from uuid import uuid4

import pytest

from nutriplan.application.swap_pool import MAX_RETRIEVED, pool_for_note, promoted_ids
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot


def _food(name: str, **kw) -> FoodItem:
    base = dict(
        id=uuid4(),
        source="USDA",
        name_es=name,
        category=FoodCategory.PROTEIN,
        kcal_100g=120,
        protein_100g=20,
        carb_100g=0,
        fat_100g=4,
        meal_slots=[MealSlot.LUNCH, MealSlot.DINNER],
    )
    base.update(kw)
    return FoodItem(**base)


class _FakeFoodRepo:
    """Un catálogo profundo de mentira: busca por subcadena, como el de verdad."""

    def __init__(self, fondo: list[FoodItem]) -> None:
        self.fondo = fondo
        self.consultas: list[str] = []

    async def search_deep(self, query: str, category=None, limit: int = 30) -> list[FoodItem]:
        self.consultas.append(query)
        q = query.lower()
        return [f for f in self.fondo if q in f.name_es.lower()][:limit]


async def test_pedir_salmon_ahumado_lo_trae_del_catalogo_profundo() -> None:
    """Lo que la persona escribe alcanza alimentos que nunca marcó."""
    base = [_food("pechuga de pollo")]
    repo = _FakeFoodRepo([_food("salmón ahumado"), _food("hígado de cordero")])

    pool = await pool_for_note(
        note="cámbiame el almuerzo por salmón ahumado",
        base=base,
        food_repo=repo,
        restrictions=[],
    )
    assert [f.name_es for f in pool] == ["pechuga de pollo", "salmón ahumado"]


async def test_una_nota_no_es_una_puerta_trasera_a_lo_que_tiene_prohibido() -> None:
    """Pedir pescado por escrito no se lo sirve a quien no come pescado."""
    repo = _FakeFoodRepo([_food("salmón ahumado", tags=["pescado"])])
    pool = await pool_for_note(
        note="quiero salmón ahumado",
        base=[_food("pechuga de pollo")],
        food_repo=repo,
        restrictions=["no_fish"],
    )
    assert [f.name_es for f in pool] == ["pechuga de pollo"]


async def test_un_alimento_vetado_no_vuelve_por_la_puerta_de_la_nota() -> None:
    prohibido = _food("hígado de cordero")
    repo = _FakeFoodRepo([prohibido])
    pool = await pool_for_note(
        note="quiero hígado de cordero",
        base=[],
        food_repo=repo,
        restrictions=[],
        banned={prohibido.id},
    )
    assert pool == []


async def test_sin_nota_no_se_toca_el_catalogo_profundo() -> None:
    """Generar sin pedir nada no dispara búsquedas: el fondo cuesta consultas."""
    repo = _FakeFoodRepo([_food("salmón ahumado")])
    base = [_food("pechuga de pollo")]
    assert await pool_for_note(note="  ", base=base, food_repo=repo, restrictions=[]) == base
    assert repo.consultas == []


async def test_una_palabra_corta_no_arrastra_medio_catalogo() -> None:
    """«pan» como subcadena trae de todo; eso lo resuelve el catálogo corto."""
    repo = _FakeFoodRepo([_food("pan de banano"), _food("panela")])
    pool = await pool_for_note(note="quiero pan", base=[], food_repo=repo, restrictions=[])
    assert pool == []


async def test_el_pool_que_ve_la_ia_nunca_crece_sin_limite() -> None:
    """Todo esto acaba en un enum que el modelo tiene que leer entero."""
    fondo = [_food(f"pescado numero {i}") for i in range(200)]
    pool = await pool_for_note(
        note="quiero pescado",
        base=[],
        food_repo=_FakeFoodRepo(fondo),
        restrictions=[],
    )
    assert len(pool) <= MAX_RETRIEVED


def test_solo_se_suma_al_perfil_lo_que_acabo_en_el_plato() -> None:
    """Buscar diez y usar uno no le llena la despensa de los otros nueve."""
    usado, ignorado = _food("salmón ahumado"), _food("hígado de cordero")
    assert promoted_ids([usado, ignorado], {usado.id}) == [usado.id]


@pytest.mark.parametrize("nota", ["", "   ", "\n"])
async def test_una_nota_vacia_devuelve_el_pool_tal_cual(nota) -> None:
    base = [_food("pechuga de pollo")]
    repo = _FakeFoodRepo([_food("salmón ahumado")])
    assert await pool_for_note(note=nota, base=base, food_repo=repo, restrictions=[]) == base
