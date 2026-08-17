"""El anillo de kcal se pinta en el servidor; el JS no puede pisarlo.

Marcar comido swappea el botón y el anillo por OOB. afterSettle de HTMX 2
dispara con detail.target = el form viejo (kcal=0). Hay que leer event.target,
que es el nodo recién asentado.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECIPES = (ROOT / "src/nutriplan/ui/web/static/js/recipes.js").read_text(encoding="utf-8")
WEEK = (ROOT / "src/nutriplan/ui/web/templates/week.html").read_text(encoding="utf-8")
DIA = (ROOT / "src/nutriplan/ui/web/templates/partials/dia_track.html").read_text(
    encoding="utf-8"
)


def test_el_anillo_no_se_repinta_con_el_formulario_viejo() -> None:
    assert "const form = event.target;" in RECIPES
    assert "if (!form?.matches?.('.meal-check')) return;" in RECIPES
    assert "pintaHero(form)" in RECIPES
    assert "if (!target?.matches?.('.meal-check')) return;" not in RECIPES
    assert "elt?.closest?.('.meal-check')" not in RECIPES


def test_cambiar_de_dia_no_recarga_el_webview() -> None:
    """Un <a href> suelto pinta el fondo nativo negro entre un día y el otro."""
    assert 'id="week-day"' in WEEK
    assert 'hx-target="#week-day"' in WEEK
    assert 'hx-select="#week-day"' in WEEK
    assert 'hx-boost="true"' in WEEK
    assert "show:none" in WEEK


def test_las_comidas_no_entran_desde_opacidad_cero() -> None:
    """El stagger dejaba un fogonazo oscuro al pintar el día."""
    assert 'class="meals stagger"' not in DIA
    assert 'class="meals"' in DIA
