"""Copy público: formal, sin dumps técnicos, sin códigos de proveedor.

El detalle (macros, 429, cuerpo de Gemini, tokens) se queda en logs y en
admin. Esta pieza es el único sitio que decide qué frase ve la persona.
"""

from __future__ import annotations

from nutriplan.domain.errors import GenerationError, LLMError, ValidationError
from nutriplan.ports.iap import IAPError

GEN_FAILED = (
    "No fue posible armar el menú con los alimentos seleccionados. "
    "Añada carbohidratos y grasas, o deje el catálogo vacío para que elijamos nosotros."
)
UNHANDLED = "No pudimos completar la solicitud. Inténtelo de nuevo en unos minutos."
SWAP_UNAVAILABLE = "Ese plato no está en el menú."
SWAP_FAILED = "No pudimos cambiar el plato con esa petición. Pruebe con otras palabras."
RECIPE_FAILED = "No fue posible cargar la receta. Puede reintentar."
PURCHASE_FAILED = "Esta compra no se pudo verificar."
JOB_STALE = "La generación se interrumpió. Vuelva a intentarlo."


def public_message(exc: BaseException) -> str:
    """Traduce una excepción de dominio a una frase que sí puede ir a pantalla."""
    if isinstance(exc, IAPError):
        return PURCHASE_FAILED
    if isinstance(exc, LLMError):
        return SWAP_FAILED
    if isinstance(exc, GenerationError):
        return _from_generation(str(exc))
    if isinstance(exc, ValidationError):
        return sanitize_public_error(str(exc)) or SWAP_FAILED
    return UNHANDLED


def sanitize_public_error(raw: str) -> str:
    """Si alguien coló un dump en `?error=`, no llega a la plantilla."""
    text = (raw or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if any(token in lower for token in ("detalle:", "429", "gemini", "cuota", "proveedor de ia")):
        return GEN_FAILED if "intentos" in lower or "detalle:" in lower else UNHANDLED
    if "protein_g" in lower or "carb_g" in lower or "fat_g" in lower:
        return GEN_FAILED
    return text


def _from_generation(message: str) -> str:
    lower = message.lower()
    if "ningún alimento" in lower or "ningun alimento" in lower:
        return (
            "No quedó ningún alimento disponible. Revise las restricciones "
            "y lo que marcó que no quiere ver."
        )
    return GEN_FAILED
