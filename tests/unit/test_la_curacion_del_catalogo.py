"""De 13.694 filas de USDA a un catálogo que alguien puede cocinar.

El reparto que fija este archivo: el código decide todo lo que se puede decidir
con reglas (estado, rol, rendimiento, porción, marcas) y audita después lo que
la IA nombró. Ningún número pasa por el modelo.
"""

import pytest

from nutriplan.adapters.food.catalog import (
    CatalogEntry,
    atwater_kcal,
    entry_id,
    validate_entries,
)
from nutriplan.adapters.food.curation import (
    build_candidates,
    derive_tags,
    is_brandish,
    pair_key,
    parse_state,
    pick_portion,
    refine_category,
    yield_factor,
)
from nutriplan.adapters.food.usda_fdc import UsdaFood, UsdaPortion
from nutriplan.domain.models import CookingMethod, FoodCategory, FoodState, MealSlot


def _usda(fdc_id: int, description: str, category: str, **kw) -> UsdaFood:
    base = dict(
        data_type="sr_legacy_food",
        description_norm=description.lower(),
        food_category=None,
        kcal_100g=130.0,
        protein_100g=2.7,
        carb_100g=28.2,
        fat_100g=0.3,
        fiber_100g=0.4,
        sugar_100g=0.0,
        sodium_mg_100g=1.0,
        category_desc=category,
    )
    base.update(kw)
    return UsdaFood(fdc_id=fdc_id, description=description, **base)


def _entry(**kw) -> CatalogEntry:
    base = dict(
        fdc_id=1,
        name_es="arroz",
        category=FoodCategory.CARB,
        kcal_100g=130,
        protein_100g=2.7,
        carb_100g=28.2,
        fat_100g=0.3,
        fiber_100g=0.4,
        meal_slots=[MealSlot.LUNCH],
    )
    base.update(kw)
    return CatalogEntry(**base)


# --- lo que el código decide solo -------------------------------------------


@pytest.mark.parametrize(
    ("descripcion", "estado", "metodo"),
    [
        ("Broccoli, raw", FoodState.RAW, None),
        ("Rice, white, long-grain, regular, enriched, cooked", FoodState.COOKED, None),
        ("Beans, snap, green, cooked, boiled, drained", FoodState.COOKED, CookingMethod.BOILED),
        ("Chicken, breast, meat only, cooked, roasted", FoodState.COOKED, CookingMethod.ROASTED),
        ("Oil, olive, salad or cooking", FoodState.NOT_APPLICABLE, None),
        ("Milk, whole, 3.25% milkfat", FoodState.NOT_APPLICABLE, None),
    ],
)
def test_el_estado_se_lee_de_la_descripcion_de_usda(descripcion, estado, metodo) -> None:
    """USDA no publica un campo «crudo/cocido»: lo escribe en el texto.

    El aceite «de ensalada o para cocinar» es la trampa: dice «cooking» y no
    está cocido.
    """
    assert parse_state(descripcion) == (estado, metodo)


def test_el_crudo_y_el_cocido_del_mismo_alimento_comparten_clave() -> None:
    """Es lo que permite emparejarlos para calcular cuánto rinde."""
    assert pair_key("Beans, snap, green, raw") == pair_key(
        "Beans, snap, green, cooked, boiled, drained, without salt"
    )


def test_lo_que_rinde_un_alimento_sale_del_agua_no_de_una_tabla_copiada() -> None:
    """Al cocinar solo entra o sale agua: la materia seca se conserva.

    De ahí que el arroz casi triplique (pierde poco, absorbe mucha) y el pollo
    encoja (suelta agua). Los dos valores son los reales de USDA.
    """
    assert yield_factor(11.6, 68.4) == pytest.approx(2.8, abs=0.05)
    assert yield_factor(74.0, 65.0) == pytest.approx(0.74, abs=0.02)


def test_un_emparejado_imposible_se_descarta_en_vez_de_publicarse() -> None:
    """Un factor de 50× solo puede venir de cruzar dos alimentos distintos."""
    assert yield_factor(99.0, 5.0) is None
    assert yield_factor(None, 60.0) is None
    assert yield_factor(50.0, 100.0) is None


def test_el_frijol_es_carbohidrato_y_el_tofu_proteina_aunque_compartan_estante() -> None:
    """USDA agrupa por origen; un plato se arma por función."""
    legumbres = "Legumes and Legume Products"
    assert refine_category(legumbres, 8.9, 23.7, 0.5) is FoodCategory.CARB
    assert refine_category(legumbres, 17.3, 2.8, 8.7) is FoodCategory.PROTEIN
    # Y la tocineta es grasa, por mucho que viva entre las carnes.
    assert refine_category("Pork Products", 12.6, 0.7, 45.0) is FoodCategory.FAT


