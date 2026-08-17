"""El cuestionario tiene que avanzar al tocar Continuar.

En el iPhone la primera pregunta se veía bien y ningún botón respondía:
Continuar no tenía listener, y la flecha de volver seguía pintada porque
el CSS le ganaba al atributo hidden.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STEPPER = (ROOT / "src/nutriplan/ui/web/static/js/stepper.js").read_text(encoding="utf-8")
ONB = (ROOT / "src/nutriplan/ui/web/templates/onboarding.html").read_text(encoding="utf-8")
SHELL = (ROOT / "src/nutriplan/ui/web/static/styles/shell.css").read_text(encoding="utf-8")


def test_continuar_escucha_el_clic() -> None:
    """Sin el listener, Continuar se ve y no hace nada."""
    assert "next?.addEventListener('click'" in STEPPER
    assert "avanza()" in STEPPER


def test_en_el_primer_paso_la_flecha_vuelve_al_inicio() -> None:
    """Sin cerrar sesión, /login rebota al cuestionario."""
    assert 'action="/logout"' in ONB
    assert "data-leave" in ONB
    assert "[data-leave]" in STEPPER
    assert "assign('/')" not in STEPPER


def test_un_error_abre_el_ultimo_paso() -> None:
    """Crear mi menú falló: no devolver a ¿cómo te llamas?."""
    assert "querySelector('.alert')" in STEPPER
    assert "steps.length - 1" in STEPPER


def test_la_flecha_de_volver_no_se_pinta_si_esta_oculta() -> None:
    """`.back { display: flex }` no puede ganar a `[hidden]`."""
    assert ".back[hidden]" in SHELL
    assert "display: none" in SHELL.split(".back[hidden]")[1].split("}")[0]
