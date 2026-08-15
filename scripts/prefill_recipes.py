"""Calienta la caché de recetas generando un menú de referencia.

Las plantillas YAML ya aportan pasos al vuelo. Este script genera un menú con
el motor determinista y pide las recetas (estáticas primero; IA si hay clave),
de modo que los primeros usuarios de la beta no paguen la latencia en frío.

    env LLM_API_KEY=… uv run python scripts/prefill_recipes.py
"""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.dish_recipes import recipes_for_week
from nutriplan.application.generate_plan import generate_plan_for_client
from nutriplan.config.settings import get_settings
from nutriplan.container import build_container
from nutriplan.domain.meal_template import static_recipes
from nutriplan.domain.models import ActivityLevel, Client, Goal, MealSlot, Sex


async def main() -> int:
    settings = get_settings()
    container = build_container()
    await upgrade_to_head_async(settings.database_url)

    async with container.session_factory() as session:
        csv = settings.project_root / "data" / "foods" / "curated_foods.csv"
        await seed_local(session, csv)
        await session.commit()

    async with container.session_factory() as session:
        repos = container.repos(session)
        client = Client(
            id=uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            name="Prefill",
            sex=Sex.FEMALE,
            age_years=30,
            height_cm=165,
            weight_kg=62,
            activity_level=ActivityLevel.MODERATE,
            goal=Goal.LOSE_FAT,
            meal_slots=tuple(MealSlot),
        )
        await repos.clients.add(client)
        config = container.nutrition_config(client)
        targets = await compute_and_store_targets(client, repos.targets, config)
        cycle = await generate_plan_for_client(
            client=client,
            targets=targets,
            food_repo=repos.foods,
            plan_repo=repos.plans,
            client_repo=repos.clients,
            config=config,
            llm=None,
            offline_engine=container.offline_engine,
            prompts_dir=settings.prompts_dir,
            model="engine-v1",
            catalog=container.meal_catalog,
            select_foods=False,
            refine_names=False,
        )
        foods = {
            f.id: f
            for f in await repos.foods.get_by_ids(
                sorted(
                    {i.food_id for d in cycle.days for m in d.meals for i in m.items if i.food_id},
                    key=str,
                )
            )
        }
        meals = [m for d in cycle.days for m in d.meals]
        found = await recipes_for_week(
            meals=meals,
            catalog=foods,
            repo=container.dish_recipe_repo(session),
            llm=container.llm_client,
            prompts_dir=settings.prompts_dir,
            model=settings.llm_model_generate,
            static=static_recipes(container.meal_catalog),
            curated=list(container.recipe_catalog.recipes),
        )
        await session.commit()
    print(f"Prefill OK: plan {cycle.id}, {len(found)} recetas en caché.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
