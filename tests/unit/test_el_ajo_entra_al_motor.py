"""El ajo no se marca en el onboarding, pero el motor sí lo usa."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    FoodCategory,
    FoodItem,
    Goal,
    Sex,
    UnitGranularity,
)


class _Foods:
    def __init__(self, foods: list[FoodItem]) -> None:
        self.foods = foods

    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]:
        wanted = set(food_ids)
        return [f for f in self.foods if f.id in wanted]

    async def list_universe(self) -> list[FoodItem]:
        return list(self.foods)


class _Clients:
    def __init__(self) -> None:
        self.banned: set[UUID] = set()

    async def list_banned_food_ids(self, client_id: UUID) -> list[UUID]:
        _ = client_id
        return list(self.banned)


def _food(nombre: str, *tags: str) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="USDA",
        name_es=nombre,
        category=FoodCategory.VEGETABLE if "condimento" in tags else FoodCategory.PROTEIN,
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


@pytest.mark.asyncio
async def test_quien_marco_pollo_igual_recibe_ajo_en_el_motor() -> None:
    pollo = _food("pechuga de pollo")
    ajo = _food("ajo", "condimento")
    client = _client(liked_food_ids=[pollo.id])
    pool = await resolve_allowed_foods(
        client,
        food_repo=_Foods([pollo, ajo]),
        client_repo=_Clients(),  # type: ignore[arg-type]
    )
    assert {f.name_es for f in pool} == {"pechuga de pollo", "ajo"}


@pytest.mark.asyncio
async def test_quien_veta_el_ajo_no_lo_vuelve_a_ver() -> None:
    pollo = _food("pechuga de pollo")
    ajo = _food("ajo", "condimento")
    clients = _Clients()
    clients.banned.add(ajo.id)
    client = _client(liked_food_ids=[pollo.id])
    pool = await resolve_allowed_foods(
        client,
        food_repo=_Foods([pollo, ajo]),
        client_repo=clients,  # type: ignore[arg-type]
    )
    assert {f.name_es for f in pool} == {"pechuga de pollo"}
