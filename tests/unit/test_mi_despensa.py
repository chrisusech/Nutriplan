"""Quitar y añadir alimentos: la despensa que la persona sí controla.

La trampa que estas historias fijan: una lista de preferencias vacía significa
«sorpréndeme», así que añadir un alimento a una lista vacía la dejaría comiendo
una sola cosa.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from nutriplan.application.my_foods import add_food, addable, remove_food
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    FoodCategory,
    FoodItem,
    Goal,
    Sex,
    UnitGranularity,
)


class _Clients:
    """Lo justo del repositorio: qué se vetó, qué se levantó y qué se guardó."""

    def __init__(self) -> None:
        self.banned: set[UUID] = set()
        self.updated: list[Client] = []

    async def ban_food(self, client_id: UUID, food_id: UUID) -> None:
        self.banned.add(food_id)

    async def unban_food(self, client_id: UUID, food_id: UUID) -> None:
        self.banned.discard(food_id)

    async def update(self, client: Client) -> None:
        self.updated.append(client)


def _food(nombre: str, *tags: str) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="USDA",
        name_es=nombre,
        category=FoodCategory.PROTEIN,
        kcal_100g=100.0,
        protein_100g=20.0,
        carb_100g=0.0,
        fat_100g=2.0,
        unit_granularity=UnitGranularity.GRAMS,
        tags=list(tags),
    )


def _client(**kw: object) -> Client:
    base: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Ana",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )
    base.update(kw)
    return Client(**base)  # type: ignore[arg-type]


async def test_lo_que_quita_queda_vetado() -> None:
    repo = _Clients()
    pescado = _food("tilapia")
    await remove_food(client=_client(), food=pescado, client_repo=repo)  # type: ignore[arg-type]
    assert pescado.id in repo.banned


async def test_quitar_algo_que_habia_marcado_como_favorito_lo_saca_de_favoritos() -> None:
    """Si no, el veto y las preferencias se contradicen y gana la confusión."""
    repo = _Clients()
    pescado = _food("tilapia")
    otro = _food("pechuga de pollo")
    client = _client(liked_food_ids=[pescado.id, otro.id])

    await remove_food(client=client, food=pescado, client_repo=repo)  # type: ignore[arg-type]

    assert repo.updated[-1].liked_food_ids == [otro.id]


async def test_quitar_algo_que_no_era_favorito_no_reescribe_el_perfil() -> None:
    repo = _Clients()
    await remove_food(client=_client(), food=_food("tilapia"), client_repo=repo)  # type: ignore[arg-type]
    assert repo.updated == []


async def test_lo_que_vuelve_a_anadir_deja_de_estar_vetado() -> None:
    repo = _Clients()
    pescado = _food("tilapia")
    client = _client()
    await remove_food(client=client, food=pescado, client_repo=repo)  # type: ignore[arg-type]

    await add_food(client=client, food=pescado, client_repo=repo)  # type: ignore[arg-type]

    assert pescado.id not in repo.banned


async def test_anadir_algo_que_dijo_que_no_queria_ver_borra_esa_frase() -> None:
    """Levantar el veto sin tocar los dislikes lo dejaría fuera igualmente."""
    repo = _Clients()
    pescado = _food("tilapia")
    client = _client(dislikes=["tilapia", "brócoli"])

    await add_food(client=client, food=pescado, client_repo=repo)  # type: ignore[arg-type]

    assert repo.updated[-1].dislikes == ["brócoli"]


async def test_quien_dijo_sorprendeme_no_se_queda_comiendo_una_sola_cosa() -> None:
    """Sin preferencias el pool es el catálogo entero: añadir no lo recorta."""
    repo = _Clients()
    pollo = _food("pechuga de pollo")
    client = _client(liked_food_ids=[])

    await add_food(client=client, food=pollo, client_repo=repo)  # type: ignore[arg-type]

    assert client.liked_food_ids == []
    assert repo.updated == []


async def test_quien_si_eligio_alimentos_ve_el_nuevo_en_su_lista() -> None:
    repo = _Clients()
    pollo = _food("pechuga de pollo")
    huevo = _food("huevo entero")
    client = _client(liked_food_ids=[huevo.id])

    await add_food(client=client, food=pollo, client_repo=repo)  # type: ignore[arg-type]

    assert repo.updated[-1].liked_food_ids == [huevo.id, pollo.id]


def test_solo_se_ofrece_anadir_lo_que_todavia_no_come() -> None:
    pollo = _food("pechuga de pollo")
    huevo = _food("huevo entero")
    ofrecidos = addable(universe=[pollo, huevo], allowed=[pollo], restrictions=[])
    assert [f.id for f in ofrecidos] == [huevo.id]


def test_una_restriccion_declarada_no_se_levanta_desde_la_despensa() -> None:
    """Se declaró en el perfil y se cambia en el perfil, no de refilón."""
    gamba = _food("camarón", "mariscos")
    pollo = _food("pechuga de pollo")
    ofrecidos = addable(universe=[pollo, gamba], allowed=[pollo], restrictions=["no_seafood"])
    assert gamba.id not in {f.id for f in ofrecidos}
