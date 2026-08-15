"""Repositorios SQLAlchemy, uno por agregado.

Vivian en un solo archivo de 1.209 lineas que crecia en cada fase. Se
reexportan aqui para que quien los importa no se entere del cambio.
"""

from nutriplan.adapters.db.repositories.admin import (
    AccountOverview,
    AutoWeekCandidate,
    SqlAdminRepository,
)
from nutriplan.adapters.db.repositories.auth import SqlAccountEraser, SqlAuthRepository
from nutriplan.adapters.db.repositories.client import SqlClientRepository
from nutriplan.adapters.db.repositories.device_token import SqlDeviceTokenRepository
from nutriplan.adapters.db.repositories.event import SqlEventRepository
from nutriplan.adapters.db.repositories.food import SqlFoodRepository
from nutriplan.adapters.db.repositories.job import SqlJobRepository
from nutriplan.adapters.db.repositories.membership import SqlMembershipRepository
from nutriplan.adapters.db.repositories.metrics import (
    Funnel,
    ProductPulse,
    SqlMetricsRepository,
)
from nutriplan.adapters.db.repositories.metrics_export import SqlMetricsExportRepository
from nutriplan.adapters.db.repositories.metrics_voices import SqlMetricsVoicesRepository
from nutriplan.adapters.db.repositories.password_reset import SqlPasswordResetRepository
from nutriplan.adapters.db.repositories.plan import SqlPlanRepository
from nutriplan.adapters.db.repositories.rating import SqlRatingRepository
from nutriplan.adapters.db.repositories.recipe import SqlDishRecipeRepository
from nutriplan.adapters.db.repositories.targets import SqlTargetsRepository
from nutriplan.adapters.db.repositories.taste import (
    SqlTasteSignalRepository,
    TasteSignals,
)
from nutriplan.adapters.db.repositories.weight import SqlWeightRepository

__all__ = [
    "AccountOverview",
    "AutoWeekCandidate",
    "SqlAccountEraser",
    "SqlAdminRepository",
    "SqlAuthRepository",
    "SqlClientRepository",
    "SqlDeviceTokenRepository",
    "SqlEventRepository",
    "SqlDishRecipeRepository",
    "Funnel",
    "ProductPulse",
    "SqlFoodRepository",
    "SqlMembershipRepository",
    "SqlMetricsExportRepository",
    "SqlMetricsRepository",
    "SqlMetricsVoicesRepository",
    "SqlJobRepository",
    "SqlPasswordResetRepository",
    "SqlPlanRepository",
    "SqlRatingRepository",
    "SqlTargetsRepository",
    "SqlTasteSignalRepository",
    "SqlWeightRepository",
    "TasteSignals",
]
