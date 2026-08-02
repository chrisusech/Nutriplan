"""El conjunto de alimentos con el que se le puede armar el menú a alguien.

Vivía copiado en cuatro sitios (generar, editar y dos pantallas del generador),
lo que hizo que "sorpréndeme" funcionara en uno y fallara en los otros tres.
"""

from uuid import UUID

from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.models import Client, FoodCategory, FoodItem
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.repository import ClientRepository


def matches_dislike(food: FoodItem, disliked: set[str]) -> bool:
    """Un dislike es texto libre: casa contra el nombre y contra los alias."""
    haystack = {food.name_es.lower(), *(a.lower() for a in food.aliases)}
    return any(d in h or h in d for d in disliked for h in haystack)


async def resolve_allowed_foods(
    client: Client, *, food_repo: FoodRepository, client_repo: ClientRepository
) -> list[FoodItem]:
    """Preferencias ∩ no-restringidos ∩ no-vetados ∩ no-rechazados.

    Sin alimentos marcados la respuesta no es "nada", es "sorpréndeme": se abre
    el catálogo entero y lo recortan las restricciones. Obligar a marcar decenas
    de ingredientes era justo lo que agobiaba en el onboarding viejo.
    """
    liked = (
        await food_repo.get_by_ids(client.liked_food_ids)
        if client.liked_food_ids
        else await food_repo.list_universe()
    )
    disliked = {d.strip().lower() for d in client.dislikes if d.strip()}
    if disliked:
        liked = [f for f in liked if not matches_dislike(f, disliked)]
    banned: set[UUID] = set(await client_repo.list_banned_food_ids(client.id))
    return allowed_foods(liked, client.restrictions, banned)


# Cuántos alimentos de cada categoría se le enseñan al modelo. El catálogo
# entero son 172: mandarlos gasta ~6.400 tokens de entrada, y el nivel gratis de
# Groq da 8.000 POR MINUTO. Con esto una selección cabe de sobra y queda cuota
# para el crítico y las recetas.
LLM_SHORTLIST: dict[FoodCategory, int] = {
    FoodCategory.PROTEIN: 10,
    FoodCategory.CARB: 8,
    FoodCategory.FAT: 5,
    FoodCategory.FRUIT: 6,
    FoodCategory.DAIRY: 5,
    FoodCategory.VEGETABLE: 5,
    FoodCategory.OTHER: 3,
}


def shortlist_for_llm(
    allowed: list[FoodItem], *, liked: set[UUID] | None = None
) -> list[FoodItem]:
    """Un catálogo corto y equilibrado para enseñarle al modelo.

    El modelo no necesita ver todo lo que existe: necesita suficiente variedad
    en cada rol para armar siete días distintos. Se prioriza lo que la persona
    marcó y luego se completa por orden alfabético, que es estable — el mismo
    perfil recibe el mismo catálogo y la caché por `input_hash` sigue sirviendo.

    Los alimentos libres entran siempre: no gastan casi tokens y sin ellos se
    pierden la ensalada y el café.
    """
    liked = liked or set()
    by_category: dict[FoodCategory, list[FoodItem]] = {}
    free: list[FoodItem] = []
    for food in sorted(allowed, key=lambda f: (f.id not in liked, f.name_es)):
        if food.is_free:
            free.append(food)
            continue
        by_category.setdefault(food.category, []).append(food)

    picked: list[FoodItem] = []
    for category, limit in LLM_SHORTLIST.items():
        picked.extend(by_category.get(category, [])[:limit])
    picked.extend(free)
    # Si el recorte dejara un rol sin nada, es mejor el pool entero que un menú
    # que no cuadra: el coste de tokens no vale un fallo de generación.
    return picked or allowed