@pytest.mark.parametrize(
    ("descripcion", "es_marca"),
    [
        ("Pillsbury, Cinnamon Rolls with Icing, refrigerated dough", True),
        ("Kraft Foods, Shake N Bake Original Recipe", True),
        ("Candies, ALMOND JOY Candy Bar", True),
        ("Rice, white, long-grain, regular, cooked", False),
        ("Cheese, Swiss", False),
        ("Beef, New Zealand, imported, flank", False),
    ],
)
def test_los_productos_de_marca_no_son_ingredientes(descripcion, es_marca) -> None:
    """El chivato es la capitalización: lo genérico solo capitaliza la primera
    palabra; lo de marca va en Title Case. Los nombres propios legítimos
    («Swiss», «New Zealand») tienen que sobrevivir."""
    assert is_brandish(descripcion) is es_marca


def test_una_taza_no_es_una_unidad_pero_una_rebanada_si() -> None:
    """El solver ya trabaja en gramos: solo interesa lo que se cuenta."""
    taza = UsdaPortion(fdc_id=1, amount=1.0, unit="cup", modifier="", gram_weight=158.0)
    rebanada = UsdaPortion(fdc_id=1, amount=1.0, unit="slice", modifier="", gram_weight=28.0)
    assert pick_portion([taza]) == (None, None)
    assert pick_portion([taza, rebanada]) == (28.0, "slice")


def test_el_filtro_deja_fuera_las_categorias_que_no_se_cocinan_en_casa() -> None:
    utiles = [
        _usda(1, "Rice, white, long-grain, cooked", "Cereal Grains and Pasta"),
        _usda(2, "Broccoli, raw", "Vegetables and Vegetable Products"),
    ]
    basura = [
        _usda(3, "Cola, carbonated", "Beverages"),
        _usda(4, "Baby food, apple", "Baby Foods"),
        _usda(5, "McDonald's hamburger", "Fast Foods"),
    ]
    nombres = {c.description for c in build_candidates(utiles + basura)}
    assert nombres == {"Rice, white, long-grain, cooked", "Broccoli, raw"}


def test_una_fila_sin_macros_no_entra_al_catalogo() -> None:
    sin = _usda(9, "Something, raw", "Vegetables and Vegetable Products", protein_100g=None)
    assert build_candidates([sin]) == []


def test_el_carbohidrato_negativo_de_usda_se_limpia_en_vez_de_reventar() -> None:
    """USDA lo calcula por diferencia; en un queso puro sale −0,5 g."""
    queso = _usda(
        10, "Cheese, cheddar", "Dairy and Egg Products", carb_100g=-0.48, protein_100g=25.0
    )
    assert build_candidates([queso])[0].carb_100g == 0.0


def test_el_arroz_cocido_hereda_el_rendimiento_de_su_hermano_crudo() -> None:
    crudo = _usda(1, "Rice, white, long-grain, raw", "Cereal Grains and Pasta", water_100g=11.6)
    cocido = _usda(2, "Rice, white, long-grain, cooked", "Cereal Grains and Pasta", water_100g=68.4)
    por_id = {c.fdc_id: c for c in build_candidates([crudo, cocido])}
    assert por_id[2].yield_factor == pytest.approx(2.8, abs=0.05)
    assert por_id[1].yield_factor is None  # el crudo ya es lo que se compra


# --- el código auditando a la IA --------------------------------------------


def test_ningun_alimento_llega_a_la_base_sin_nombre_en_español() -> None:
    """Si el modelo devuelve la descripción de USDA tal cual, no entra."""
    _ok, rechazados = validate_entries(
        [
            _entry(name_es="arroz blanco"),
            _entry(fdc_id=2, name_es="Rice, white, long-grain, cooked"),
            _entry(fdc_id=3, name_es="chicken breast, cooked"),
        ]
    )
    assert [e.name_es for e, _ in rechazados] == [
        "Rice, white, long-grain, cooked",
        "chicken breast, cooked",
    ]


def test_un_macro_que_no_cuadra_con_sus_kcal_va_a_cuarentena_no_a_la_base() -> None:
    """La coma corrida es el error que hay que atrapar: desvía un orden de
    magnitud, no un 5 %."""
    _ok, rechazados = validate_entries([_entry(kcal_100g=13, carb_100g=28.2)])
    assert "no cuadran" in rechazados[0][1]


def test_la_verdura_no_se_rechaza_por_tener_fibra() -> None:
    """La fibra va dentro del carbohidrato pero no aporta 4 kcal/g. Contarla a 4
    hacía que espinaca, brócoli y lechuga fallaran el control por ser lo que
    son."""
    espinaca = _entry(
        name_es="espinaca",
        category=FoodCategory.VEGETABLE,
        kcal_100g=23,
        protein_100g=2.9,
        carb_100g=3.6,
        fat_100g=0.4,
        fiber_100g=2.2,
    )
    # Con la fibra a 4 kcal/g darían 29,6 contra 23 declaradas: un 29 % de
    # desvío y fuera. Contándola a 2 quedan 25,2, que es ruido de redondeo.
    assert atwater_kcal(espinaca) == pytest.approx(25.2, abs=0.5)
    ok, rechazados = validate_entries([espinaca])
    assert len(ok) == 1 and not rechazados


