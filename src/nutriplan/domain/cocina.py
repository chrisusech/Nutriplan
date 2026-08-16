"""Gramos del plato (cocido) → gramos de compra (crudo)."""

from nutriplan.domain.models import FoodItem, FoodState


def gramos_en_crudo(food: FoodItem, gramos: float) -> float:
    """Gramos a comprar. Sin factor se deja el del plato: no se inventa un número."""
    if food.state is not FoodState.COOKED or not food.yield_factor:
        return gramos
    return gramos / food.yield_factor


def convierte_a_crudo(food: FoodItem) -> bool:
    """Si para este alimento el peso de compra y el del plato son distintos."""
    return food.state is FoodState.COOKED and bool(food.yield_factor)
