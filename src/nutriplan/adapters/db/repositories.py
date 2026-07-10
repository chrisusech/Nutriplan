"""Repositorios SQLAlchemy con filtro automático de tenant.

Cada repositorio recibe el tenant_id del contexto y lo aplica en TODA
consulta y escritura. Por construcción, un tenant no puede ver ni tocar
datos de otro (sección 7.2).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AuditLogRow,
    ClientFoodPreferenceRow,
    ClientRow,
    DayPlanRow,
    ExportArtifactRow,
    FoodRow,
    GenerationJobRow,
    IntakeDocumentRow,
    MealEntryRow,
    NutritionTargetsRow,
    PlanCycleRow,
    RecipeRow,
    TenantRow,
    UserRow,
)
from nutriplan.domain.errors import TenantIsolationError
from nutriplan.domain.food_matching import normalize
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    DayPlan,
    FoodCategory,
    FoodItem,
    Goal,
    IntakeDocument,
    IntakeStatus,
    MacroFormula,
    MacroTargets,
    MealEntry,
    MealFoodPortion,
    MealSlot,
    NutritionTargets,
    PlanCycle,
    PlanPhase,
    PlanStatus,
    Recipe,
    RecipeIngredient,
    RecipeStatus,
    Sex,
    Trainer,
    UnitGranularity,
)
from nutriplan.ports.job_repository import ExportArtifact, Job, JobKind, JobStatus


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlClientRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    def _to_row(self, c: Client) -> ClientRow:
        return ClientRow(
            id=c.id,
            tenant_id=self._tenant,
            name=c.name,
            sex=c.sex.value,
            birthdate=c.birthdate,
            age_years=c.age_years,
            height_cm=c.height_cm,
            weight_kg=c.weight_kg,
            goal=c.goal.value,
            activity_level=c.activity_level.value,
            restrictions=list(c.restrictions),
            notes=c.notes,
            preferences=[
                ClientFoodPreferenceRow(tenant_id=self._tenant, client_id=c.id, food_id=fid)
                for fid in c.liked_food_ids
            ],
        )

    @staticmethod
    def _to_domain(row: ClientRow) -> Client:
        return Client(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            sex=Sex(row.sex),
            birthdate=row.birthdate,
            age_years=row.age_years,
            height_cm=row.height_cm,
            weight_kg=row.weight_kg,
            goal=Goal(row.goal),
            activity_level=ActivityLevel(row.activity_level),
            liked_food_ids=[p.food_id for p in row.preferences],
            restrictions=list(row.restrictions or []),
            notes=row.notes,
        )

    async def add(self, client: Client) -> None:
        if client.tenant_id != self._tenant:
            raise TenantIsolationError("El cliente no pertenece al tenant del contexto")
        self._s.add(self._to_row(client))
        await self._s.flush()

    async def get(self, client_id: UUID) -> Client | None:
        stmt = select(ClientRow).where(
            ClientRow.id == client_id, ClientRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list(self) -> list[Client]:
        stmt = (
            select(ClientRow).where(ClientRow.tenant_id == self._tenant).order_by(ClientRow.name)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update(self, client: Client) -> None:
        stmt = select(ClientRow).where(
            ClientRow.id == client.id, ClientRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TenantIsolationError("Cliente inexistente para este tenant")
        row.name = client.name
        row.sex = client.sex.value
        row.birthdate = client.birthdate
        row.age_years = client.age_years
        row.height_cm = client.height_cm
        row.weight_kg = client.weight_kg
        row.goal = client.goal.value
        row.activity_level = client.activity_level.value
        row.restrictions = list(client.restrictions)
        row.notes = client.notes
        # borrar preferencias viejas antes de insertar (unique client_id+food_id)
        row.preferences.clear()
        await self._s.flush()
        row.preferences = [
            ClientFoodPreferenceRow(tenant_id=self._tenant, client_id=client.id, food_id=fid)
            for fid in client.liked_food_ids
        ]
        await self._s.flush()


class SqlFoodRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: FoodRow) -> FoodItem:
        return FoodItem(
            id=row.id,
            tenant_id=row.tenant_id,
            source=row.source,
            source_ref=row.source_ref,
            name_es=row.name_es,
            name_en=row.name_en,
            category=FoodCategory(row.category),
            kcal_100g=row.kcal_100g,
            protein_100g=row.protein_100g,
            carb_100g=row.carb_100g,
            fat_100g=row.fat_100g,
            tags=list(row.tags or []),
            default_unit_g=row.default_unit_g,
            unit_granularity=UnitGranularity(row.unit_granularity or "grams"),
            unit_name=row.unit_name,
        )

    @staticmethod
    def _fill_row(row: FoodRow, food: FoodItem) -> FoodRow:
        row.source = food.source
        row.source_ref = food.source_ref
        row.name_es = food.name_es
        row.name_norm = normalize(food.name_es)
        row.name_en = food.name_en
        row.category = food.category.value
        row.kcal_100g = food.kcal_100g
        row.protein_100g = food.protein_100g
        row.carb_100g = food.carb_100g
        row.fat_100g = food.fat_100g
        row.tags = list(food.tags)
        row.default_unit_g = food.default_unit_g
        row.unit_granularity = food.unit_granularity.value
        row.unit_name = food.unit_name
        return row

    async def upsert_globals(self, foods: list[FoodItem]) -> None:
        for food in foods:
            existing = await self._s.get(FoodRow, food.id)
            if existing is None:
                existing = FoodRow(id=food.id, tenant_id=None)
                self._s.add(existing)
            self._fill_row(existing, food)
        await self._s.flush()

    async def add_custom(self, food: FoodItem) -> None:
        row = self._fill_row(FoodRow(id=food.id, tenant_id=self._tenant), food)
        self._s.add(row)
        await self._s.flush()

    def _universe_filter(self) -> ColumnElement[bool]:
        return or_(FoodRow.tenant_id.is_(None), FoodRow.tenant_id == self._tenant)

    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]:
        if not food_ids:
            return []
        stmt = select(FoodRow).where(FoodRow.id.in_(food_ids), self._universe_filter())
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_universe(self) -> list[FoodItem]:
        stmt = select(FoodRow).where(self._universe_filter()).order_by(FoodRow.name_es)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def search(self, query: str, category: FoodCategory | None = None) -> list[FoodItem]:
        stmt = select(FoodRow).where(
            self._universe_filter(), FoodRow.name_norm.like(f"%{normalize(query)}%")
        )
        if category is not None:
            stmt = stmt.where(FoodRow.category == category.value)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]


class SqlIntakeRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: IntakeDocumentRow) -> IntakeDocument:
        return IntakeDocument(
            id=row.id,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            source_filename=row.source_filename,
            raw_text=row.raw_text,
            parsed=dict(row.parsed or {}),
            ambiguities=list(row.ambiguities or []),
            status=IntakeStatus(row.status),
            created_at=_aware(row.created_at),
        )

    async def add(self, intake: IntakeDocument) -> None:
        self._s.add(
            IntakeDocumentRow(
                id=intake.id,
                tenant_id=self._tenant,
                client_id=intake.client_id,
                source_filename=intake.source_filename,
                raw_text=intake.raw_text,
                parsed=intake.parsed,
                ambiguities=intake.ambiguities,
                status=intake.status.value,
                created_at=intake.created_at,
            )
        )
        await self._s.flush()

    async def get(self, intake_id: UUID) -> IntakeDocument | None:
        stmt = select(IntakeDocumentRow).where(
            IntakeDocumentRow.id == intake_id, IntakeDocumentRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update_status(
        self, intake_id: UUID, status: IntakeStatus, *, parsed: dict[str, Any] | None = None
    ) -> None:
        stmt = select(IntakeDocumentRow).where(
            IntakeDocumentRow.id == intake_id, IntakeDocumentRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return
        row.status = status.value
        if parsed is not None:
            row.parsed = parsed
        await self._s.flush()

    async def list(self) -> list[IntakeDocument]:
        stmt = (
            select(IntakeDocumentRow)
            .where(IntakeDocumentRow.tenant_id == self._tenant)
            .order_by(IntakeDocumentRow.created_at.desc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]


class SqlTargetsRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: NutritionTargetsRow) -> NutritionTargets:
        return NutritionTargets(
            id=row.id,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            daily=MacroTargets(**row.daily),
            per_meal={MealSlot(slot): MacroTargets(**m) for slot, m in row.per_meal.items()},
            method=row.method,
            config_version=row.config_version,
            overrides=dict(row.overrides or {}),
            formula=MacroFormula(**(row.formula or {})),
            computed_at=_aware(row.computed_at),
        )

    async def add(self, targets: NutritionTargets) -> None:
        self._s.add(
            NutritionTargetsRow(
                id=targets.id,
                tenant_id=self._tenant,
                client_id=targets.client_id,
                daily=targets.daily.model_dump(),
                per_meal={s.value: m.model_dump() for s, m in targets.per_meal.items()},
                method=targets.method,
                config_version=targets.config_version,
                overrides=targets.overrides,
                formula=targets.formula.model_dump(exclude_none=True),
                computed_at=targets.computed_at,
            )
        )
        await self._s.flush()

    async def get(self, targets_id: UUID) -> NutritionTargets | None:
        stmt = select(NutritionTargetsRow).where(
            NutritionTargetsRow.id == targets_id, NutritionTargetsRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def latest_for_client(self, client_id: UUID) -> NutritionTargets | None:
        stmt = (
            select(NutritionTargetsRow)
            .where(
                NutritionTargetsRow.client_id == client_id,
                NutritionTargetsRow.tenant_id == self._tenant,
            )
            .order_by(NutritionTargetsRow.computed_at.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None


class SqlPlanRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    def _day_rows(self, plan: PlanCycle) -> list[DayPlanRow]:
        return [
            DayPlanRow(
                tenant_id=self._tenant,
                day_index=day.day_index,
                totals=day.totals.model_dump(),
                meals=[
                    MealEntryRow(
                        tenant_id=self._tenant,
                        position=i,
                        slot=meal.slot.value,
                        portions=[
                            {"food_id": str(p.food_id), "grams": p.grams} for p in meal.portions
                        ],
                        computed=meal.computed.model_dump(),
                        free_salad=meal.free_salad,
                        free_protein=meal.free_protein,
                    )
                    for i, meal in enumerate(day.meals)
                ],
            )
            for day in plan.days
        ]

    @staticmethod
    def _to_domain(row: PlanCycleRow) -> PlanCycle:
        return PlanCycle(
            id=row.id,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            targets_id=row.targets_id,
            phase=PlanPhase(row.phase),
            days=[
                DayPlan(
                    day_index=d.day_index,
                    totals=MacroTargets(**d.totals),
                    meals=[
                        MealEntry(
                            slot=MealSlot(m.slot),
                            portions=[
                                MealFoodPortion(food_id=UUID(p["food_id"]), grams=p["grams"])
                                for p in m.portions
                            ],
                            computed=MacroTargets(**m.computed),
                            free_salad=m.free_salad,
                            free_protein=m.free_protein,
                        )
                        for m in d.meals
                    ],
                )
                for d in row.days
            ],
            status=PlanStatus(row.status),
            config_version=row.config_version,
            prompt_version=row.prompt_version,
            model=row.model,
            input_hash=row.input_hash,
            created_by=row.created_by,
            created_at=_aware(row.created_at),
            approved_at=_aware(row.approved_at) if row.approved_at else None,
        )

    async def add(self, plan: PlanCycle) -> None:
        self._s.add(
            PlanCycleRow(
                id=plan.id,
                tenant_id=self._tenant,
                client_id=plan.client_id,
                targets_id=plan.targets_id,
                phase=plan.phase.value,
                status=plan.status.value,
                config_version=plan.config_version,
                prompt_version=plan.prompt_version,
                model=plan.model,
                input_hash=plan.input_hash,
                created_by=plan.created_by,
                created_at=plan.created_at,
                approved_at=plan.approved_at,
                days=self._day_rows(plan),
            )
        )
        await self._s.flush()

    async def _row(self, plan_id: UUID) -> PlanCycleRow | None:
        stmt = select(PlanCycleRow).where(
            PlanCycleRow.id == plan_id, PlanCycleRow.tenant_id == self._tenant
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get(self, plan_id: UUID) -> PlanCycle | None:
        row = await self._row(plan_id)
        return self._to_domain(row) if row else None

    async def list_for_client(self, client_id: UUID) -> list[PlanCycle]:
        stmt = (
            select(PlanCycleRow)
            .where(PlanCycleRow.client_id == client_id, PlanCycleRow.tenant_id == self._tenant)
            .order_by(PlanCycleRow.created_at.desc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def find_by_input_hash(self, input_hash: str) -> list[PlanCycle]:
        stmt = select(PlanCycleRow).where(
            PlanCycleRow.input_hash == input_hash, PlanCycleRow.tenant_id == self._tenant
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update_days(self, plan: PlanCycle) -> None:
        row = await self._row(plan.id)
        if row is None:
            raise TenantIsolationError("Plan inexistente para este tenant")
        # borrar los días viejos antes de insertar (unique plan_cycle_id+day_index)
        row.days.clear()
        await self._s.flush()
        row.days = self._day_rows(plan)
        await self._s.flush()

    async def set_status(self, plan_id: UUID, status: PlanStatus) -> None:
        row = await self._row(plan_id)
        if row is None:
            raise TenantIsolationError("Plan inexistente para este tenant")
        row.status = status.value
        row.approved_at = datetime.now(UTC) if status == PlanStatus.APPROVED else None
        await self._s.flush()


class SqlJobRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: GenerationJobRow) -> "Job":
        return Job(
            id=row.id,
            tenant_id=row.tenant_id,
            kind=JobKind(row.kind),
            status=JobStatus(row.status),
            idempotency_key=row.idempotency_key,
            input_hash=row.input_hash,
            result_id=row.result_id,
            error=row.error,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )

    async def add(self, job: "Job") -> None:
        self._s.add(
            GenerationJobRow(
                id=job.id,
                tenant_id=self._tenant,
                kind=job.kind.value,
                status=job.status.value,
                idempotency_key=job.idempotency_key,
                input_hash=job.input_hash,
                result_id=job.result_id,
                error=job.error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )
        await self._s.flush()

    async def get(self, job_id: UUID) -> "Job | None":
        stmt = select(GenerationJobRow).where(
            GenerationJobRow.id == job_id, GenerationJobRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_idempotency_key(self, key: str) -> "Job | None":
        stmt = select(GenerationJobRow).where(
            GenerationJobRow.idempotency_key == key, GenerationJobRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update(self, job: "Job") -> None:
        stmt = select(GenerationJobRow).where(
            GenerationJobRow.id == job.id, GenerationJobRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TenantIsolationError("Job inexistente para este tenant")
        row.status = job.status.value
        row.input_hash = job.input_hash
        row.result_id = job.result_id
        row.error = job.error
        row.updated_at = datetime.now(UTC)
        await self._s.flush()


class SqlArtifactRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    async def add(self, artifact: "ExportArtifact") -> None:
        self._s.add(
            ExportArtifactRow(
                id=artifact.id,
                tenant_id=self._tenant,
                plan_cycle_id=artifact.plan_cycle_id,
                format=artifact.format,
                path=artifact.path,
                created_at=artifact.created_at,
            )
        )
        await self._s.flush()

    async def list_for_plan(self, plan_cycle_id: UUID) -> "list[ExportArtifact]":
        stmt = select(ExportArtifactRow).where(
            ExportArtifactRow.plan_cycle_id == plan_cycle_id,
            ExportArtifactRow.tenant_id == self._tenant,
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [
            ExportArtifact(
                id=r.id,
                tenant_id=r.tenant_id,
                plan_cycle_id=r.plan_cycle_id,
                format=r.format,
                path=r.path,
                created_at=_aware(r.created_at),
            )
            for r in rows
        ]


class SqlAuthRepository:
    """Cuentas de entrenador/cliente. NO filtra por tenant: el login es previo
    a la sesión y resuelve a qué tenant pertenece el usuario."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _to_domain(row: UserRow) -> Trainer:
        return Trainer(
            id=row.id, tenant_id=row.tenant_id, name=row.name,
            email=row.email or "", role=row.role, client_id=row.client_id,
        )

    async def get_by_email(self, email: str) -> tuple[Trainer, str] | None:
        """Devuelve (cuenta, password_hash) o None. El hash no sale del adaptador."""
        stmt = select(UserRow).where(UserRow.email == email.strip().lower())
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None or row.password_hash is None:
            return None
        return self._to_domain(row), row.password_hash

    async def create_account(
        self, *, tenant_id: UUID, tenant_name: str, name: str,
        email: str, password_hash: str, role: str = "trainer",
    ) -> Trainer:
        self._s.add(TenantRow(id=tenant_id, name=tenant_name, created_at=datetime.now(UTC)))
        user = UserRow(
            id=uuid4(), tenant_id=tenant_id, name=name,
            email=email.strip().lower(), password_hash=password_hash, role=role,
        )
        self._s.add(user)
        await self._s.flush()
        return self._to_domain(user)

    async def create_client_login(
        self, *, tenant_id: UUID, client_id: UUID, name: str,
        email: str, password_hash: str,
    ) -> Trainer:
        """Cuenta de cliente en el tenant del entrenador (no crea tenant)."""
        user = UserRow(
            id=uuid4(), tenant_id=tenant_id, name=name, email=email.strip().lower(),
            password_hash=password_hash, role="client", client_id=client_id,
        )
        self._s.add(user)
        await self._s.flush()
        return self._to_domain(user)


