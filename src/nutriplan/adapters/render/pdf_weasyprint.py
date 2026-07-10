"""Renderer PDF: Jinja2 (HTML/CSS) → WeasyPrint.

Determinista: mismo plan → mismo HTML; el PDF es byte-estable salvo
metadatos de fecha de WeasyPrint.
"""

from pathlib import Path
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nutriplan.adapters.render.view import (
    ANOTACIONES_IMPORTANTES,
    WEEK_SUBTITLE,
    build_week,
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
    """Tinte claro del color de marca para los fondos (mezcla con blanco al 90%)."""
    c = hex_color.lstrip("#")
    if len(c) != 6:
        return "#FBF7F4"
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    m = tuple(round(x + (255 - x) * 0.9) for x in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(*m)


def render_plan_html(
    plan: PlanCycle,
    branding: Branding,
    foods: dict[UUID, FoodItem],
    *,
    client_name: str | None = None,
    daily_totals: MacroTargets | None = None,
) -> str:
    """HTML intermedio del PDF semanal (portada + 7 días). También lo usan los
    snapshot tests."""
    week = build_week(plan, foods)
    daily = daily_totals or _average_daily(plan)
    template = _jinja_env().get_template("plan_semanal.html.j2")
    return template.render(
        plan=plan,
        branding=branding,
        brand=branding.primary_color,
        brand_soft=_soft_of(branding.primary_color),
        client_name=client_name or "Cliente",
        subtitle=WEEK_SUBTITLE,
        week=week,
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
    ) -> bytes:
        if fmt != "pdf":
            raise RenderError(f"WeasyPrintRenderer solo produce pdf, no {fmt}")
        html = render_plan_html(plan, branding, foods, client_name=client_name)
        try:
            from weasyprint import HTML

            pdf: bytes = HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf()
            return pdf
        except RenderError:
            raise
        except Exception as exc:  # errores de librería → error tipado del dominio
            raise RenderError(f"WeasyPrint falló: {exc}") from exc
