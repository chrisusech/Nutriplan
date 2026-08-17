"""El paywall vende el plan; no parece un login de Apple."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = (ROOT / "src/nutriplan/ui/web/templates/plan.html").read_text(encoding="utf-8")
NATIVE = (ROOT / "src/nutriplan/ui/web/static/js/native.js").read_text(encoding="utf-8")
WEEK = (ROOT / "src/nutriplan/ui/web/templates/week.html").read_text(encoding="utf-8")
MEMBERSHIP = (ROOT / "src/nutriplan/domain/membership.py").read_text(encoding="utf-8")


def test_el_boton_dice_suscribirme_no_continuar_con_apple() -> None:
    assert "Suscribirme" in PLAN
    assert "Continuar con Apple" not in PLAN


def test_el_paywall_cuenta_lo_que_compra() -> None:
    assert "Tu menú de la semana, sin pensar qué cocinar" in PLAN
    assert "7 días, tus comidas" in PLAN
    assert "/privacidad" in PLAN
    assert "/terminos" in PLAN
    assert "calendar_view_week" not in PLAN
    assert ">scale<" not in PLAN
    assert "Apple cobra" not in PLAN
    assert "Nosotros" not in PLAN


def test_el_precio_se_ve_antes_de_abrir_apple() -> None:
    assert "$5 USD" in PLAN
    assert "$30 USD" in PLAN
    assert "Apple muestra el precio en su hoja." not in NATIVE
    assert "purchaseProduct" in NATIVE


def test_el_aviso_sin_semanas_llama_a_activar() -> None:
    assert "Accede a semanas ilimitadas. Activa tu plan." in MEMBERSHIP
    assert "Ya usaste las semanas que tenías" not in MEMBERSHIP


def test_activar_el_plan_desde_la_semana_va_al_paywall() -> None:
    assert 'href="/plan"' in WEEK
    assert 'href="/soporte"' not in WEEK
