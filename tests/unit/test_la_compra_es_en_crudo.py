"""Nadie compra arroz cocido.

El plan se calcula y se guarda en gramos cocidos —es lo que va al plato y sobre
lo que cuadran los macros— pero la lista de compra tiene que hablar el idioma
del súper. Estos tests fijan esa traducción y, sobre todo, fijan que no se
invente cuando no la sabe.
"""

import pytest
from tests.fixtures.foods import catalog_by_name

from nutriplan.domain.cocina import convierte_a_crudo, gramos_en_crudo
from nutriplan.domain.models import FoodCategory, FoodItem, FoodState


@pytest.fixture(scope="module")
def catalogo() -> dict[str, FoodItem]:
    return catalog_by_name()


def test_el_arroz_cocido_del_menu_se_compra_en_crudo(catalogo) -> None:
    """150 g de arroz en el plato son ~54 g de arroz en la bolsa."""
    arroz = catalogo["arroz blanco cocido"]
    assert arroz.state is FoodState.COOKED
    assert arroz.yield_factor == pytest.approx(2.8, abs=0.2)

    comprar = gramos_en_crudo(arroz, 150)
    assert comprar == pytest.approx(53.6, abs=1.0)
    # Y sobre todo: mucho menos que lo que dice el menú. Comprar 150 g de arroz
    # para servir 150 g cocidos era el error de siempre.
    assert comprar < 150 / 2


def test_la_carne_encoge_asi_que_hay_que_comprar_mas_de_la_que_se_sirve(catalogo) -> None:
    """Al revés que el grano: 150 g de res servidos salen de ~200 g crudos."""
    res = catalogo["carne de res magra"]
    assert res.yield_factor is not None and res.yield_factor < 1
    assert gramos_en_crudo(res, 150) > 150


def test_un_alimento_sin_par_crudo_no_miente_con_los_gramos(catalogo) -> None:
    """Sin factor no se convierte. Preferimos quedarnos cortos a inventar.

    USDA no publica el crudo de todos los cocidos, y adivinar un factor es peor
    que no tenerlo: quien pesa la compra confía en el número.
    """
    huevo = catalogo["huevo entero"]
    assert huevo.yield_factor is None
    assert gramos_en_crudo(huevo, 100) == 100
    assert not convierte_a_crudo(huevo)

    inventado = FoodItem(
        id=huevo.id,
        source="curated",
        name_es="algo cocido sin par",
        category=FoodCategory.CARB,
        kcal_100g=100,
        protein_100g=2,
        carb_100g=20,
        fat_100g=1,
        state=FoodState.COOKED,
    )
    assert gramos_en_crudo(inventado, 200) == 200


def test_lo_que_no_se_cocina_pesa_igual_en_el_plato_que_en_el_carrito(catalogo) -> None:
    """El aguacate y el aceite no cambian de peso: el interruptor no aplica."""
    for nombre in ("aguacate", "aceite de oliva", "avena en hojuelas"):
        food = catalogo[nombre]
        assert not convierte_a_crudo(food), nombre
        assert gramos_en_crudo(food, 42) == 42, nombre


def test_el_factor_de_rendimiento_de_las_lentejas_es_creible(catalogo) -> None:
    """Los granos secos casi triplican al hidratarse; nada rinde diez veces.

    El factor sale del agua que publica USDA, no de una tabla copiada a mano:
    si alguna vez sale de este rango, el emparejado crudo↔cocido se rompió.
    """
    for nombre in ("lentejas cocidas", "frijoles negros cocidos", "quinoa cocida"):
        food = catalogo[nombre]
        assert food.yield_factor is not None, nombre
        assert 2.0 <= food.yield_factor <= 3.6, f"{nombre}: rinde {food.yield_factor}"
