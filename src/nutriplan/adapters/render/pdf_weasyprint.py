"""Renderer PDF: Jinja2 (HTML/CSS) → WeasyPrint — rejilla 5×7 (Fase 7)."""

from pathlib import Path
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nutriplan.adapters.render.view import (
    ANOTACIONES_IMPORTANTES,
    DAY_LABELS,
    PHASE_SUBTITLES,
    SLOT_LABELS,
    build_grid,
    cell_for_template,
    day_totals_row,
    macro_line,
    plan_phases_in,
)
from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import Branding, FoodItem, MacroTargets, PlanCycle

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _soft_of(hex_color: str) -> str:
    c = hex_color.lstrip("#")
    if len(c) != 6:
        return "#FBF7F4"
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    m = tuple(round(x + (255 - x) * 0.9) for x in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(*m)


def _grid_sections(plan: PlanCycle, foods: dict[UUID, FoodItem]) -> list[dict]:
    sections: list[dict] = []
    phases = plan_phases_in(plan)
    multi = len(phases) > 1 or plan.duration_days >= 30
    for phase in phases:
        grid = build_grid(plan, foods, phase=phase)
        rows = []
        for slot, cells in grid.items():
            rows.append({
                "label": SLOT_LABELS[slot],
                "cells": [cell_for_template(c) for c in cells],
            })
        title = PHASE_SUBTITLES.get(phase, "") if multi else None
        sections.append({
            "title": title,
            "days": DAY_LABELS,
            "rows": rows,
            "day_totals": [
                {
                    "kcal": dt.kcal,
                    "protein_g": dt.protein_g,
                    "carb_g": dt.carb_g,
                    "fat_g": dt.fat_g,
                }
                for dt in day_totals_row(plan, phase)
            ],
        })
    return sections


def render_plan_html(
    plan: PlanCycle,
    branding: Branding,
    foods: dict[UUID, FoodItem],
    *,
    client_name: str | None = None,
    daily_totals: MacroTargets | None = None,
) -> str:
    """HTML del PDF en rejilla 5×7 (una sección por fase si aplica)."""
    daily = daily_totals or _average_daily(plan)
    duration_note = ""
    if plan.duration_days >= 30:
        duration_note = " · Plan 30 días (2 semanas)"
    elif plan.duration_days >= 15:
        duration_note = " · Plan 15 días (1 semana)"
    template = _jinja_env().get_template("plan_grid.html.j2")
    return template.render(
        plan=plan,
        branding=branding,
        brand=branding.primary_color,
        brand_soft=_soft_of(branding.primary_color),
        client_name=client_name or "Cliente",
        macro_subtitle=macro_line(daily) + duration_note,
        sections=_grid_sections(plan, foods),
        daily=daily,
        anotaciones=ANOTACIONES_IMPORTANTES,
    )


def _average_daily(plan: PlanCycle) -> MacroTargets:
    n = max(len(plan.days), 1)
    return MacroTargets(
        kcal=sum(d.totals.kcal for d in plan.days) / n,
        protein_g=sum(d.totals.protein_g for d in plan.days) / n,
        carb_g=sum(d.totals.carb_g for d in plan.days) / n,
        fat_g=sum(d.totals.fat_g for d in plan.days) / n,
    )


class WeasyPrintRenderer:
    """Implementa el puerto Renderer para fmt='pdf'."""

    async def render(
        self,
        plan: PlanCycle,
        branding: Branding,
        foods: dict[UUID, FoodItem],
        fmt: str = "pdf",
        client_name: str | None = None,
        daily_targets: MacroTargets | None = None,
    ) -> bytes:
        if fmt != "pdf":
            raise RenderError(f"WeasyPrintRenderer solo produce pdf, no {fmt}")
        html = render_plan_html(
            plan, branding, foods, client_name=client_name, daily_totals=daily_targets
        )
        try:
            from weasyprint import HTML

            pdf: bytes = HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()
            return pdf
        except RenderError:
            raise
        except Exception as exc:
            raise RenderError(f"WeasyPrint falló: {exc}") from exc
