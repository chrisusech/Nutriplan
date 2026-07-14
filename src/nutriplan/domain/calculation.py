"""Motor de cálculo determinista (Módulo 2, secciones 9.1–9.4).

Código puro, sin IA, reproducible bit a bit. El orden importa — no hay
porcentajes mágicos, las kcal se calculan primero y los macros se estructuran
por peso corporal:

  1. BMR  = Mifflin-St Jeor
  2. TDEE = BMR * factor de actividad
  3. kcal = MAX( TDEE * (1 + ajuste del objetivo),  BMR,  piso por sexo )
  4. proteína = peso_kg * g/kg      (1.6–2.2, la elige el entrenador)
  5. grasa    = peso_kg * g/kg      (0.8–1.0, la elige el entrenador)
  6. carbo    = lo que sobra de las kcal   ← cierra el invariante energético

El paso 3 es un máximo, no un producto: un déficit porcentual sobre un TDEE
sedentario bajo se hunde por debajo del propio metabolismo basal. La guía
AHA/ACC/TOS marca 1200–1500 kcal en mujeres y 1500–1800 en hombres.

La fibra sale de las kcal (14 g por 1000 kcal, estándar DRI).
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import (
    Client,
    MacroFormula,
    MacroTargets,
    MealSlot,
    NutritionTargets,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig, SlotShare

KCAL_PER_G_PROTEIN = 4.0
KCAL_PER_G_CARB = 4.0
KCAL_PER_G_FAT = 9.0

# Campos de MacroTargets que el entrenador puede fijar manualmente.
OVERRIDABLE_FIELDS = ("kcal", "protein_g", "carb_g", "fat_g")


def resolve_age_years(client: Client, *, today: date | None = None) -> int:
    """Edad desde birthdate (preferida) o age_years; sin ambas → error claro."""
    if client.birthdate is not None:
        ref = today or date.today()
        years = ref.year - client.birthdate.year
        if (ref.month, ref.day) < (client.birthdate.month, client.birthdate.day):
            years -= 1
        return years
    if client.age_years is not None:
        return client.age_years
    raise CalculationError(f"Cliente {client.id}: falta fecha de nacimiento o edad")


def bmr_mifflin_st_jeor(sex: Sex, weight_kg: float, height_cm: float, age_years: int) -> float:
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years
    return base + 5.0 if sex == Sex.MALE else base - 161.0


def kcal_floor_for(client: Client, config: NutritionConfig) -> float:
    """El suelo energético del cliente: ni bajo su basal, ni bajo el mínimo de la guía."""
    age = resolve_age_years(client)
    bmr = bmr_mifflin_st_jeor(client.sex, client.weight_kg, client.height_cm, age)
    return max(bmr, config.kcal_floor[client.sex])


def validate_daily(daily: MacroTargets, client: Client, config: NutritionConfig) -> None:
    """Los mismos pisos que la fórmula, para un objetivo VENGA DE DONDE VENGA.

    `compute_daily_macros` los comprueba sobre lo que calcula; esto los comprueba
    sobre lo que se va a guardar. Sin ello, los ajustes manuales del entrenador —que
    se aplican después— entraban sin pasar por ninguna guarda.
    """
    floor = kcal_floor_for(client, config)
    if daily.kcal < floor:
        raise CalculationError(
            f"{daily.kcal:.0f} kcal está bajo el piso de {floor:.0f} (metabolismo basal y "
            f"mínimo de la guía para {client.sex.value}). Ese déficit no es sostenible: "
            f"sube las kcal o baja menos los macros."
        )
    carb_floor = config.carb_floor_g_per_kg * client.weight_kg
    if daily.carb_g < carb_floor:
        raise CalculationError(
            f"Los carbohidratos quedan en {daily.carb_g:.0f} g, bajo el piso de "
            f"{carb_floor:.0f} g ({config.carb_floor_g_per_kg} g/kg). Baja la proteína o la "
            f"grasa, o sube las kcal."
        )
    if daily.protein_g <= 0 or daily.fat_g <= 0:
        raise CalculationError("La proteína y la grasa tienen que ser mayores que cero.")


def compute_daily_macros(
    client: Client, config: NutritionConfig, formula: MacroFormula | None = None
) -> MacroTargets:
    """Macros diarios desde el peso y la fórmula g/kg.

    Con `formula=None` (o vacía) usa los defaults de la config por objetivo, así
    que reproduce bit a bit el cálculo previo. Con g/kg fijados por el entrenador,
    esos mandan; el carbohidrato siempre cierra el resto hasta las kcal.
    """
    formula = formula or MacroFormula()
    age = resolve_age_years(client)
    bmr = bmr_mifflin_st_jeor(client.sex, client.weight_kg, client.height_cm, age)
    tdee = bmr * config.activity_factors[client.activity_level]

    # Piso energético: nadie come por debajo de su BMR ni del mínimo de la guía.
    # Un déficit porcentual sobre un TDEE sedentario bajo se hunde solo — una
    # mujer de 80 kg salía en 1496 kcal con un BMR de 1520.
    floor = max(bmr, config.kcal_floor[client.sex])

    if formula.kcal_override is not None:
        kcal = formula.kcal_override
        if kcal < floor:
            raise CalculationError(
                f"Cliente {client.id}: {kcal:.0f} kcal está bajo el piso de {floor:.0f} "
                f"(BMR {bmr:.0f}, mínimo {config.kcal_floor[client.sex]:.0f} para "
                f"{client.sex.value}). Ese déficit no es sostenible."
            )
    else:
        # El máximo de las tres restricciones, no el producto a secas.
        kcal = max(tdee * (1.0 + config.goal_adjustments[client.goal]), floor)

    ppk = formula.protein_g_per_kg
    if ppk is None:
        ppk = config.protein_g_per_kg[client.goal]
    protein_g = ppk * client.weight_kg

    # La grasa se fija por peso, no por porcentaje de kcal. Un % fijo se calcula
    # sobre unas kcal ya recortadas, así que en déficit deja a la clienta en
    # ~0.6 g/kg (y en volumen se dispara a 1.3). El g/kg no depende del déficit.
    fpk = formula.fat_g_per_kg or config.fat_g_per_kg.get(client.goal)
    if fpk is not None:
        fat_g = fpk * client.weight_kg
    else:
        fat_g = (kcal * config.fat_pct_of_kcal) / KCAL_PER_G_FAT

    carb_g = (kcal - protein_g * KCAL_PER_G_PROTEIN - fat_g * KCAL_PER_G_FAT) / KCAL_PER_G_CARB

    # El carbo cierra, así que absorbe todo lo que se pasen proteína y grasa.
    # Guarda de imposibilidad, no de opinión: los planes low-carb son legítimos
    # (hay planes reales sin carbohidrato en almuerzo ni cena), así que el piso
    # solo ataja lo que no se puede armar de ninguna forma.
    carb_floor = config.carb_floor_g_per_kg * client.weight_kg
    if carb_g < carb_floor:
        raise CalculationError(
            f"Cliente {client.id}: los carbohidratos quedan en {carb_g:.0f} g, bajo el piso "
            f"de {carb_floor:.0f} g ({config.carb_floor_g_per_kg} g/kg). Con {kcal:.0f} kcal, "
            f"proteína {ppk} g/kg y grasa {fpk} g/kg no hay margen. "
            f"Baja la proteína o la grasa, o sube las kcal."
        )

    return MacroTargets(
        kcal=round(kcal, 1),
        protein_g=round(protein_g, 1),
        carb_g=round(carb_g, 1),
        fat_g=round(fat_g, 1),
        fiber_g=round(kcal / 1000.0 * config.fiber.g_per_1000_kcal, 1),
    )


def energy_kcal(protein_g: float, carb_g: float, fat_g: float) -> float:
    """Las kcal de unos macros. La identidad que todo plato real cumple."""
    return round(
        protein_g * KCAL_PER_G_PROTEIN
        + carb_g * KCAL_PER_G_CARB
        + fat_g * KCAL_PER_G_FAT,
        1,
    )


def apply_overrides(daily: MacroTargets, overrides: dict[str, float]) -> MacroTargets:
    """Aplica los ajustes manuales del entrenador MANTENIENDO el invariante energético.

    Las kcal no son un cuarto campo suelto: son el RESULTADO de los macros
    (4·P + 4·C + 9·G). Antes se recomputaban solo si las kcal no venían en los
    ajustes, y ahí murió un plan: la pantalla mandaba los cuatro campos, las kcal
    entraban como ajuste sin que nadie las hubiera tocado, y entonces se congelaban
    mientras los macros cambiaban. El objetivo quedaba con unas kcal que no eran las
    de sus propios macros, y como `validate_day` mide las kcal Y los tres macros por
    separado, cuadrar los cuatro a la vez era imposible: el motor agotaba los
    reintentos y el cliente se quedaba sin plan.

    Las dos direcciones, sin excepciones:
      · se toca un macro  → las kcal se recalculan (son su consecuencia);
      · se tocan las kcal → el carbohidrato cierra, igual que en la fórmula.
    """
    unknown = set(overrides) - set(OVERRIDABLE_FIELDS)
    if unknown:
        raise CalculationError(f"Overrides desconocidos: {sorted(unknown)}")

    values = daily.model_dump()
    values.update({k: float(v) for k, v in overrides.items()})

    if {"protein_g", "carb_g", "fat_g"} & set(overrides):
        values["kcal"] = energy_kcal(
            values["protein_g"], values["carb_g"], values["fat_g"]
        )
    elif "kcal" in overrides:
        values["carb_g"] = round(
            (
                values["kcal"]
                - values["protein_g"] * KCAL_PER_G_PROTEIN
                - values["fat_g"] * KCAL_PER_G_FAT
            )
            / KCAL_PER_G_CARB,
            1,
        )
    return MacroTargets(**values)


def split_per_meal(
    daily: MacroTargets, distribution: dict[MealSlot, SlotShare]
) -> dict[MealSlot, MacroTargets]:
    """Reparto por comida: cada macro tiene su propio peso.

    La grasa y la fibra no lo tienen (la grasa cierra a nivel de día, donde hay
    fuente): van por el peso en kcal del slot.
    """
    return {
        slot: MacroTargets(
            kcal=round(daily.kcal * share.kcal, 1),
            protein_g=round(daily.protein_g * share.protein_g, 1),
            carb_g=round(daily.carb_g * share.carb_g, 1),
            fat_g=round(daily.fat_g * share.kcal, 1),
            fiber_g=round(daily.fiber_g * share.kcal, 1),
        )
        for slot, share in distribution.items()
    }


def compute_targets(
    client: Client,
    config: NutritionConfig,
    *,
    formula: MacroFormula | None = None,
    overrides: dict[str, float] | None = None,
    targets_id: UUID | None = None,
    now: datetime | None = None,
) -> NutritionTargets:
    """De Client + config (+ fórmula g/kg + overrides) → NutritionTargets.

    La fórmula fija el modelo (g/kg, kcal); los overrides son nudges de gramos
    sueltos aplicados encima. Se guardan ambos para procedencia.
    """
    formula = formula or MacroFormula()
    daily = compute_daily_macros(client, config, formula)
    if overrides:
        daily = apply_overrides(daily, overrides)
        # Los ajustes manuales se aplican DESPUÉS de las guardas de
        # `compute_daily_macros`, así que hasta ahora no los validaba nadie: por los
        # cuadritos de la pantalla se podía persistir un carbo negativo o un día
        # bajo el metabolismo basal.
        validate_daily(daily, client, config)
    per_meal = split_per_meal(daily, config.meal_distribution)

    return NutritionTargets(
        id=targets_id or uuid4(),
        tenant_id=client.tenant_id,
        client_id=client.id,
        daily=daily,
        per_meal=per_meal,
        config_version=config.version,
        overrides=overrides or {},
        formula=formula,
        # El peso con el que se calcularon. `Client.weight_kg` cambia el mes que
        # viene; estos macros no, y sin el peso de entonces no hay forma de saber si
        # el déficit funcionó.
        weight_kg=client.weight_kg,
        computed_at=now or datetime.now(UTC),
    )
