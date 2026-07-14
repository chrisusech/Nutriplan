"""Módulo 5: snapshot del HTML intermedio + smoke de PDF y DOCX."""

import re
import zlib
from io import BytesIO

import pytest
from tests.fixtures.plan_builder import build_fixed_plan, catalog_by_name

from nutriplan.adapters.render.docx_renderer import DocxRenderer
from nutriplan.adapters.render.pdf_weasyprint import WeasyPrintRenderer, render_plan_html
from nutriplan.adapters.render.view import natural_units, portion_text
from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import MacroTargets, MealSlot


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


def test_the_notes_say_what_the_numbers_do_cooked_not_raw(fixed_plan) -> None:
    """El catálogo lleva macros de alimento COCIDO; la nota decía "pesa en CRUDO".

    Quien pesara 120 g de pollo crudo se comía ~85 g cocidos: un 30% menos de
    proteína de la que el plan le prometía. La nota ahora dice lo que el motor
    calcula.
    """
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    assert "COCIDO" in html
    assert "CRUDO" not in html


def test_the_notes_count_the_meals_the_plan_really_has() -> None:
    """A quien recibe cuatro comidas no se le promete un plan de cinco."""
    plan, foods, branding = build_fixed_plan()  # propio: no se toca el del módulo
    html = render_plan_html(plan, branding, foods)
    assert "El plan consta de 5 comidas" in html

    for day in plan.days:  # el mismo plan, sin snack PM
        day.meals = [m for m in day.meals if m.slot is not MealSlot.SNACK_PM]
    html = render_plan_html(plan, branding, foods)
    assert "El plan consta de 4 comidas: desayuno, snack AM, almuerzo y cena." in html
    assert "Snack PM" not in html  # tampoco hay fila vacía en la rejilla


def test_html_contains_business_format(fixed_plan) -> None:
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    for label in ("Desayuno", "Snack AM", "Almuerzo", "Snack PM", "Cena"):
        assert label in html
    for day in ("Lunes", "Domingo"):
        assert day in html
    assert "Anotaciones Importantes" in html
    assert "Ensalada libre" in html
    assert "Valeria Fit" in html
    assert "@valeria.fit" in html  # el handle, que antes nunca se pintaba
    assert "2 huevos (100 g)" in html
    assert "Totales día" in html
    assert "Tu objetivo diario" in html
    assert "<table" in html


def test_the_grid_carries_no_clock_and_no_cover_line(fixed_plan) -> None:
    """El plan dice QUÉ come, no a qué hora; y no se abre con una ficha del cliente."""
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    for hora in ("7:00 am", "10:30 am", "1:00 pm", "4:30 pm", "7:30 pm"):
        assert hora not in html
    assert "Plan de" not in html
    assert "Plan 15 días" not in html


def test_the_notes_explain_that_the_week_is_what_closes(fixed_plan) -> None:
    """Un día puede quedar un poco arriba o abajo; lo que cuadra es la semana."""
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods)
    assert "SEMANA" in html
    assert "la pasta, etc.)" in html


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
    assert "primeros 15 días" in html
    assert "próximos 15 días" in html


async def test_pdf_renders(fixed_plan) -> None:
    plan, foods, branding = fixed_plan
    pdf = await WeasyPrintRenderer().render(plan, branding, foods, "pdf")
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 10_000


def _pdf_font_names(pdf: bytes) -> set[str]:
    """Los /BaseFont del PDF. Van dentro de object streams comprimidos, así que un
    grep sobre los bytes crudos no los ve — y daría un falso negativo."""
    names: set[str] = set()
    for match in re.finditer(rb"stream\r?\n", pdf):
        start = match.end()
        end = pdf.find(b"endstream", start)
        try:
            blob = zlib.decompress(pdf[start:end])
        except zlib.error:
            continue
        for font in re.findall(rb"/BaseFont\s*/([A-Za-z0-9+\-]+)", blob):
            names.add(font.decode("latin1"))
    return names


async def test_pdf_embeds_poppins(fixed_plan) -> None:
    """La fuente de marca tiene que VIAJAR dentro del PDF.

    WeasyPrint no descarga webfonts declaradas solo con `font-family`, y además
    ignora el @font-face EN SILENCIO si no se le pasa un FontConfiguration: el
    PDF salía en Helvetica mientras la app usaba Poppins y ningún test lo veía,
    porque todos miraban el HTML en vez de los bytes del PDF.
    """
    plan, foods, branding = fixed_plan
    pdf = await WeasyPrintRenderer().render(plan, branding, foods, "pdf")
    fonts = _pdf_font_names(pdf)
    assert any("Poppins" in f for f in fonts), fonts
    assert not any("Helvetica" in f for f in fonts), fonts


async def test_pdf_uses_the_tenant_brand_color(fixed_plan) -> None:
    """El PDF pinta el coral de la app, no el verde del default viejo."""
    plan, foods, branding = fixed_plan
    html = render_plan_html(plan, branding, foods, client_name="Cliente")
    assert "--brand: #F26D5B" in html
    assert "#2E7D32" not in html


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


def test_the_free_meal_is_a_cell_and_the_note_names_its_day() -> None:
    """La frase de la comida libre salía en TODOS los planes, tuvieran una o no.

    Era texto fijo ("puedes hacerla el sábado o el domingo, según prefieras") porque
    la comida libre no existía como dato. Ahora la celda está en la rejilla y la
    anotación dice el día y la comida de verdad — o no dice nada.
    """
    plan, foods, branding = build_fixed_plan()

    # Sin comida libre no se promete ninguna.
    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    assert "COMIDA LIBRE" not in html
    assert "comida libre" not in html.lower()

    # Con ella: la celda en la rejilla, y la anotación con su día y su comida.
    sunday = plan.days[6]
    freed = [
        m.model_copy(update={
            "items": [],
            "computed": MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
            "is_free_meal": True,
        })
        if m.slot is MealSlot.DINNER else m
        for m in sunday.meals
    ]
    plan = plan.model_copy(update={
        "days": [*plan.days[:6], sunday.model_copy(update={"meals": freed})]
    })

    html = render_plan_html(plan, branding, foods, client_name="Cliente Ejemplo")
    assert "COMIDA LIBRE" in html
    assert "free-meal" in html  # la celda lleva su propio estilo
    assert "Tu comida libre es el domingo en el cena" in html or (
        "comida libre es el domingo" in html
    )
