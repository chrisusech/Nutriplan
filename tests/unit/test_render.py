"""Módulo 5: snapshot del HTML intermedio + smoke de PDF y DOCX."""

from io import BytesIO

import pytest
from tests.fixtures.plan_builder import build_fixed_plan, catalog_by_name

from nutriplan.adapters.render.docx_renderer import DocxRenderer
from nutriplan.adapters.render.pdf_weasyprint import WeasyPrintRenderer, render_plan_html
from nutriplan.adapters.render.view import natural_units, portion_text
from nutriplan.domain.errors import RenderError


@pytest.fixture(scope="module")
def fixed_plan():
    return build_fixed_plan()


def test_html_snapshot(fixed_plan, snapshot) -> None:
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    assert html == snapshot


def test_html_is_deterministic(fixed_plan) -> None:
    plan, foods, branding = fixed_plan
    a = render_plan_html(plan, branding, foods)
    b = render_plan_html(plan, branding, foods)
    assert a == b


def test_html_contains_business_format(fixed_plan) -> None:
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    for label in ("Desayuno", "Snack AM", "Almuerzo", "Snack PM", "Cena"):
        assert label in html
    for day in ("Lunes", "Domingo"):
        assert day in html
    assert "Anotaciones importantes" in html
    assert "Ensalada libre" in html
    assert "Valeria Fit" in html
    assert "2 huevos (100 g)" in html
    assert "Totales día" in html
    assert "Objetivo diario" in html
    assert "Plan 15 días (1 semana)" in html
    assert "<table" in html
    assert "Cliente Ejemplo" in html


def test_thirty_day_pdf_has_two_week_grids(fixed_plan) -> None:
    from nutriplan.domain.models import DayPlan, PlanPhase

    plan, foods, branding = fixed_plan
    second_week = [
        DayPlan(
            day_index=d.day_index,
            phase=PlanPhase.NEXT_15,
            meals=d.meals,
            totals=d.totals,
        )
        for d in plan.days
    ]
    plan = plan.model_copy(update={"duration_days": 30, "days": plan.days + second_week})
    html = render_plan_html(plan, branding, foods, client_name="Cliente")
    assert html.count('<table class="grid">') == 2
    assert "Semana 1" in html
    assert "Semana 2" in html
    assert "Plan 30 días (2 semanas)" in html


async def test_pdf_renders(fixed_plan) -> None:
    plan, foods, branding = fixed_plan
    pdf = await WeasyPrintRenderer().render(plan, branding, foods, "pdf")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000


async def test_docx_renders_and_reopens(fixed_plan) -> None:
    from docx import Document

    plan, foods, branding = fixed_plan
    blob = await DocxRenderer().render(plan, branding, foods, "docx")
    doc = Document(BytesIO(blob))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Valeria Fit" in text
    assert "Anotaciones Importantes" in text


async def test_wrong_format_rejected(fixed_plan) -> None:
    plan, foods, branding = fixed_plan
    with pytest.raises(RenderError):
        await WeasyPrintRenderer().render(plan, branding, foods, "docx")
    with pytest.raises(RenderError):
        await DocxRenderer().render(plan, branding, foods, "pdf")


async def test_missing_food_raises_render_error(fixed_plan) -> None:
    plan, _foods, branding = fixed_plan
    with pytest.raises(RenderError):
        await WeasyPrintRenderer().render(plan, branding, {}, "pdf")


def test_natural_units() -> None:
    foods = catalog_by_name()
    huevo = foods["huevo entero"]  # 50 g/und, whole
    assert natural_units(100, huevo) == "2 huevos"
    assert natural_units(50, huevo) == "1 huevo"
    banano = foods["banano"]  # 120 g/und, half
    assert natural_units(60, banano) == "½ unidad"
    # gramos libres (grasas, incl. aguacate ahora, y proteínas) no llevan unidad
    assert natural_units(60, foods["aguacate"]) is None
    assert natural_units(10, foods["aceite de oliva"]) is None
    assert natural_units(120, foods["pechuga de pollo"]) is None


def test_portion_text_units_first() -> None:
    foods = catalog_by_name()
    assert portion_text(150, foods["huevo entero"]) == "3 huevos (150 g)"
    assert portion_text(100, foods["atún en agua"]) == "1 lata de atún en agua (100 g)"
    assert portion_text(120, foods["pechuga de pollo"]) == "Pechuga de pollo — 120 g"
