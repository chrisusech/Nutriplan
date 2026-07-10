"""Renderer PDF: Jinja2 (HTML/CSS) → WeasyPrint.

Determinista: mismo plan → mismo HTML; el PDF es byte-estable salvo
metadatos de fecha de WeasyPrint.
"""

from pathlib import Path
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nutriplan.adapters.render.view import (
    ANOTACIONES_IMPORTANTES,
    DAY_LABELS,
    SLOT_LABELS,
    WEEK_SUBTITLE,
    build_grid,
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


def render_plan_html(
    plan: PlanCycle,
    branding: Branding,
    foods: dict[UUID, FoodItem],
    *,
    client_name: str | None = None,
    daily_totals: MacroTargets | None = None,
) -> str:
    """HTML intermedio (también usado por los snapshot tests)."""
    grid = build_grid(plan, foods)
    daily = daily_totals or _average_daily(plan)
    template = _jinja_env().get_template("plan.html.j2")
    return template.render(
        plan=plan,
        branding=branding,
        client_name=client_name,
        phase_label=WEEK_SUBTITLE,
        day_labels=DAY_LABELS,
        slot_labels=SLOT_LABELS,
        grid=grid,
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
    ) -> bytes:
        if fmt != "pdf":
            raise RenderError(f"WeasyPrintRenderer solo produce pdf, no {fmt}")
        html = render_plan_html(plan, branding, foods)
        try:
            from weasyprint import HTML

            pdf: bytes = HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()
            return pdf
        except RenderError:
            raise
        except Exception as exc:  # errores de librería → error tipado del dominio
            raise RenderError(f"WeasyPrint falló: {exc}") from exc
