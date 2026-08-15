"""Cómo se le escribe una porción a la persona que va a comérsela.

Vivían en `test_render.py` junto al PDF. El PDF ya no existe; el formato sí,
porque es lo que la app muestra en pantalla.
"""

from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.domain.portion_label import natural_units, portion_text


def test_a_quien_le_toca_huevo_se_le_dice_cuantos_no_cuantos_gramos() -> None:
    foods = catalog_by_name()
    huevo = foods["huevo entero"]  # 50 g/und, whole
    assert natural_units(100, huevo) == "2 huevos"
    assert natural_units(50, huevo) == "1 huevo"
    banano = foods["banano"]  # 120 g/und, half
    assert natural_units(60, banano) == "½ unidad"


def test_lo_que_se_pesa_en_gramos_no_finge_tener_unidades() -> None:
    foods = catalog_by_name()
    assert natural_units(60, foods["aguacate"]) is None
    assert natural_units(10, foods["aceite de oliva"]) is None
    assert natural_units(120, foods["pechuga de pollo"]) is None


def test_la_unidad_va_primero_porque_es_lo_que_la_persona_necesita_saber() -> None:
    foods = catalog_by_name()
    assert portion_text(150, foods["huevo entero"]) == "3 huevos enteros (150 g)"
    assert portion_text(100, foods["atún en agua"]) == "1 lata de atún en agua (100 g)"
    assert portion_text(120, foods["pechuga de pollo"]) == "Pechuga de pollo — 120 g"


def test_la_arepa_se_lee_con_la_marca_y_el_pita_no_pierde_el_apellido() -> None:
    # La unidad ('arepa', 'pita') no puede tragarse el resto del nombre.
    foods = catalog_by_name()
    assert portion_text(140, foods["arepa Sarys extradélgada"]) == (
        "2 arepas Sarys extradélgadas (140 g)"
    )
    assert portion_text(90, foods["pan pita integral"]) == "1½ panes pita integrales (90 g)"
