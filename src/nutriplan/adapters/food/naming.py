"""La IA nombra; el código ya trajo macros, estado y porción."""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field

from nutriplan.adapters.food.catalog import CatalogEntry
from nutriplan.adapters.food.curation import Candidate
from nutriplan.adapters.food.traductor import aliases_de, nombre_es
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodCategory, FoodState, MealSlot, UnitGranularity
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

FOOD_CURATION_VERSION = 1

# Lotes de 12: con 40 un modelo pequeño (gpt-oss-20b) trunca el JSON.
BATCH_SIZE = 12

_SLOTS_POR_ROL: dict[FoodCategory, tuple[MealSlot, ...]] = {
    FoodCategory.PROTEIN: (MealSlot.LUNCH, MealSlot.DINNER),
    FoodCategory.CARB: (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER),
    FoodCategory.FAT: tuple(MealSlot),
    FoodCategory.FRUIT: (MealSlot.BREAKFAST, MealSlot.SNACK_AM, MealSlot.SNACK_PM),
    FoodCategory.VEGETABLE: (MealSlot.LUNCH, MealSlot.DINNER),
    FoodCategory.DAIRY: (MealSlot.BREAKFAST, MealSlot.SNACK_AM, MealSlot.SNACK_PM),
    FoodCategory.OTHER: tuple(MealSlot),
}


class CuratedName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    i: int
    apto: bool
    name_es: str = Field(default="", max_length=60)
    aliases: list[str] = Field(default_factory=list, max_length=6)
    meal_slots: list[MealSlot] = Field(default_factory=list)
    nucleo: bool = False


class CuratedBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alimentos: list[CuratedName]


def batch_prompt(candidates: list[Candidate]) -> str:
    lines = ["ALIMENTOS DEL LOTE:", ""]
    for i, c in enumerate(candidates):
        estado = c.state.value if c.state is not FoodState.NOT_APPLICABLE else "no aplica"
        lines.append(
            f"{i}. {c.description}\n"
            f"   categoría USDA: {c.usda_category} · rol: {c.category.value} · estado: {estado}\n"
            f"   por 100 g: {c.kcal_100g:.0f} kcal · P {c.protein_100g:.1f}"
            f" · C {c.carb_100g:.1f} · G {c.fat_100g:.1f}"
        )
    return "\n".join(lines)


def merge(candidate: Candidate, curated: CuratedName) -> CatalogEntry:
    """Junta lo que calculó el código con lo que nombró la IA."""
    granularity = (
        UnitGranularity.WHOLE
        if candidate.default_unit_g and candidate.unit_hint
        else UnitGranularity.GRAMS
    )
    return CatalogEntry(
        fdc_id=candidate.fdc_id,
        name_es=curated.name_es.strip().lower(),
        name_en=candidate.description,
        category=candidate.category,
        kcal_100g=candidate.kcal_100g,
        protein_100g=candidate.protein_100g,
        carb_100g=candidate.carb_100g,
        fat_100g=candidate.fat_100g,
        fiber_100g=candidate.fiber_100g,
        sugar_100g=candidate.sugar_100g,
        sodium_mg_100g=candidate.sodium_mg_100g,
        state=candidate.state,
        cooking_method=candidate.cooking_method,
        yield_factor=candidate.yield_factor,
        tags=list(candidate.tags),
        aliases=[a.strip().lower() for a in curated.aliases if a.strip()],
        meal_slots=list(curated.meal_slots),
        default_unit_g=candidate.default_unit_g,
        unit_granularity=granularity,
        unit_name=candidate.unit_hint,
        engine_default=curated.nucleo,
        pair_key=candidate.pair_key,
    )


def fallback_entry(candidate: Candidate) -> CatalogEntry:
    """Sin IA no hay nombre de cocina: se marca para revisión humana."""
    return merge(candidate, CuratedName(i=0, apto=False, name_es=candidate.description[:60]))


def entry_traducida(candidate: Candidate) -> CatalogEntry | None:
    """El mismo alimento, nombrado por el léxico. None si no se sabe traducir.

    Es la vía por defecto: determinista, instantánea y gratis. La IA solo hace
    falta para lo que el léxico no cubre, y aun entonces sigue sin tocar un
    número.
    """
    nombre = nombre_es(candidate.description)
    if nombre is None:
        return None
    return merge(
        candidate,
        CuratedName(
            i=0,
            apto=True,
            name_es=nombre,
            aliases=aliases_de(nombre),
            meal_slots=slots_por_defecto(candidate.category),
            nucleo=False,
        ),
    )


def slots_por_defecto(category: FoodCategory) -> list[MealSlot]:
    """En qué comidas encaja, por su rol. Es lo que ya hacía `FoodItem` solo.

    Se pasa explícito para que la fila del catálogo diga lo que significa en vez
    de depender de un default que puede cambiar debajo.
    """
    return list(_SLOTS_POR_ROL[category])


async def curate_batch(
    candidates: list[Candidate],
    *,
    llm: LLMClient,
    system: str,
    model: str,
) -> list[CatalogEntry]:
    """Nombra un lote. Lo que la IA descarta o se salta no entra al catálogo."""
    raw = await llm.extract(
        system=system,
        text=batch_prompt(candidates),
        schema=CuratedBatch,
        model=model,
    )
    by_index = {item.i: item for item in raw.alimentos}
    entries: list[CatalogEntry] = []
    for i, candidate in enumerate(candidates):
        curated = by_index.get(i)
        if curated is None:
            logger.warning("curation_missing", fdc_id=candidate.fdc_id)
            continue
        if not curated.apto or not curated.name_es.strip():
            continue
        entries.append(merge(candidate, curated))
    return entries


async def curate_all(
    candidates: list[Candidate],
    *,
    llm: LLMClient,
    system: str,
    model: str,
    batch_size: int = BATCH_SIZE,
    on_progress: object = None,
) -> list[CatalogEntry]:
    """Recorre el catálogo en lotes. Un lote que falla no tumba la corrida."""
    entries: list[CatalogEntry] = []
    total = (len(candidates) + batch_size - 1) // batch_size
    for n, start in enumerate(range(0, len(candidates), batch_size), start=1):
        batch = candidates[start : start + batch_size]
        try:
            entries.extend(await curate_batch(batch, llm=llm, system=system, model=model))
        except (LLMError, ValueError) as exc:
            logger.warning("curation_batch_failed", batch=n, error=str(exc))
        if callable(on_progress):
            on_progress(n, total, len(entries))
    return entries
