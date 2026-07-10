"""Taxonomía de errores del dominio (sección 17 de la spec).

Regla: nada de `except: pass`. Todo error se registra con contexto y, si es de
datos, se convierte en "marcar para revisión", no en una suposición.
El mapeo a HTTP ocurre en la capa API (Nivel 2), no aquí.
"""


class NutriPlanError(Exception):
    """Base de todos los errores de dominio."""


class IntakeAmbiguityError(NutriPlanError):
    """Falta o es dudoso un dato del intake (→ NEEDS_REVIEW, HTTP 422)."""

    def __init__(self, ambiguities: list[str]) -> None:
        self.ambiguities = ambiguities
        super().__init__(f"Intake ambiguo: {', '.join(ambiguities)}")


class FoodNotFoundError(NutriPlanError):
    """Alimento de la lista no está en la base (HTTP 422)."""


class CalculationError(NutriPlanError):
    """Datos insuficientes para calcular targets (HTTP 422)."""


class ValidationError(NutriPlanError):
    """Datos de entrada inválidos para un caso de uso (HTTP 422)."""


class GenerationError(NutriPlanError):
    """No se logró cuadrar macros tras max_retries (HTTP 422)."""


class RenderError(NutriPlanError):
    """Falla al generar PDF/DOCX (HTTP 500)."""


class LLMError(NutriPlanError):
    """Falla del proveedor de IA o esquema inválido tras reintentos (HTTP 502)."""


class TenantIsolationError(NutriPlanError):
    """Acceso cruzado entre tenants — nunca debería pasar (HTTP 403)."""
