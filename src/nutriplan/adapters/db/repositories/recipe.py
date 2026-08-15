"""La caché global de recetas de plato.

Global y sin tenant a propósito: el mismo plato se prepara igual para todo el
mundo, y compartir la caché es lo que hace viable la cuota gratuita de IA."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import DishRatingRow, DishRecipeRow, MealEntryRow
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.models import MacroTargets


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
            food_ids=[UUID(f) for f in (row.food_ids or [])],
            reference_grams=dict(row.reference_grams or {}),
            macros=MacroTargets(**row.macros) if row.macros else None,
            rating_avg=row.rating_avg,
            rating_count=row.rating_count,
            times_served=row.times_served,
        )

    @staticmethod
    def _numbers(
        recipe: DishRecipe,
    ) -> tuple[list[str], dict[str, float], dict[str, float] | None]:
        """Lo que calculó el código: alimentos, gramos y macros de referencia."""
        return (
            [str(f) for f in recipe.food_ids],
            dict(recipe.reference_grams),
            recipe.macros.model_dump() if recipe.macros else None,
        )

    def _quality_values(self) -> dict[str, Any]:
        """Las tres cifras de calidad, recalculadas desde su fuente.

        Se agregan por `dish_key` cruzando todos los tenants a propósito: la
        receta es una sola para todo el mundo, y su calidad también. Se recalcula
        en vez de acumularse porque volver a calificar corrige la nota, y un
        contador incremental se habría quedado con la vieja.
        """
        rated = DishRatingRow.dish_key == DishRecipeRow.dish_key
        return {
            "rating_avg": select(func.avg(DishRatingRow.rating)).where(rated).scalar_subquery(),
            "rating_count": (
                select(func.count()).select_from(DishRatingRow).where(rated).scalar_subquery()
            ),
            "times_served": (
                select(func.count())
                .select_from(MealEntryRow)
                .where(MealEntryRow.dish_key == DishRecipeRow.dish_key)
                .scalar_subquery()
            ),
        }

    async def refresh_quality(self, dish_keys: list[str]) -> None:
        """Pone al día la calidad de estas recetas."""
        if not dish_keys:
            return
        await self._s.execute(
            update(DishRecipeRow)
            .where(DishRecipeRow.dish_key.in_(dish_keys))
            .values(**self._quality_values())
        )
        await self._s.flush()

    async def refresh_all(self) -> int:
        """Recalcula la biblioteca entera. Devuelve cuántas recetas ha tocado.

        Existe porque la calidad solo se refresca al calificar: las recetas que
        se escribieron antes de que eso existiera —141 en el beta— se quedaron
        con cero votos y cero veces servida para siempre, y la consola las
        ordenaba por unos números que eran todos cero. Es idempotente: recalcula
        desde las notas y los platos servidos, no acumula.
        """
        await self._s.execute(update(DishRecipeRow).values(**self._quality_values()))
        await self._s.flush()
        total = await self._s.execute(select(func.count()).select_from(DishRecipeRow))
        return int(total.scalar_one())

    async def get_many(self, keys: list[str]) -> dict[str, DishRecipe]:
        """Las recetas vivas. Una retirada no se sirve: se vuelve a generar."""
        if not keys:
            return {}
        stmt = select(DishRecipeRow).where(
            DishRecipeRow.dish_key.in_(keys), DishRecipeRow.retired_at.is_(None)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return {r.dish_key: self._to_domain(r) for r in rows}

    async def library(self, *, source: str | None = None, limit: int = 200) -> list[DishRecipe]:
        """La biblioteca para la consola: lo mejor valorado primero.

        Sin nota va al final, no arriba: una receta que nadie ha probado no es
        mejor que una de cuatro estrellas, solo es desconocida.
        """
        stmt = select(DishRecipeRow).where(DishRecipeRow.retired_at.is_(None))
        if source:
            stmt = stmt.where(DishRecipeRow.source == source)
        stmt = stmt.order_by(
            DishRecipeRow.rating_avg.desc().nulls_last(),
            DishRecipeRow.times_served.desc(),
            DishRecipeRow.name_es,
        ).limit(limit)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def top_rated(
        self, *, min_rating: float, min_count: int, limit: int = 200
    ) -> list[DishRecipe]:
        """Los platos que la gente calificó bien, para volver a proponerlos."""
        stmt = (
            select(DishRecipeRow)
            .where(
                DishRecipeRow.retired_at.is_(None),
                DishRecipeRow.rating_count >= min_count,
                DishRecipeRow.rating_avg >= min_rating,
            )
            .order_by(DishRecipeRow.rating_avg.desc(), DishRecipeRow.rating_count.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def get(self, dish_key: str) -> DishRecipe | None:
        stmt = select(DishRecipeRow).where(DishRecipeRow.dish_key == dish_key)
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def retire(self, dish_key: str) -> None:
        """Fuera de la caché. El plato sigue existiendo; su explicación no."""
        await self._s.execute(
            update(DishRecipeRow)
            .where(DishRecipeRow.dish_key == dish_key)
            .values(retired_at=datetime.now(UTC))
        )
        await self._s.flush()

    async def add(self, recipe: DishRecipe, *, model: str, prompt_version: str) -> None:
        """Idempotente; si llega una IA sobre una plantilla yaml, la reemplaza."""
        existing = (
            await self._s.execute(
                select(DishRecipeRow).where(DishRecipeRow.dish_key == recipe.dish_key)
            )
        ).scalar_one_or_none()
        food_ids, grams, macros = self._numbers(recipe)
        if existing is not None:
            # Caché global: se puede mejorar una plantilla yaml con IA (prefer_ai
            # al mirar el día), pero nunca pisar una receta curated ni otra AI.
            if recipe.source == "ai" and existing.source == "yaml":
                existing.template_id = recipe.template_id
                existing.name_es = recipe.name_es
                existing.ingredients = list(recipe.ingredients)
                existing.steps = list(recipe.steps)
                existing.prep_minutes = recipe.prep_minutes
                existing.difficulty = recipe.difficulty
                existing.tips = recipe.tips
                existing.source = recipe.source
                existing.model = model or None
                existing.prompt_version = prompt_version
            # Las cifras sí se refrescan siempre: una receta vieja sin macros se
            # completa la próxima vez que alguien se come el plato.
            if macros is not None:
                existing.food_ids = food_ids
                existing.reference_grams = grams
                existing.macros = macros
            await self._s.flush()
            return
        self._s.add(
            DishRecipeRow(
                id=uuid4(),
                dish_key=recipe.dish_key,
                template_id=recipe.template_id,
                name_es=recipe.name_es,
                ingredients=list(recipe.ingredients),
                steps=list(recipe.steps),
                prep_minutes=recipe.prep_minutes,
                difficulty=recipe.difficulty,
                tips=recipe.tips,
                source=recipe.source,
                model=model or None,
                prompt_version=prompt_version,
                created_at=datetime.now(UTC),
                food_ids=food_ids,
                reference_grams=grams,
                macros=macros,
            )
        )
        await self._s.flush()
        # El plato pudo servirse y calificarse antes de que su receta existiera
        # (la escribimos al abrir el día, no al generar la semana). Sin esto nace
        # con cero votos aunque ya tenga notas, y nadie la vuelve a mirar.
        await self.refresh_quality([recipe.dish_key])
