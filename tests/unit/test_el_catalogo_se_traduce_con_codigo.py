"""El nombre en español sale de un léxico, no de un modelo.

USDA escribe códigos de inventario con vocabulario controlado («Beef, chuck, arm
pot roast, separable lean only, trimmed to 1/8" fat, choice, cooked, braised»).
Eso lo cubre un diccionario: 641 cabezas para 5.374 filas. Un léxico corre en un
segundo, cuesta cero, da el mismo resultado siempre y se puede leer en un test —
tres cosas que una llamada a una IA no da.
"""

import pytest

from nutriplan.adapters.food.curation import es_ingrediente
from nutriplan.adapters.food.traductor import aliases_de, nombre_es


@pytest.mark.parametrize(
    ("descripcion", "esperado"),
    [
        # El corte manda sobre el animal, y el ruido de despiece se tira entero.
        (
            'Beef, chuck, arm pot roast, separable lean only, trimmed to 1/8" fat, '
            "choice, cooked, braised",
            "paleta de res",
        ),
        ("Chicken, broilers or fryers, breast, meat only, cooked, roasted", "pechuga de pollo"),
        ("Pork, fresh, loin, tenderloin, separable lean only, cooked", "solomillo de cerdo"),
        # La especie manda sobre la familia: esto no es «pescado», es salmón.
        ("Fish, salmon, Atlantic, wild, raw", "salmón"),
        ("Crustaceans, shrimp, mixed species, cooked, moist heat", "camarón"),
        ("Mollusks, squid, mixed species, raw", "calamar"),
        ("Nuts, almond butter, plain, without salt added", "mantequilla de almendras"),
        ("Cheese, cheddar", "queso cheddar"),
        ("Beans, black, mature seeds, cooked, boiled, without salt", "frijol negro"),
        ("Oil, olive, salad or cooking", "aceite de oliva"),
        ("Egg, yolk, raw, fresh", "yema de huevo"),
        # Y los calificadores que sí distinguen algo se conservan.
        ("Rice, brown, long-grain, cooked", "arroz integral"),
        ("Milk, reduced fat, fluid, 2% milkfat", "leche semidescremada"),
    ],
)
def test_una_descripcion_de_usda_se_lee_como_un_nombre_de_cocina(descripcion, esperado) -> None:
    assert nombre_es(descripcion) == esperado


@pytest.mark.parametrize(
    "descripcion",
    [
        "Beef, retail cuts, separable fat, cooked",
        "Pork, fresh, variety meats and by-products, chitterlings, raw",
        "Fish, bluefish, raw",  # una especie que el léxico no conoce
        "Oil, flaxseed, cold pressed",
        "Cheese, pasteurized process, American, low fat",
    ],
)
def test_una_familia_sin_corte_ni_especie_no_recibe_un_nombre_generico(descripcion) -> None:
    """«Beef» a secas no es un alimento.

    Las 150 filas que caerían bajo «carne de res» van de 128 a 731 kcal. Darle
    ese nombre a una cualquiera haría que el motor sirviera sebo llamándolo
    carne. Sin corte reconocido, la fila se va a cuarentena.
    """
    assert nombre_es(descripcion) is None


def test_las_concordancias_de_genero_no_se_rompen() -> None:
    """«lechuga rojo» no es español, y el catálogo lo lee una persona."""
    assert nombre_es("Lettuce, leaf, red, raw") == "lechuga roja"
    assert nombre_es("Soybeans, mature seeds, sprouted, raw") == "soya germinada"
    assert nombre_es("Radishes, oriental, dried") == "rábano seco"
    assert nombre_es("Milk, whole, 3.25% milkfat") == "leche entera"


def test_gana_el_corte_mas_especifico() -> None:
    """«tenderloin» y «loin» están los dos en la descripción; manda el fino."""
    assert nombre_es("Pork, fresh, loin, tenderloin, cooked") == "solomillo de cerdo"


def test_una_cabeza_que_no_esta_en_el_lexico_no_se_adivina() -> None:
    """Devolver None manda la fila a cuarentena, que es lo correcto: un catálogo
    con «Butterbur, raw» dentro es peor que un catálogo más corto."""
    assert nombre_es("Butterbur, (fuki), raw") is None
    assert nombre_es("Sesbania flower, raw") is None
    assert nombre_es("") is None


@pytest.mark.parametrize(
    ("descripcion", "es_comida"),
    [
        ("Beef, retail cuts, separable fat, cooked", False),
        ("Chicken, mechanically separated, raw", False),
        ("Pork, fresh, variety meats and by-products, brain, raw", False),
        ("Beef, loin, tenderloin, cooked", True),
        ("Broccoli, raw", True),
    ],
)
def test_los_despojos_de_la_despiece_no_son_ingredientes(descripcion, es_comida) -> None:
    """Un recorte de grasa de 731 kcal no es carne, y competía por su nombre."""
    assert es_ingrediente(descripcion) is es_comida


def test_los_regionalismos_viajan_como_alias() -> None:
    """Quien escribe «palta» tiene que encontrar el aguacate."""
    assert "palta" in aliases_de("aguacate")
    assert "caraota" in aliases_de("frijol")
    assert aliases_de("salmón") == []


def test_el_lexico_es_estable() -> None:
    """Dos corridas dan lo mismo: es un diccionario, no una muestra."""
    descripcion = "Beef, chuck, arm pot roast, separable lean only, cooked, braised"
    assert nombre_es(descripcion) == nombre_es(descripcion)