class SqlRecipeRepository:
    """Recetas del tenant. Con `tenant_id=None` (modo admin) no filtra: el
    admin ve y verifica las recetas pendientes de todos los entrenadores."""

    def __init__(self, session: AsyncSession, tenant_id: UUID | None) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: RecipeRow) -> Recipe:
        return Recipe(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            ingredients=[
                RecipeIngredient(food_id=UUID(i["food_id"]), grams=i["grams"])
                for i in row.ingredients
            ],
            macros=MacroTargets(**row.macros),
            total_grams=row.total_grams,
            status=RecipeStatus(row.status),
            created_by=row.created_by,
            created_at=_aware(row.created_at),
            compound_food_id=row.compound_food_id,
        )

    async def add(self, recipe: Recipe) -> None:
        self._s.add(
            RecipeRow(
                id=recipe.id,
                tenant_id=recipe.tenant_id,
                name=recipe.name,
                ingredients=[
                    {"food_id": str(i.food_id), "grams": i.grams} for i in recipe.ingredients
                ],
                macros=recipe.macros.model_dump(),
                total_grams=recipe.total_grams,
                status=recipe.status.value,
                created_by=recipe.created_by,
                created_at=recipe.created_at,
                compound_food_id=recipe.compound_food_id,
            )
        )
        await self._s.flush()

    async def _row(self, recipe_id: UUID) -> RecipeRow | None:
        stmt = select(RecipeRow).where(RecipeRow.id == recipe_id)
        if self._tenant is not None:
            stmt = stmt.where(RecipeRow.tenant_id == self._tenant)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get(self, recipe_id: UUID) -> Recipe | None:
        row = await self._row(recipe_id)
        return self._to_domain(row) if row else None

    async def list_for_tenant(self) -> list[Recipe]:
        stmt = select(RecipeRow).order_by(RecipeRow.created_at.desc())
        if self._tenant is not None:
            stmt = stmt.where(RecipeRow.tenant_id == self._tenant)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_pending(self) -> list[Recipe]:
        """Cola de verificación (admin): pendientes de todos los tenants."""
        stmt = (
            select(RecipeRow)
            .where(RecipeRow.status == RecipeStatus.PENDING.value)
            .order_by(RecipeRow.created_at)
        )
        if self._tenant is not None:
            stmt = stmt.where(RecipeRow.tenant_id == self._tenant)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def set_status(
        self, recipe_id: UUID, status: RecipeStatus, *, compound_food_id: UUID | None = None
    ) -> None:
        row = await self._row(recipe_id)
        if row is None:
            raise TenantIsolationError("Receta inexistente para este contexto")
        row.status = status.value
        if compound_food_id is not None:
            row.compound_food_id = compound_food_id
        await self._s.flush()


class SqlAuditLogRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._s.add(
            AuditLogRow(
                tenant_id=self._tenant,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details or {},
                at=datetime.now(UTC),
            )
        )
        await self._s.flush()
