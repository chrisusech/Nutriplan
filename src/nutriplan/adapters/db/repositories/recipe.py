"""La caché global de recetas de plato.

Global y sin tenant a propósito: el mismo plato se prepara igual para todo el
mundo, y compartir la caché es lo que hace viable la cuota gratuita de IA."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import DishRecipeRow
from nutriplan.domain.dish_recipe import DishRecipe


class SqlDishRecipeRepository:
    """Recetas de plato. GLOBAL, sin tenant: el mismo plato se prepara igual
    para todo el mundo, y compartirlas es lo que hace viable la cuota de IA."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _to_domain(row: DishRecipeRow) -> DishRecipe:
        return DishRecipe(
            dish_key=row.dish_key,
            template_id=row.template_id,
            name_es=row.name_es,
            ingredients=list(row.ingredients or []),
            steps=list(row.steps or []),
            prep_minutes=row.prep_minutes,
            difficulty=row.difficulty,
            tips=row.tips,
            source=row.source,
        )

    async def get_many(self, keys: list[str]) -> dict[str, DishRecipe]:
        if not keys:
            return {}
        stmt = select(DishRecipeRow).where(DishRecipeRow.dish_key.in_(keys))
        rows = (await self._s.execute(stmt)).scalars().all()
        return {r.dish_key: self._to_domain(r) for r in rows}

    async def add(self, recipe: DishRecipe, *, model: str, prompt_version: str) -> None:
        """Idempotente: dos menús generados a la vez piden el mismo plato."""
        existing = await self._s.execute(
            select(DishRecipeRow.id).where(DishRecipeRow.dish_key == recipe.dish_key)
        )
        if existing.scalar_one_or_none() is not None:
            return
        self._s.add(
            DishRecipeRow(
                id=uuid4(), dish_key=recipe.dish_key, template_id=recipe.template_id,
                name_es=recipe.name_es, ingredients=list(recipe.ingredients),
                steps=list(recipe.steps), prep_minutes=recipe.prep_minutes,
                difficulty=recipe.difficulty, tips=recipe.tips, source=recipe.source,
                model=model or None, prompt_version=prompt_version,
                created_at=datetime.now(UTC),
            )
        )
        await self._s.flush()
