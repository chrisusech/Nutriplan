"""Recetas: alimentos compuestos del tenant y la cache global de preparaciones."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    DishRecipeRow,
    RecipeRow,
)
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.errors import TenantIsolationError
from nutriplan.domain.models import (
    MacroTargets,
    MealSlot,
    Recipe,
    RecipeIngredient,
    RecipeStatus,
)


class SqlRecipeRepository:
    """Recetas del tenant. Con `tenant_id=None` (modo admin) no filtra: el
    admin ve y verifica las recetas pendientes de todos los entrenadores."""

    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: RecipeRow) -> Recipe:
        return Recipe(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            ingredients=[
                RecipeIngredient(food_id=UUID(i["food_id"]), grams=i["grams"])
                for i in row.ingredients
            ],
            macros=MacroTargets(**row.macros),
            total_grams=row.total_grams,
            meal_slots=[MealSlot(s) for s in (row.meal_slots or [])],
            status=RecipeStatus(row.status),
            created_by=row.created_by,
            created_at=_aware(row.created_at),
            compound_food_id=row.compound_food_id,
        )

    async def add(self, recipe: Recipe) -> None:
        self._s.add(
            RecipeRow(
                id=recipe.id,
                tenant_id=recipe.tenant_id,
                name=recipe.name,
                ingredients=[
                    {"food_id": str(i.food_id), "grams": i.grams} for i in recipe.ingredients
                ],
                macros=recipe.macros.model_dump(),
                total_grams=recipe.total_grams,
                meal_slots=[s.value for s in recipe.meal_slots],
                status=recipe.status.value,
                created_by=recipe.created_by,
                created_at=recipe.created_at,
                compound_food_id=recipe.compound_food_id,
            )
        )
        await self._s.flush()

    async def _row(self, recipe_id: UUID) -> RecipeRow | None:
        stmt = select(RecipeRow).where(RecipeRow.id == recipe_id)
        if self._tenant is not None:
            stmt = stmt.where(RecipeRow.tenant_id == self._tenant)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get(self, recipe_id: UUID) -> Recipe | None:
        row = await self._row(recipe_id)
        return self._to_domain(row) if row else None

    async def list_for_tenant(self) -> list[Recipe]:
        stmt = select(RecipeRow).order_by(RecipeRow.created_at.desc())
        if self._tenant is not None:
            stmt = stmt.where(RecipeRow.tenant_id == self._tenant)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_pending(self) -> list[Recipe]:
        """Cola de verificación (admin): pendientes de todos los tenants."""
        stmt = (
            select(RecipeRow)
            .where(RecipeRow.status == RecipeStatus.PENDING.value)
            .order_by(RecipeRow.created_at)
        )
        if self._tenant is not None:
            stmt = stmt.where(RecipeRow.tenant_id == self._tenant)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def set_status(
        self, recipe_id: UUID, status: RecipeStatus, *, compound_food_id: UUID | None = None
    ) -> None:
        row = await self._row(recipe_id)
        if row is None:
            raise TenantIsolationError("Receta inexistente para este contexto")
        row.status = status.value
        if compound_food_id is not None:
            row.compound_food_id = compound_food_id
        await self._s.flush()


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
