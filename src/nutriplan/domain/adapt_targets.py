"""Adaptación determinista de kcal según la tendencia de peso semanal.

La IA no toca estos números: el código decide subir, bajar o mantener, dentro
de pisos y techos. Eso es lo que permite testear “si no baja en déficit, bajan
las kcal” sin depender de un proveedor.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from nutriplan.domain.calculation import ADMIN_LOCK, USER_LOCK, kcal_floor_for
from nutriplan.domain.models import Client, Goal, MacroFormula, NutritionTargets
from nutriplan.domain.nutrition_config import AdaptationConfig, NutritionConfig

__all__ = [
    "ADMIN_LOCK",
    "USER_LOCK",
    "AdaptAction",
    "AdaptationDecision",
    "decide_kcal_adaptation",
    "decision_from_targets",
    "is_admin_locked",
]


class AdaptAction(StrEnum):
    RAISE = "raise"
    LOWER = "lower"
    HOLD = "hold"


class AdaptationDecision(BaseModel):
    """Resultado de una pasada de adaptación. Todo explicable sin IA."""

    action: AdaptAction
    delta_kcal: float
    previous_kcal: float
    new_kcal: float
    weight_delta_kg: float
    reason_code: str
    formula: MacroFormula


def decide_kcal_adaptation(
    *,
    client: Client,
    config: NutritionConfig,
    previous_weight_kg: float,
    current_weight_kg: float,
    previous_kcal: float,
    previous_formula: MacroFormula | None = None,
    admin_locked: bool = False,
) -> AdaptationDecision:
    """Compara peso semana a semana y propone el siguiente `kcal_override`.

    `previous_kcal` es lo que ya comía (última fila de targets). El resultado
    lleva una `MacroFormula` lista para `compute_targets`.

    Con `admin_locked` no se mueve nada: alguien ajustó esas kcal a mano por una
    razón que la báscula no conoce, y el check-in del lunes no puede deshacerla
    sin avisar.
    """
    if admin_locked:
        return _hold_locked(previous_kcal, current_weight_kg - previous_weight_kg, previous_formula)
    adapt = config.adaptation
    delta_w = current_weight_kg - previous_weight_kg
    action, reason = _action_for(client.goal, delta_w, adapt)

    step = 0.0 if action is AdaptAction.HOLD else adapt.step_kcal
    if action is AdaptAction.LOWER:
        step = -step

    floor = kcal_floor_for(client, config)
    ceiling = previous_kcal + adapt.max_raise_kcal
    raw = previous_kcal + step
    new_kcal = min(max(raw, floor), max(ceiling, floor))
    applied = round(new_kcal - previous_kcal, 1)
    if abs(applied) < 1.0:
        action = AdaptAction.HOLD
        if reason != "hold_on_track":
            reason = "hold_clamped"
        applied = 0.0
        new_kcal = previous_kcal

    base = previous_formula or MacroFormula()
    # Tras el check-in fijamos override: si solo dependiéramos del TDEE, un
    # cambio de peso movería las kcal sin que la regla lo hubiera pedido.
    formula = MacroFormula(
        protein_g_per_kg=base.protein_g_per_kg,
        fat_g_per_kg=base.fat_g_per_kg,
        kcal_override=round(new_kcal, 1),
    )

    return AdaptationDecision(
        action=action,
        delta_kcal=applied,
        previous_kcal=previous_kcal,
        new_kcal=round(new_kcal, 1),
        weight_delta_kg=round(delta_w, 2),
        reason_code=reason,
        formula=formula,
    )


def _hold_locked(
    previous_kcal: float, delta_w: float, previous_formula: MacroFormula | None
) -> AdaptationDecision:
    base = previous_formula or MacroFormula()
    return AdaptationDecision(
        action=AdaptAction.HOLD,
        delta_kcal=0.0,
        previous_kcal=previous_kcal,
        new_kcal=previous_kcal,
        weight_delta_kg=round(delta_w, 2),
        reason_code="hold_admin_lock",
        formula=MacroFormula(
            protein_g_per_kg=base.protein_g_per_kg,
            fat_g_per_kg=base.fat_g_per_kg,
            kcal_override=round(previous_kcal, 1),
        ),
    )


def is_admin_locked(targets: NutritionTargets) -> bool:
    """Admin o la propia persona: el check-in no pisa esos números."""
    overrides = targets.overrides or {}
    return bool(overrides.get(ADMIN_LOCK) or overrides.get(USER_LOCK))


def decision_from_targets(
    *,
    client: Client,
    config: NutritionConfig,
    previous_weight_kg: float,
    current_weight_kg: float,
    previous: NutritionTargets,
) -> AdaptationDecision:
    return decide_kcal_adaptation(
        client=client,
        config=config,
        previous_weight_kg=previous_weight_kg,
        current_weight_kg=current_weight_kg,
        previous_kcal=previous.daily.kcal,
        previous_formula=previous.formula,
        admin_locked=is_admin_locked(previous),
    )


def _action_for(goal: Goal, delta_w: float, adapt: AdaptationConfig) -> tuple[AdaptAction, str]:
    tol = adapt.tolerance_kg
    if goal is Goal.LOSE_FAT:
        if delta_w <= adapt.lose_fat_too_fast_kg:
            return AdaptAction.RAISE, "lose_too_fast"
        if delta_w >= -tol:
            return AdaptAction.LOWER, "lose_stalled"
        return AdaptAction.HOLD, "hold_on_track"
    if goal is Goal.GAIN_MUSCLE:
        if delta_w >= adapt.gain_muscle_too_fast_kg:
            return AdaptAction.LOWER, "gain_too_fast"
        if delta_w <= tol:
            return AdaptAction.RAISE, "gain_stalled"
        return AdaptAction.HOLD, "hold_on_track"
    # maintain
    if abs(delta_w) <= tol:
        return AdaptAction.HOLD, "hold_on_track"
    if delta_w > 0:
        return AdaptAction.LOWER, "maintain_up"
    return AdaptAction.RAISE, "maintain_down"
