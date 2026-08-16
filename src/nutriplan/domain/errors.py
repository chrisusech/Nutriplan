"""Taxonomía de errores del dominio (sección 17 de la spec).

Regla: nada de `except: pass`. Todo error se registra con contexto y, si es de
datos, se convierte en "marcar para revisión", no en una suposición.
El mapeo a HTTP ocurre en la capa API (Nivel 2), no aquí.
"""


class NutriPlanError(Exception):
    """Base de todos los errores de dominio."""


class CalculationError(NutriPlanError):
    """Datos insuficientes para calcular targets (HTTP 422)."""


class ValidationError(NutriPlanError):
    """Datos de entrada inválidos para un caso de uso (HTTP 422)."""


class GenerationError(NutriPlanError):
    """No se logró cuadrar macros tras max_retries (HTTP 422)."""


class LLMError(NutriPlanError):
    """Falla del proveedor de IA o esquema inválido tras reintentos (HTTP 502)."""


class TenantIsolationError(NutriPlanError):
    """Acceso cruzado entre tenants — nunca debería pasar (HTTP 403)."""


class FoodNotFoundError(NutriPlanError):
    """Se pidió corregir un alimento que no está en la base (HTTP 404)."""


class MembershipError(NutriPlanError):
    """La cuenta no tiene semanas para generar (HTTP 403).

    Lleva el mensaje que se le muestra a la persona: no es lo mismo «se te
    venció el plan» que «te falta cerrar la semana»."""
