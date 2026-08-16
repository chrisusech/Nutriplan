"""Lo que comió la semana pasada: para no servirle el mismo caldo otra vez."""

from __future__ import annotations

from datetime import date

from nutriplan.domain.models import PlanCycle


def dishes_of_previous_week(
    plans: list[PlanCycle], week: date
) -> tuple[frozenset[str], frozenset[str]]:
    """Platos ya servidos hasta esta semana (incluida).

    «Generar otra semana» borra el borrador *después* de armar el menú. Si
    solo se miraba `week_start < week`, regenerar el mismo lunes no veía
    los platos que acababa de servir y repetía el caldo. Las claves de
    todas las semanas ya vividas se penalizan; las plantillas, solo las
    del plan más reciente, para no dejar el molde entero a precio uniforme.
    """
    served = [p for p in plans if p.week_start <= week]
    if not served:
        return frozenset(), frozenset()
    keys = frozenset(m.dish_key for p in served for d in p.days for m in d.meals if m.dish_key)
    last = max(served, key=lambda p: (p.week_start, p.variant))
    templates = frozenset(t for d in last.days for m in d.meals if (t := m.template_id))
    return keys, templates
