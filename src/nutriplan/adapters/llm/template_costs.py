"""Lo que le cuesta al motor cada decisión, y por qué vale lo que vale.

Son los números con los que el motor de platos compara alternativas: repetir un
alimento, mandar pan a la cena, pasarse de grasa. Viven aparte del selector
porque no son código sino calibración — cada uno se movió por un menú concreto
que salía mal, y esa historia es el comentario que lo acompaña. Cambiar uno
cambia lo que la gente come; leerlos junto al algoritmo escondía cuál era cuál.

Las magnitudes están ordenadas a propósito: prohibiciones de facto arriba
(decenas de miles), preferencias abajo (decenas). Al tocar uno, mirar dónde cae
respecto a los demás importa más que su valor absoluto.
"""

from nutriplan.domain.models import (
    DEFAULT_SLOT_WEIGHT,
    FoodCategory,
    FoodItem,
    MealSlot,
)

# El suelo de porción de un acompañante que no declara el suyo. Coincide con
# `portioning.min_portion_g` de la config: por debajo, el solver no baja.
MIN_CO_PORTION_G = 20.0

# Peso de cada comida en la PROTEÍNA del día. Solo se usa si no hay config.
FALLBACK_PROTEIN_SHARE = {
    MealSlot.BREAKFAST: 0.22,
    MealSlot.SNACK_AM: 0.05,
    MealSlot.LUNCH: 0.36,
    MealSlot.SNACK_PM: 0.05,
    MealSlot.DINNER: 0.32,
}

# El orden del día. Los slots que el cliente NO come no salen del plan: el motor
# recorre los que su reparto declara.
SLOT_ORDER = [
    MealSlot.BREAKFAST,
    MealSlot.SNACK_AM,
    MealSlot.LUNCH,
    MealSlot.SNACK_PM,
    MealSlot.DINNER,
]

# Pesos del coste. El del mismo día es una prohibición de facto: repetir un
# alimento dos veces en una jornada es peor que cualquier otra cosa.
W_SAME_DAY = 10_000.0
# Pasarse del tope de repeticiones que `check_variety` va a exigir. Es casi una
# prohibición: sin este término, el motor no conoce la regla por la que lo
# evalúan, y con dos carbos de desayuno para siete días hacía 5/2 en vez de 4/3
# —una sola repetición de más— y la generación fallaba entera.
W_OVER_CAP = 5_000.0
W_TEMPLATE = 120.0  # repetir el mismo plato en la semana
W_FOOD_WEEK = 40.0  # repetir el mismo alimento en la semana, CRUZANDO SLOTS
W_ANCHOR_DAY = 80.0  # el ancla proteica del día, repetida en otro slot
W_RECENCY = 25.0  # comer hoy lo de ayer o anteayer

# La afinidad por comida (`FoodItem.weight_in`) son DOS ideas distintas, y cobrarlas
# igual fue el primer error de calibración:
#
# W_PREFERRED — "es su sitio". Entre el arroz y la papa en un almuerzo no hay drama:
#   una caricia basta para ordenarlos, y la variedad puede pasarle por encima.
#
# W_OFF_MEAL — "no es su comida": el pan y la arepa en una cena. Esto NO es una
#   preferencia, es un error de cocina, y tiene que ganarle a la variedad. Con la
#   lista corta de un cliente real (tres carbos para catorce comidas principales)
#   repetir es inevitable, y repetir arroz cuatro veces es lo que haría cualquier
#   nutricionista antes que mandar pan a la cena. Por eso 2.500 y no 30: el coste de
#   repetir un alimento es 40·n² (40, 160, 360... 1.960 a la séptima), así que el
#   motor agota los carbos buenos —hasta el techo de variedad— antes de tocar el pan.
#   Se queda por debajo de W_OVER_CAP a propósito: cuando repetir más ROMPERÍA la
#   regla de variedad, el pan vuelve a ser la salida buena.
#
# El que no declara peso (el catálogo entero, hasta ahora) paga 0. Un plato sin datos
# de afinidad cuesta lo mismo lleve dos alimentos o tres.
W_PREFERRED = 30.0
W_OFF_MEAL = 2_500.0
# Plato cuyo carbo no alcanza el objetivo (p. ej. plátano topeado a 300 g
# cuando el almuerzo pide 125 g de carbo). Preferir arroz/papa antes que
# quedar cortos y fallar la validación.
W_UNDER_CARB = 3_000.0

# Lo que la persona ya tiene en casa. Deliberadamente pequeño: desempata y
# empuja, pero no le gana ni a la variedad (W_FOOD_WEEK crece 40·n²: la segunda
# vez ya cuesta 160) ni al sentido común de cocina (W_OFF_MEAL). Tener arroz en
# la despensa no puede convertirse en siete cenas de arroz — eso sería castigar
# a quien nos cuenta lo que tiene.
W_EN_CASA = 60.0

# Un plato que ya odió dos veces. Casi una prohibición, pero por debajo de
# W_SAME_DAY: si no queda alternativa, mejor repetir un odiado que el mismo
# alimento dos veces el mismo día.
W_REJECTED = 8_000.0
W_AVOID_FOOD = 6_000.0
W_LOVED = 90.0
W_PREFER_FOOD = 50.0
# El mismo plato (o la misma plantilla) la semana pasada. Fuerte para que
# gane otra receta, por debajo de W_OFF_MEAL: si el pool es chico, aún
# puede cuadrar macros con un repetido. El mismo peso se cobra *dentro*
# de la semana: dos almuerzos que se llaman igual se leen como un fallo.
W_RECENT_WEEK = 2_400.0
W_RECENT_TEMPLATE = 400.0

# Servir un plato sin su componente opcional (el almuerzo sin aguacate). Flojo a
# propósito: la versión completa es la buena y gana por defecto, pero cuando la
# alternativa es repetir un alimento el mismo día (W_SAME_DAY, 10.000) el motor
# prefiere quitar la grasa a poner el mismo aguacate dos veces —que era lo que
# reventaba el presupuesto de grasa del día—.
W_DROPPED = 60.0

# Pasarse del presupuesto de GRASA del día. Por gramo, y caro: un día que se pasa de
# grasa no se puede arreglar porcionando —los ítems de grasa ya están en su mínimo—,
# así que el reparador recorta la proteína para bajar las kcal y la comida se queda
# sin ella. Es una restricción, no una preferencia: 400 por gramo deja que un solo
# gramo de más pese más que cualquier consideración de variedad, pero se queda por
# debajo de W_SAME_DAY para no forzar un alimento repetido en el mismo día.
W_FAT_OVER = 400.0

# Las grasas son condimento: que el aceite de oliva salga todos los almuerzos no
# es falta de variedad. Lo que define la comida son la proteína, el carbo y la fruta.
VARIETY_CATEGORIES = frozenset(
    {FoodCategory.PROTEIN, FoodCategory.DAIRY, FoodCategory.CARB, FoodCategory.FRUIT}
)


def affinity_cost(food: FoodItem, slot: MealSlot) -> float:
    """Lo que cuesta poner este alimento en esta comida. Negativo si es su sitio."""
    weight = food.weight_in(slot)
    if weight < DEFAULT_SLOT_WEIGHT:
        return W_OFF_MEAL * (DEFAULT_SLOT_WEIGHT - weight)
    return -W_PREFERRED * (weight - DEFAULT_SLOT_WEIGHT)
