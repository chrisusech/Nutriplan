"""Módulo 5: snapshot del HTML intermedio + smoke de PDF y DOCX."""

from io import BytesIO

import pytest

from nutriplan.adapters.render.docx_renderer import DocxRenderer
from nutriplan.adapters.render.pdf_weasyprint import WeasyPrintRenderer, render_plan_html
from nutriplan.adapters.render.view import natural_units
from nutriplan.domain.errors import RenderError
from tests.fixtures.plan_builder import build_fixed_plan, catalog_by_name


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
    html = render_plan_html(plan, branding, foods)
    for label in ("Desayuno", "Snack AM", "Almuerzo", "Snack PM", "Cena"):
        assert label in html
    for day in ("Lunes", "Domingo"):
        assert day in html
    assert "Anotaciones Importantes" in html
    assert "Ensalada libre" in html
    assert "Valeria Fit" in html
    assert "Huevo entero — 100 g (≈ 2 und)" in html


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
    huevo = foods["huevo entero"]  # 50 g/und
    assert natural_units(100, huevo) == "≈ 2 und"
    assert natural_units(75, huevo) == "≈ 1.5 und"
    assert natural_units(1000, huevo) is None  # fuera de rango razonable
    aceite = foods["aceite de oliva"]
    assert natural_units(10, aceite) == "≈ 1 und"
