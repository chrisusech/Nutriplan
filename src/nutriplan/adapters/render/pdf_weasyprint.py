"""Renderer PDF: Jinja2 (HTML/CSS) → WeasyPrint — rejilla 5×7 (Fase 7).

El diseño imita el formato real que entrega la entrenadora: página de
anotaciones, banner "Plan Nutricional", rejilla vertical de 7 días con la
cabecera en el color de marca, y pie con el nombre y el @handle.
"""

import base64
import mimetypes
from pathlib import Path
from uuid import UUID

from jinja2 import Environment, FileSystemLoader, select_autoescape

from nutriplan.adapters.render.color import MACRO_COLORS, mix_white, soft_of, tint_of
from nutriplan.adapters.render.view import (
    DAY_LABELS,
    PHASE_INTRO,
    SLOT_LABELS,
    SLOT_TIMES,
    anotaciones,
    build_grid,
    cell_for_template,
    day_totals_row,
    plan_phases_in,
    slots_in,
)
from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import Branding, FoodItem, MacroTargets, PlanCycle

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STYLESHEET = _TEMPLATES_DIR / "pdf.css"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _logo_data_uri(branding: Branding) -> str | None:
    """El logo, embebido en el HTML.

    `logo_path` es una ruta del filesystem del tenant; WeasyPrint la resolvería
    contra el `base_url` (el directorio de plantillas) y no la encontraría. Se
    codifica a data-URI. Un logo que falta nunca revienta el PDF.
    """
    if not branding.logo_path:
        return None
    path = Path(branding.logo_path)
    if not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _target_tiles(daily: MacroTargets) -> list[dict[str, str]]:
    """Los 4 macros del día, con el color de MACRO_COLORS (el mismo que la app)."""
    values = {
        "kcal": daily.kcal,
        "protein_g": daily.protein_g,
        "carb_g": daily.carb_g,
        "fat_g": daily.fat_g,
    }
    return [
        {
            "label": meta["label"],
            "color": meta["color"],
            "soft": meta["soft"],
            "value": f"{round(values[key])}{'' if key == 'kcal' else ' g'}",
        }
        for key, meta in MACRO_COLORS.items()
    ]


def _grid_sections(plan: PlanCycle, foods: dict[UUID, FoodItem]) -> list[dict]:
    sections: list[dict] = []
    for phase in plan_phases_in(plan):
        grid = build_grid(plan, foods, phase=phase)
        rows = [
            {
                "label": SLOT_LABELS[slot],
                "time": SLOT_TIMES[slot],
                "cells": [cell_for_template(c) for c in cells],
            }
            for slot, cells in grid.items()
        ]
        sections.append({
            "intro": PHASE_INTRO[phase],
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
    """HTML del PDF: anotaciones + una rejilla por fase."""
    daily = daily_totals or _average_daily(plan)
    if plan.duration_days >= 30:
        duration_note = "Plan 30 días (2 semanas)"
    elif plan.duration_days >= 15:
        duration_note = "Plan 15 días (1 semana)"
    else:
        duration_note = ""
    brand = branding.primary_color
    template = _jinja_env().get_template("plan_grid.html.j2")
    return template.render(
        plan=plan,
        branding=branding,
        brand=brand,
        brand_soft=soft_of(brand),
        brand_tint=tint_of(brand),
        brand_wash=mix_white(brand, 0.95),
        logo_data_uri=_logo_data_uri(branding),
        client_name=client_name or "Cliente",
        duration_note=duration_note,
        target_tiles=_target_tiles(daily),
        sections=_grid_sections(plan, foods),
        daily=daily,
        anotaciones=anotaciones(slots_in(plan)),
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
            from weasyprint import CSS, HTML
            from weasyprint.text.fonts import FontConfiguration

            # El FontConfiguration NO es opcional: sin él WeasyPrint ignora el
            # @font-face EN SILENCIO y cae a Helvetica. Hay que pasárselo tanto al
            # CSS que declara la fuente como al write_pdf. Así llevaba meses
            # saliendo el PDF en una tipografía distinta a la de la app.
            # `base_url` apunta al directorio de plantillas para que
            # `fonts/Poppins-*.ttf` resuelva.
            font_config = FontConfiguration()
            stylesheet = CSS(filename=str(_STYLESHEET), font_config=font_config)
            pdf: bytes = HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf(
                stylesheets=[stylesheet], font_config=font_config
            )
            return pdf
        except RenderError:
            raise
        except Exception as exc:
            raise RenderError(f"WeasyPrint falló: {exc}") from exc