def test_dos_filas_no_pueden_llamarse_igual_y_gana_la_del_nucleo() -> None:
    """Tres versiones de waffle congelado acaban con el mismo nombre de cocina;
    se queda la que la persona reconocería."""
    ok, rechazados = validate_entries(
        [
            _entry(fdc_id=1, name_es="waffle", engine_default=False),
            _entry(fdc_id=2, name_es="waffle", engine_default=True),
        ]
    )
    assert [e.fdc_id for e in ok] == [2]
    assert len(rechazados) == 1


def test_un_factor_de_rendimiento_en_un_alimento_crudo_es_un_error() -> None:
    _ok, rechazados = validate_entries([_entry(state=FoodState.RAW, yield_factor=2.8)])
    assert "cocido" in rechazados[0][1]


def test_lo_que_se_cuenta_por_unidades_tiene_que_decir_cuanto_pesa_una() -> None:
    """Si no, el plan pide «2 huevos» sin saber traducirlo a gramos."""
    _ok, rechazados = validate_entries([_entry(unit_granularity="whole", default_unit_g=None)])
    assert "cuánto pesa una" in rechazados[0][1]


def test_los_alimentos_libres_no_pasan_por_el_control_de_macros() -> None:
    """La ensalada libre tiene 0 kcal a propósito: el solver ni la mira."""
    ok, _rej = validate_entries(
        [
            _entry(
                name_es="ensalada libre",
                category=FoodCategory.OTHER,
                kcal_100g=0,
                protein_100g=0,
                carb_100g=0,
                fat_100g=0,
                fiber_100g=0,
                is_free=True,
                free_text="ensalada a voluntad",
            )
        ]
    )
    assert len(ok) == 1


def test_renombrar_un_alimento_no_lo_convierte_en_otro() -> None:
    """El id se ancla en USDA y en el estado, no en el nombre.

    Es el bug que hubo que migrar: la PK salía del `name_es`, así que corregir
    «tofu firme» → «tofu» creaba un alimento nuevo y dejaba huérfanas las
    preferencias, la despensa y los platos ya generados.
    """
    antes = entry_id(171477, FoodState.COOKED, "pechuga de pollo")
    despues = entry_id(171477, FoodState.COOKED, "pechuga de pollo asada")
    assert antes == despues
    # Pero el crudo y el cocido del mismo fdc_id sí son alimentos distintos.
    assert entry_id(171477, FoodState.RAW, "pechuga de pollo") != antes


@pytest.mark.parametrize(
    ("descripcion", "categoria", "prohibido"),
    [
        # «egg» por subcadena marcaba la berenjena como huevo, y quien no come
        # huevo se quedaba sin berenjena. Un tag mal puesto es una restricción
        # mal aplicada, y eso llega al plato de alguien.
        ("Eggplant, cooked, boiled, drained", "Vegetables and Vegetable Products", "huevo"),
        ("Squash, winter, butternut, baked", "Vegetables and Vegetable Products", "frutos_secos"),
        ("Doughnuts, french crullers, glazed", "Baked Products", "frutos_secos"),
        # «oyster» es también un corte del avestruz. Fuera de la pescadería esas
        # palabras significan otra cosa.
        ("Ostrich, oyster, cooked", "Poultry Products", "mariscos"),
    ],
)
def test_un_tag_no_se_pega_por_parecerse_a_una_palabra(descripcion, categoria, prohibido) -> None:
    assert prohibido not in derive_tags(descripcion, categoria)


@pytest.mark.parametrize(
    ("descripcion", "categoria", "esperado"),
    [
        ("Crustaceans, shrimp, cooked", "Finfish and Shellfish Products", "mariscos"),
        ("Fish, salmon, Atlantic, raw", "Finfish and Shellfish Products", "pescado"),
        ("Egg, whole, raw", "Dairy and Egg Products", "huevo"),
        ("Bread, white wheat", "Baked Products", "gluten"),
        ("Tofu, raw, firm", "Legumes and Legume Products", "soya"),
        ("Nuts, almonds", "Nut and Seed Products", "frutos_secos"),
    ],
)
def test_las_restricciones_de_verdad_si_se_marcan(descripcion, categoria, esperado) -> None:
    """Afinar el matcher no puede dejar sin tag lo que sí lo necesita."""
    assert esperado in derive_tags(descripcion, categoria)
