"""Quitar y añadir alimentos de la despensa de una persona.

La despensa no es una tabla: es el resultado de cruzar preferencias, veto y
restricciones (`resolve_allowed_foods`). Por eso "quitar" y "añadir" no son un
`UPDATE` sobre una lista, sino dos operaciones que tienen que dejar el cruce
diciendo lo que la persona pidió — incluida la trampa de que una lista de
preferencias vacía significa "sorpréndeme", no "nada".
"""

from uuid import UUID

from nutriplan.application.food_pool import matches_dislike
from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.models import Client, FoodItem
from nutriplan.ports.repository import ClientRepository


async def remove_food(*, client: Client, food: FoodItem, client_repo: ClientRepository) -> None:
    """Fuera del menú: se veta y se cae de las preferencias.

    El veto es lo que manda, porque el pool se completa solo cuando un slot no
    cuadra (`supplement_pool_for_targets`) y esa puerta trasera solo respeta la
    lista de vetados.
    """
    await client_repo.ban_food(client.id, food.id)
    if food.id in client.liked_food_ids:
        client.liked_food_ids = [f for f in client.liked_food_ids if f != food.id]
        await client_repo.update(client)


async def add_food(*, client: Client, food: FoodItem, client_repo: ClientRepository) -> None:
    """De vuelta al menú: se levanta el veto y se destraba lo que lo excluía.

    Si la persona tiene preferencias explícitas hay que meterlo ahí o el pool
    seguiría sin verlo. Si no las tiene, el pool ya es el catálogo entero y
    añadirlo a una lista vacía lo dejaría comiendo una sola cosa.
    """
    await client_repo.unban_food(client.id, food.id)
    changed = False

    surviving = [d for d in client.dislikes if not matches_dislike(food, {d.lower()})]
    if len(surviving) != len(client.dislikes):
        client.dislikes = surviving
        changed = True

    if client.liked_food_ids and food.id not in client.liked_food_ids:
        client.liked_food_ids = [*client.liked_food_ids, food.id]
        changed = True

    if changed:
        await client_repo.update(client)


def addable(
    *, universe: list[FoodItem], allowed: list[FoodItem], restrictions: list[str]
) -> list[FoodItem]:
    """Lo que se le puede ofrecer añadir: el catálogo menos lo que ya come.

    Las restricciones no se ofrecen para desactivar aquí. Se declararon en el
    perfil y se cambian en el perfil; una pantalla de alimentos que las levanta
    de refilón es como se cuela el gluten en el menú de alguien celíaco.
    """
    have: set[UUID] = {f.id for f in allowed}
    return [f for f in allowed_foods(universe, restrictions, set()) if f.id not in have]
