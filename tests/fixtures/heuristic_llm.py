"""Doble de prueba del LLM para generación.

Promovido a adaptador de producción (modo offline de la UI); se re-exporta
aquí para que los tests sigan importando desde fixtures.
"""

from nutriplan.adapters.llm.heuristic import HeuristicSelector

__all__ = ["HeuristicSelector"]
