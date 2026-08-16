"""Filtro determinista de USDA: categorías, estado, rol, porción y rendimiento."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from nutriplan.adapters.food.usda_fdc import UsdaFood, UsdaPortion
from nutriplan.domain.models import CookingMethod, FoodCategory, FoodState

# Ingredientes de casa. Fuera: bebidas, dulces, papillas, fast food, snacks, sopas.
KEEP_CATEGORIES: frozenset[str] = frozenset(
    {
        "Dairy and Egg Products",
        "Spices and Herbs",
        "Fats and Oils",
        "Poultry Products",
        "Sausages and Luncheon Meats",
        "Fruits and Fruit Juices",
        "Pork Products",
        "Vegetables and Vegetable Products",
        "Nut and Seed Products",
        "Beef Products",
        "Finfish and Shellfish Products",
        "Legumes and Legume Products",
        "Lamb, Veal, and Game Products",
        "Cereal Grains and Pasta",
        "Baked Products",
        "Breakfast Cereals",
    }
)

# Rol por defecto; las ambiguas (legumbres, lácteos) se afinan por macros.
_CATEGORY_ROLE: dict[str, FoodCategory] = {
    "Dairy and Egg Products": FoodCategory.DAIRY,
    "Spices and Herbs": FoodCategory.OTHER,
    "Fats and Oils": FoodCategory.FAT,
    "Poultry Products": FoodCategory.PROTEIN,
    "Sausages and Luncheon Meats": FoodCategory.PROTEIN,
    "Fruits and Fruit Juices": FoodCategory.FRUIT,
    "Pork Products": FoodCategory.PROTEIN,
    "Vegetables and Vegetable Products": FoodCategory.VEGETABLE,
    "Nut and Seed Products": FoodCategory.FAT,
    "Beef Products": FoodCategory.PROTEIN,
    "Finfish and Shellfish Products": FoodCategory.PROTEIN,
    "Legumes and Legume Products": FoodCategory.CARB,
    "Lamb, Veal, and Game Products": FoodCategory.PROTEIN,
    "Cereal Grains and Pasta": FoodCategory.CARB,
    "Baked Products": FoodCategory.CARB,
    "Breakfast Cereals": FoodCategory.CARB,
}

# Title Case tras la primera palabra = marca (Pillsbury…), no ingrediente genérico.
_ALL_CAPS_RE = re.compile(r"\b[A-Z]{3,}\b")
_CAPITALIZED_RE = re.compile(r"^[A-Z][a-z]{2,}$")
_BRAND_CAPS_THRESHOLD = 2

# "New Zealand", "Atlantic" no son marcas; sin esto se pierden cortes importados.
_PROVENANCE_WORDS = frozenset({"New", "Zealand", "Australian", "Australia", "Alaska", "Atlantic"})

_COOKING_METHODS: tuple[tuple[str, CookingMethod], ...] = (
    ("boiled", CookingMethod.BOILED),
    ("steamed", CookingMethod.STEAMED),
    ("braised", CookingMethod.STEWED),
    ("stewed", CookingMethod.STEWED),
    ("simmered", CookingMethod.STEWED),
    ("pan-fried", CookingMethod.FRIED),
    ("fried", CookingMethod.FRIED),
    ("roasted", CookingMethod.ROASTED),
    ("broiled", CookingMethod.GRILLED),
    ("grilled", CookingMethod.GRILLED),
    ("baked", CookingMethod.BAKED),
)

_RAW_RE = re.compile(r"(^|,\s*)(raw|uncooked)(\s*,|$)")
_COOKED_RE = re.compile(r"(^|,\s*)cook(ed|ing)?(\s*,|$)")

# Quitar el sufijo de preparación para emparejar crudo con cocido.
_PREP_CLAUSE = re.compile(
    r",\s*(raw|uncooked|cooked|boiled|steamed|braised|stewed|simmered|roasted|broiled"
    r"|grilled|baked|pan-fried|fried|drained|solids|with salt|without salt|unprepared"
    r"|prepared|nfs|ns as to form|reheated|heated|frozen|unheated)\b[^,]*",
    re.IGNORECASE,
)

# Los términos se buscan como PALABRA COMPLETA. Por subcadena, «egg» marcaba la
# berenjena (eggplant) como huevo y «nut» marcaba el coco, la auyama butternut y
# las donas como frutos secos. Un tag mal puesto es una restricción mal aplicada.
_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gluten", ("wheat", "barley", "rye", "bread", "pasta", "flour", "cracker", "couscous")),
    (
        "frutos_secos",
        ("almond", "walnut", "cashew", "pecan", "pistachio", "hazelnut", "macadamia", "filbert"),
    ),
    ("soya", ("soy", "soya", "soybean", "soymilk", "tofu", "tempeh", "edamame")),
    ("huevo", ("egg",)),
    ("conserva", ("canned", "in oil", "in water")),
    ("platano", ("plantain", "banana")),
)


def _palabras(needles: tuple[str, ...]) -> re.Pattern[str]:
    """Un patrón que exige palabra completa, admitiendo el plural en inglés."""
    alternativas = "|".join(re.escape(n) for n in needles)
    return re.compile(rf"\b({alternativas})s?\b", re.IGNORECASE)


_TAG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (tag, _palabras(needles)) for tag, needles in _TAG_RULES
)

_CATEGORY_TAGS: dict[str, tuple[str, ...]] = {
    "Finfish and Shellfish Products": ("pescado",),
    "Beef Products": ("res",),
    "Pork Products": ("cerdo",),
    "Dairy and Egg Products": ("lacteo",),
    "Spices and Herbs": ("condimento",),
    "Nut and Seed Products": ("frutos_secos",),
}

_SHELLFISH = ("shrimp", "crab", "lobster", "clam", "oyster", "mussel", "scallop", "squid")
_SHELLFISH_RE = _palabras(_SHELLFISH)

# Vegetal de raíz y sin tag animal → vegano.
_VEGAN_CATEGORIES = frozenset(
    {
        "Vegetables and Vegetable Products",
        "Fruits and Fruit Juices",
        "Legumes and Legume Products",
        "Nut and Seed Products",
        "Cereal Grains and Pasta",
        "Spices and Herbs",
    }
)
_ANIMAL_TAGS = frozenset({"lacteo", "huevo", "pescado", "mariscos", "res", "cerdo"})

# Huevo, rebanada, filete: sí. Taza de arroz: no, el solver trabaja en gramos.
_COUNTABLE_UNITS = frozenset(
    {
        "piece",
        "slice",
        "egg",
        "fruit",
        "breast",
        "thigh",
        "fillet",
        "link",
        "patty",
        "tortilla",
        "bar",
        "cookie",
        "roll",
        "unit",
    }
)


@dataclass(frozen=True)
class Candidate:
    """Una fila de USDA ya limpia, lista para que la IA le ponga nombre."""

    fdc_id: int
    description: str
    category: FoodCategory
    usda_category: str
    kcal_100g: float
    protein_100g: float
    carb_100g: float
    fat_100g: float
    fiber_100g: float
    sugar_100g: float
    sodium_mg_100g: float
    water_100g: float | None
    state: FoodState
    cooking_method: CookingMethod | None
    pair_key: str
    yield_factor: float | None = None
    default_unit_g: float | None = None
    unit_hint: str | None = None
    tags: list[str] = field(default_factory=list)


# Filas que USDA lista como alimento pero que no lo son en una cocina: recortes
# de grasa, vísceras raras y despojos industriales. Sin esta lista, «Beef, retail
# cuts, separable fat» (680 kcal, 70 g de grasa) compite por el nombre «carne de
# res» y lo gana, porque su descripción es corta.
_NO_ES_INGREDIENTE = re.compile(
    r"separable fat|mechanically separated|by-products|chitterlings|"
    r"\b(brain|lung|spleen|pancreas|thymus|blood|melt|udder|snout|tail bone)\b",
    re.IGNORECASE,
)


def es_ingrediente(description: str) -> bool:
    """¿Esto lo pone alguien en un plato, o es un despojo de la despiece?"""
    return not _NO_ES_INGREDIENTE.search(description)


def is_brandish(description: str) -> bool:
    """¿La descripción nombra una marca comercial?"""
    if _ALL_CAPS_RE.search(description):
        return True
    words = re.split(r"[,\s]+", description.strip())
    capitalized = sum(
        1 for word in words[1:] if _CAPITALIZED_RE.match(word) and word not in _PROVENANCE_WORDS
    )
    return capitalized >= _BRAND_CAPS_THRESHOLD


def parse_state(description: str) -> tuple[FoodState, CookingMethod | None]:
    """USDA no tiene campo de estado: lo escribe en el texto («, raw», «cooked»)."""
    text = description.lower()
    method: CookingMethod | None = None
    for needle, candidate in _COOKING_METHODS:
        if needle in text:
            method = candidate
            break
    if _COOKED_RE.search(text) or method is not None:
        return FoodState.COOKED, method
    if _RAW_RE.search(text) or ", dry" in text or ", dried" in text:
        return FoodState.RAW, None
    return FoodState.NOT_APPLICABLE, None


def pair_key(description: str) -> str:
    """Clave para emparejar la versión cruda y la cocida del mismo alimento."""
    stripped = _PREP_CLAUSE.sub("", description.lower())
    return " ".join(stripped.replace(",", " ").split())


def yield_factor(water_raw: float | None, water_cooked: float | None) -> float | None:
    """Gramos cocidos por gramo crudo: (100 − agua_crudo) / (100 − agua_cocido)."""
    if water_raw is None or water_cooked is None:
        return None
    dry_raw, dry_cooked = 100.0 - water_raw, 100.0 - water_cooked
    if dry_raw <= 0 or dry_cooked <= 0:
        return None
    factor = dry_raw / dry_cooked
    return round(factor, 3) if 0.3 <= factor <= 6.0 else None


def derive_tags(description: str, usda_category: str) -> list[str]:
    """Las restricciones que toca este alimento, por categoría y por palabras."""
    text = description.lower()
    tags: set[str] = set(_CATEGORY_TAGS.get(usda_category, ()))
    for tag, patron in _TAG_PATTERNS:
        if patron.search(text):
            tags.add(tag)
    # El marisco solo se busca DENTRO de su categoría: «Ostrich, oyster» es un
    # corte del avestruz que se llama ostra, y estaba quedando marcado como
    # marisco. Fuera de la pescadería, esas palabras significan otra cosa.
    if usda_category == "Finfish and Shellfish Products" and _SHELLFISH_RE.search(text):
        tags.add("mariscos")
        tags.discard("pescado")
    # El huevo vive en la categoría de lácteos de USDA, pero no es un lácteo.
    if "huevo" in tags:
        tags.discard("lacteo")
    if usda_category in _VEGAN_CATEGORIES and not (tags & _ANIMAL_TAGS):
        tags.add("vegano")
    return sorted(tags)


def _macro_share(candidate_kcal: float, total: float) -> float:
    return candidate_kcal / total if total > 0 else 0.0


def refine_category(usda_category: str, protein: float, carb: float, fat: float) -> FoodCategory:
    """Rol en el plato: USDA agrupa por origen, el motor necesita función."""
    role = _CATEGORY_ROLE.get(usda_category, FoodCategory.OTHER)
    p_kcal, c_kcal, f_kcal = protein * 4.0, carb * 4.0, fat * 9.0
    total = p_kcal + c_kcal + f_kcal

    if role is FoodCategory.CARB and _macro_share(p_kcal, total) >= 0.40:
        return FoodCategory.PROTEIN  # tofu, tempeh, seitán
    if role is FoodCategory.PROTEIN and _macro_share(f_kcal, total) >= 0.75:
        return FoodCategory.FAT  # tocineta, manteca, chicharrón
    if role is FoodCategory.DAIRY and _macro_share(f_kcal, total) >= 0.80:
        return FoodCategory.FAT  # mantequilla, crema de leche
    return role


def pick_portion(portions: list[UsdaPortion]) -> tuple[float | None, str | None]:
    """Porción contable (un huevo, una rebanada). Una taza no cuenta."""
    for portion in portions:
        name = portion.unit.strip().lower()
        # Frases enteras en el campo de unidad («serving 1 roll with icing») no sirven.
        if name not in _COUNTABLE_UNITS:
            continue
        if portion.amount != 1.0 or not 5.0 <= portion.gram_weight <= 400.0:
            continue
        return portion.gram_weight, name
    return None, None


def has_macros(food: UsdaFood) -> bool:
    return None not in (food.kcal_100g, food.protein_100g, food.carb_100g, food.fat_100g)


def _non_negative(value: float | None) -> float:
    """USDA calcula el carbo por diferencia; en quesos casi puros sale negativo."""
    return max(value or 0.0, 0.0)


def build_candidates(
    foods: list[UsdaFood], portions: dict[int, list[UsdaPortion]] | None = None
) -> list[Candidate]:
    """Filtra, deriva y empareja crudo con cocido. Sin IA y sin I/O."""
    portions = portions or {}
    kept: list[Candidate] = []
    for food in foods:
        category = food.category_desc or ""
        if category not in KEEP_CATEGORIES or not has_macros(food):
            continue
        if is_brandish(food.description) or not es_ingrediente(food.description):
            continue
        state, method = parse_state(food.description)
        unit_g, unit_hint = pick_portion(portions.get(food.fdc_id, []))
        kept.append(
            Candidate(
                fdc_id=food.fdc_id,
                description=food.description,
                category=refine_category(
                    category,
                    _non_negative(food.protein_100g),
                    _non_negative(food.carb_100g),
                    _non_negative(food.fat_100g),
                ),
                usda_category=category,
                kcal_100g=_non_negative(food.kcal_100g),
                protein_100g=_non_negative(food.protein_100g),
                carb_100g=_non_negative(food.carb_100g),
                fat_100g=_non_negative(food.fat_100g),
                fiber_100g=_non_negative(food.fiber_100g),
                sugar_100g=_non_negative(food.sugar_100g),
                sodium_mg_100g=_non_negative(food.sodium_mg_100g),
                water_100g=food.water_100g,
                state=state,
                cooking_method=method,
                pair_key=pair_key(food.description),
                default_unit_g=unit_g,
                unit_hint=unit_hint,
                tags=derive_tags(food.description, category),
            )
        )
    return _with_yield_factors(kept)


def _with_yield_factors(candidates: list[Candidate]) -> list[Candidate]:
    """Le pone a cada fila cocida el factor contra su hermana cruda."""
    raw_water: dict[str, float] = {}
    for candidate in candidates:
        if candidate.state is FoodState.RAW and candidate.water_100g is not None:
            raw_water.setdefault(candidate.pair_key, candidate.water_100g)

    out: list[Candidate] = []
    for candidate in candidates:
        factor = None
        if candidate.state is FoodState.COOKED:
            factor = yield_factor(_raw_water_for(candidate, raw_water), candidate.water_100g)
        out.append(candidate if factor is None else replace(candidate, yield_factor=factor))
    return out


def _raw_water_for(cooked: Candidate, raw_water: dict[str, float]) -> float | None:
    """Agua del crudo equivalente: clave exacta, si no el prefijo más largo."""
    exact = raw_water.get(cooked.pair_key)
    if exact is not None:
        return exact

    words = cooked.pair_key.split()
    for size in range(len(words) - 1, 0, -1):
        found = raw_water.get(" ".join(words[:size]))
        if found is not None:
            return found
    return None
