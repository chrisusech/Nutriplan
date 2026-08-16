"""Errores que ve la persona: formales, sin dumps ni códigos de proveedor."""

from pathlib import Path

from nutriplan.domain.errors import GenerationError, LLMError, ValidationError
from nutriplan.ports.iap import IAPError
from nutriplan.ui.web.public_errors import (
    GEN_FAILED,
    PURCHASE_FAILED,
    SWAP_FAILED,
    UNHANDLED,
    public_message,
    sanitize_public_error,
)


def test_un_dump_de_macros_no_llega_a_pantalla() -> None:
    raw = "No se logró cuadrar el plan tras 6 intentos. Detalle: día 2, day protein_g 74.7 vs 59.6"
    assert sanitize_public_error(raw) == GEN_FAILED
    assert public_message(GenerationError(raw)) == GEN_FAILED
    assert "protein_g" not in public_message(GenerationError(raw))


def test_un_429_del_proveedor_no_se_cita() -> None:
    exc = LLMError("El proveedor de IA rechazó la petición (429): quota exceeded")
    assert public_message(exc) == SWAP_FAILED
    assert "429" not in public_message(exc)
    assert sanitize_public_error(str(exc)) == UNHANDLED


def test_una_validacion_humana_se_respeta() -> None:
    assert public_message(ValidationError("Ese plato es de calle.")) == "Ese plato es de calle."


def test_la_plantilla_de_generacion_no_pinta_detalle() -> None:
    tpl = (
        Path(__file__).resolve().parents[2]
        / "src/nutriplan/ui/web/templates/partials/gen_error.html"
    )
    text = tpl.read_text(encoding="utf-8")
    assert "Detalle" not in text
    assert "Volver a generar" in text
    assert "No se pudo generar el menú." in text


def test_un_fallo_de_compra_no_cita_al_proveedor() -> None:
    assert public_message(IAPError("status 429 Gemini")) == PURCHASE_FAILED
    assert "429" not in public_message(IAPError("status 429"))
