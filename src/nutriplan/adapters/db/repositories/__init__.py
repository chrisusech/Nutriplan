"""Repositorios SQLAlchemy, uno por agregado.

Vivian en un solo archivo de 1.209 lineas que crecia en cada fase. Se
reexportan aqui para que quien los importa no se entere del cambio.
"""

from nutriplan.adapters.db.repositories.audit import SqlAuditLogRepository
from nutriplan.adapters.db.repositories.auth import SqlAccountEraser, SqlAuthRepository
from nutriplan.adapters.db.repositories.client import SqlClientRepository
from nutriplan.adapters.db.repositories.device_token import SqlDeviceTokenRepository
from nutriplan.adapters.db.repositories.event import SqlEventRepository
from nutriplan.adapters.db.repositories.food import SqlFoodRepository
from nutriplan.adapters.db.repositories.job import SqlJobRepository
from nutriplan.adapters.db.repositories.metrics import Funnel, SqlMetricsRepository
from nutriplan.adapters.db.repositories.plan import SqlPlanRepository
from nutriplan.adapters.db.repositories.rating import SqlRatingRepository
from nutriplan.adapters.db.repositories.recipe import SqlDishRecipeRepository
from nutriplan.adapters.db.repositories.targets import SqlTargetsRepository

__all__ = [
    "SqlAccountEraser",
    "SqlAuditLogRepository",
    "SqlAuthRepository",
    "SqlClientRepository",
    "SqlDeviceTokenRepository",
    "SqlEventRepository",
    "SqlDishRecipeRepository",
    "Funnel",
    "SqlFoodRepository",
    "SqlMetricsRepository",
    "SqlJobRepository",
    "SqlPlanRepository",
    "SqlRatingRepository",
    "SqlTargetsRepository",
]
