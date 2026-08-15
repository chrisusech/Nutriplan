"""El importador del catálogo USDA, con un bulk de juguete.

No es código de runtime —la app nunca lee este staging— pero es de donde salen
los números del catálogo, así que un error aquí se propaga a todos los planes.
"""

import csv
from pathlib import Path

import pytest

from nutriplan.adapters.food.usda_fdc import (
    build_sqlite,
    connect,
    get,
    normalize_en,
    search,
    stream_foods,
)

# Filas del bulk real, recortadas a lo que el importador mira.
FOODS = [
    (171077, "sr_legacy_food", "Cheese, cheddar", "1"),
    (171705, "foundation_food", "Chicken, broilers, breast, meat only, cooked, roasted", "5"),
    (168409, "survey_fndds_food", "Rice, white, cooked", "9"),
    (999999, "branded_food", "Choco Puffs Cereal", "18"),
]
# (fdc_id, nutrient_id, amount)
NUTRIENTS = [
    (171077, 1008, "403"),
    (171077, 1003, "22.9"),
    (171077, 1005, "3.1"),
    (171077, 1004, "33.1"),
    (171077, 1093, "653"),
    # El pollo no trae 1008: solo la energía de Atwater.
    (171705, 2047, "165"),
    (171705, 1003, "31.0"),
    (171705, 1004, "3.6"),
    (168409, 1008, "130"),
    (168409, 1005, "28.2"),
    (168409, 1079, "0.4"),
    (168409, 2000, ""),  # amount vacío: se ignora
    (999999, 1008, "500"),  # branded: ni siquiera debería mirarse
]


@pytest.fixture
def bulk(tmp_path: Path) -> Path:
    with (tmp_path / "food.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fdc_id", "data_type", "description", "food_category_id"])
        w.writerows(FOODS)
    with (tmp_path / "food_nutrient.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fdc_id", "nutrient_id", "amount"])
        w.writerows(NUTRIENTS)
    return tmp_path


# --- Normalizar -------------------------------------------------------------


def test_los_sufijos_de_preparacion_de_usda_no_ensucian_el_nombre() -> None:
    assert normalize_en("Chicken, breast, raw") == "chicken breast"


def test_el_ingles_no_se_singulariza_como_si_fuera_espanol() -> None:
    """`domain.food_matching.normalize` convertiría "cheese" en "chees"."""
    assert normalize_en("Cheese") == "cheese"


def test_los_acentos_y_la_puntuacion_se_van() -> None:
    assert normalize_en("Crème, fraîche (cultured)") == "creme fraiche cultured"


# --- Leer el bulk -----------------------------------------------------------


def test_los_productos_de_marca_no_entran_al_catalogo(bulk) -> None:
    """Dos millones de productos de EE.UU. no sirven para un plan latino."""
    ids = {f.fdc_id for f in stream_foods(bulk)}
    assert 999999 not in ids
    assert len(ids) == 3


def test_un_alimento_llega_con_sus_macros(bulk) -> None:
    queso = next(f for f in stream_foods(bulk) if f.fdc_id == 171077)
    assert queso.kcal_100g == 403
    assert queso.protein_100g == 22.9
    assert queso.sodium_mg_100g == 653


def test_lo_que_no_trae_kcal_directas_las_toma_de_atwater(bulk) -> None:
    """Varios `foundation_food` solo traen la energía derivada. Sin este
    respaldo se quedarían sin kcal y no podrían entrar a un plan."""
    pollo = next(f for f in stream_foods(bulk) if f.fdc_id == 171705)
    assert pollo.kcal_100g == 165


def test_lo_que_no_declara_fibra_o_azucar_se_cuenta_como_cero(bulk) -> None:
    """`None` obligaría a comprobar en cada suma; cero es el dato real."""
    queso = next(f for f in stream_foods(bulk) if f.fdc_id == 171077)
    assert queso.fiber_100g == 0.0
    assert queso.sugar_100g == 0.0


def test_una_cantidad_vacia_no_se_lee_como_cero_falso(bulk) -> None:
    arroz = next(f for f in stream_foods(bulk) if f.fdc_id == 168409)
    assert arroz.sugar_100g == 0.0
    assert arroz.fiber_100g == 0.4


def test_sin_el_bulk_descargado_se_dice_que_falta_y_donde(bulk, tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="food.csv"):
        list(stream_foods(tmp_path / "vacio"))


# --- El staging -------------------------------------------------------------


def test_construir_el_staging_deja_dentro_los_alimentos_utiles(bulk, tmp_path) -> None:
    total = build_sqlite(bulk, tmp_path / "st" / "usda.db")
    assert total == 3


def test_reconstruir_no_duplica(bulk, tmp_path) -> None:
    """El bulk se reimporta cada vez que USDA publica: si acumulara, el
    catálogo tendría cada alimento dos veces."""
    db = tmp_path / "usda.db"
    build_sqlite(bulk, db)
    assert build_sqlite(bulk, db) == 3


def test_buscar_sin_haber_importado_explica_que_hacer(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="nutriplan-food import-usda"):
        connect(tmp_path / "no-existe.db")


def test_se_puede_recuperar_un_alimento_por_su_fdc_id(bulk, tmp_path) -> None:
    """El `fdc_id` es el ancla: es lo que se versiona en el catálogo curado."""
    db = tmp_path / "usda.db"
    build_sqlite(bulk, db)
    with connect(db) as conn:
        assert get(conn, 171077).description == "Cheese, cheddar"
        assert get(conn, 123) is None


# --- Buscar candidatos ------------------------------------------------------


def test_el_nombre_curado_encuentra_su_descripcion_larga_de_usda(bulk, tmp_path) -> None:
    """ "chicken breast" tiene que ganar contra la descripción kilométrica que
    lo contiene, que es el caso normal del catálogo."""
    db = tmp_path / "usda.db"
    build_sqlite(bulk, db)
    with connect(db) as conn:
        mejor, score = search(conn, "chicken breast")[0]
    assert mejor.fdc_id == 171705
    assert score > 0.6


def test_una_busqueda_vacia_no_devuelve_nada(bulk, tmp_path) -> None:
    db = tmp_path / "usda.db"
    build_sqlite(bulk, db)
    with connect(db) as conn:
        assert search(conn, "   ") == []


def test_si_el_prefiltro_no_deja_nada_igual_se_busca_en_todo(bulk, tmp_path) -> None:
    """Devolver vacío obligaría a enlazar ese alimento a mano."""
    db = tmp_path / "usda.db"
    build_sqlite(bulk, db)
    with connect(db) as conn:
        assert search(conn, "queso manchego") != []
