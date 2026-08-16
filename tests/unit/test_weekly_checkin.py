"""Check-in semanal: gate, semana ISO y semilla de onboarding."""

from datetime import date, datetime
from uuid import uuid4

import pytest

from nutriplan.application.dish_recipes import RECIPE_PROMPT_VERSION, _prompt_for
from nutriplan.application.weekly_checkin import (
    WEIGHT_MAX_KG,
    iso_week_start,
    needs_weekly_checkin,
    seed_weight_from_profile,
    submit_weekly_checkin,
    week_closure,
)
from nutriplan.domain.dish_recipe import GeneratedRecipe
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MacroTargets,
    MealEntry,
    MealSlot,
    Sex,
)
from nutriplan.domain.week_close import RATINGS_REQUIRED


def test_el_lunes_de_la_semana_iso_es_el_inicio() -> None:
    # Miércoles 12 ago 2026 → lunes 10.
    assert iso_week_start(date(2026, 8, 12)) == date(2026, 8, 10)
    assert iso_week_start(datetime(2026, 8, 10, 23, 0)) == date(2026, 8, 10)


def test_hoy_viernes_no_es_el_lunes() -> None:
    from nutriplan.domain.week import today_weekday

    assert today_weekday(date(2026, 8, 14)) == 4


def test_el_prompt_de_receta_no_pide_inventar_gramos() -> None:
    meal = MealEntry(
        slot=MealSlot.LUNCH,
        dish_name="Pollo con arroz",
        template_id="proteina_carbo_ensalada",
        items=[],
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0, fiber_g=0),
    )
    text = _prompt_for(meal, {})
    assert "juzga si el plato es adecuado" in text
    assert "name_es culinario" in text
    assert RECIPE_PROMPT_VERSION == 4
    fields = GeneratedRecipe.model_fields
    assert "steps" in fields
    assert "adequacy" in fields
    assert "name_es" in fields
    assert "kcal" not in fields
    assert "grams" not in fields


class _FakeWeights:
    def __init__(self) -> None:
        self.entries: dict[tuple, object] = {}

    async def upsert(self, entry):  # type: ignore[no-untyped-def]
        self.entries[(entry.client_id, entry.week_start)] = entry
        return entry

    async def for_week(self, client_id, week_start):  # type: ignore[no-untyped-def]
        return self.entries.get((client_id, week_start))

    async def latest(self, client_id):  # type: ignore[no-untyped-def]
        return None

    async def previous_before(self, client_id, week_start):  # type: ignore[no-untyped-def]
        return None


def _client(**kw: object) -> Client:
    base: dict[str, object] = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Luis",
        sex=Sex.MALE,
        age_years=28,
        height_cm=178.0,
        weight_kg=80.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )
    base.update(kw)
    return Client(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_sin_peso_de_esta_semana_hace_falta_el_checkin() -> None:
    weights = _FakeWeights()
    client = _client()
    assert await needs_weekly_checkin(client=client, weights=weights) is True
    await seed_weight_from_profile(client=client, weights=weights)
    assert await needs_weekly_checkin(client=client, weights=weights) is False


@pytest.mark.asyncio
async def test_un_peso_fuera_de_rango_se_rechaza(nutrition_config) -> None:  # type: ignore[no-untyped-def]
    class Cfg:
        def get_nutrition_config(self):
            return nutrition_config

    class Clients:
        async def update(self, client):
            return None

    class Targets:
        async def latest_for_client(self, client_id):
            return None

        async def add(self, targets):
            return None

    with pytest.raises(ValidationError):
        await submit_weekly_checkin(
            client=_client(),
            weight_kg=WEIGHT_MAX_KG + 10,
            client_repo=Clients(),  # type: ignore[arg-type]
            weights=_FakeWeights(),  # type: ignore[arg-type]
            targets_repo=Targets(),  # type: ignore[arg-type]
            config_provider=Cfg(),  # type: ignore[arg-type]
        )


class _FakeRatings:
    def __init__(self, n: int) -> None:
        self.n = n

    async def count_for_plan(self, plan_cycle_id) -> int:  # type: ignore[no-untyped-def]
        _ = plan_cycle_id
        return self.n


@pytest.mark.asyncio
async def test_un_plan_corto_cierra_con_tantas_notas_como_comidas() -> None:
    """No pedimos cinco estrellas si la semana solo tuvo tres platos."""
    weights = _FakeWeights()
    client = _client(active_plan_id=uuid4())
    await seed_weight_from_profile(client=client, weights=weights)
    cierre = await week_closure(
        client=client,
        weights=weights,  # type: ignore[arg-type]
        ratings=_FakeRatings(3),  # type: ignore[arg-type]
        meals_in_plan=3,
    )
    assert cierre.ratings_required == 3
    assert cierre.is_closed


@pytest.mark.asyncio
async def test_un_plan_largo_no_pide_mas_de_cinco_estrellas() -> None:
    weights = _FakeWeights()
    client = _client(active_plan_id=uuid4())
    cierre = await week_closure(
        client=client,
        weights=weights,  # type: ignore[arg-type]
        ratings=_FakeRatings(5),  # type: ignore[arg-type]
        meals_in_plan=35,
    )
    assert cierre.ratings_required == RATINGS_REQUIRED
    assert not cierre.is_closed  # falta el peso
