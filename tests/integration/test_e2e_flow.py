"""Paso 10 end-to-end con mock LLM: Word → intake → targets → plan → PDF.

El flujo completo del negocio sin tocar la red: extracción con respuestas
grabadas, generación con el selector heurístico, jobs persistidos con su
ciclo de estados, aprobación humana y export renderizado de verdad.
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.integration.test_parse_intake import ANA

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.adapters.render.docx_renderer import DocxRenderer
from nutriplan.adapters.render.pdf_weasyprint import WeasyPrintRenderer
from nutriplan.application.approve_plan import approve_plan
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.export_plan import export_plan
from nutriplan.application.generate_plan import generate_plan_for_client
from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.application.parse_intake import parse_intake
from nutriplan.container import Container
from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import (
    ActivityLevel,
    Branding,
    Client,
    Goal,
    PlanStatus,
    Sex,
)
from nutriplan.ports.job_repository import JobKind, JobStatus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "intakes"
PROMPTS = ROOT / "prompts"
CSV_PATH = ROOT / "data" / "foods" / "curated_foods.csv"


@pytest.fixture
async def ctx(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    await upgrade_to_head_async(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    container = Container(tenant_id=DEFAULT_TENANT_ID)
    async with factory() as session:
        await seed_local(session, CSV_PATH)
        yield container, container.repos(session), tmp_path
        await session.commit()
    await engine.dispose()


def client_from_intake(parsed: dict, food_matches: dict[str, str]) -> Client:
    """Lo que hará la UI al confirmar el intake: Client con alimentos mapeados."""
    return Client(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=parsed["name"],
        sex=Sex(parsed["sex"]),
        age_years=parsed["age_years"],
        height_cm=parsed["height_cm"],
        weight_kg=parsed["weight_kg"],
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
        liked_food_ids=[UUID(v) for v in food_matches.values()],
        restrictions=["no_seafood"],
    )


async def test_word_to_pdf_full_flow(ctx) -> None:
    container, repos, tmp_path = ctx

    # 1) Ingesta Word → IntakeDocument limpio
    mock = MockLLMClient()
    mock.enqueue(ANA)
    doc = await parse_intake(
        file_bytes=(FIXTURES / "intake_limpio.docx").read_bytes(),
        filename="intake_limpio.docx",
        tenant_id=DEFAULT_TENANT_ID,
        llm=mock,
        intake_repo=repos.intakes,
        food_repo=repos.foods,
        prompts_dir=PROMPTS,
        model="claude-haiku-4-5",
    )
    assert doc.parsed["unmatched_foods"] == []

    # 2) Confirmación humana → Client persistido
    client = client_from_intake(doc.parsed, doc.parsed["food_matches"])
    await repos.clients.add(client)

    # 3) Macros calculados y persistidos
    targets = await compute_and_store_targets(
        client=client, config_provider=container.config_provider, targets_repo=repos.targets
    )
    assert targets.daily.kcal > 1000

    # 4) Generación como job persistido (modo offline → heurístico)
    job = new_job(
        tenant_id=DEFAULT_TENANT_ID, kind=JobKind.GENERATE, idempotency_key=f"gen:{client.id}"
    )
    await repos.jobs.add(job)
    config = container.config_provider.get_nutrition_config()
    job = await run_generation_job(
        job=job,
        job_repo=repos.jobs,
        client_id=client.id,
        client_repo=repos.clients,
        targets_repo=repos.targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        config=config,
        llm=None,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
    )
    assert job.status == JobStatus.DONE, job.error
    cycles = await repos.plans.list_for_client(client.id)
    assert len(cycles) == 1  # una sola semana, no 15+15
    first = cycles[0]
    assert len(first.days) == 7

    # 5) Reproducibilidad: regenerar devuelve el MISMO plan sin re-generar
    again = await generate_plan_for_client(
        client=client,
        targets=targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        client_repo=repos.clients,
        config=config,
        llm=None,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
    )
    assert again.id == first.id

    # 6) Sin aprobar no se exporta (compuerta humana)
    branding = Branding(tenant_name="Estudio Fit", primary_color="#F26D5B")
    with pytest.raises(RenderError):
        await export_plan(
            plan_id=first.id,
            fmt="pdf",
            plan_repo=repos.plans,
            food_repo=repos.foods,
            artifact_repo=repos.artifacts,
            renderer=WeasyPrintRenderer(),
            branding=branding,
            exports_dir=tmp_path / "exports",
        )

    # 7) Aprobar → exportar PDF y DOCX reales
    approved = await approve_plan(
        plan_id=first.id, plan_repo=repos.plans, audit_repo=repos.audit
    )
    assert approved.status == PlanStatus.APPROVED
    assert approved.approved_at is not None

    artifact, pdf = await export_plan(
        plan_id=first.id,
        fmt="pdf",
        plan_repo=repos.plans,
        food_repo=repos.foods,
        artifact_repo=repos.artifacts,
        renderer=WeasyPrintRenderer(),
        branding=branding,
        exports_dir=tmp_path / "exports",
    )
    assert pdf[:5] == b"%PDF-"
    assert Path(artifact.path).exists()

    _, docx = await export_plan(
        plan_id=first.id,
        fmt="docx",
        plan_repo=repos.plans,
        food_repo=repos.foods,
        artifact_repo=repos.artifacts,
        renderer=DocxRenderer(),
        branding=branding,
        exports_dir=tmp_path / "exports",
    )
    assert docx[:2] == b"PK"  # zip → docx
    assert len(await repos.artifacts.list_for_plan(first.id)) == 2


async def test_thirty_day_plan_has_two_phases(ctx) -> None:
    """Fase 5: 30 días = 2 semanas (first_15 + next_15)."""
    container, repos, _ = ctx
    from nutriplan.application.compute_targets import compute_and_store_targets

    foods = await repos.foods.list_universe()
    client = Client(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Plan 30",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165,
        weight_kg=65,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
        liked_food_ids=[f.id for f in foods],
        restrictions=[],
    )
    await repos.clients.add(client)
    targets = await compute_and_store_targets(
        client=client, config_provider=container.config_provider, targets_repo=repos.targets
    )
    cycle = await generate_plan_for_client(
        client=client,
        targets=targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        client_repo=repos.clients,
        config=container.config_provider.get_nutrition_config(),
        llm=None,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
        duration_days=30,
    )
    assert cycle.duration_days == 30
    assert len(cycle.days) == 14
    from nutriplan.domain.models import PlanPhase

    assert sum(1 for d in cycle.days if d.phase is PlanPhase.FIRST_15) == 7
    assert sum(1 for d in cycle.days if d.phase is PlanPhase.NEXT_15) == 7

    container, repos, _ = ctx
    job = new_job(tenant_id=DEFAULT_TENANT_ID, kind=JobKind.GENERATE, idempotency_key="gen:x")
    await repos.jobs.add(job)
    job = await run_generation_job(
        job=job,
        job_repo=repos.jobs,
        client_id=uuid4(),  # cliente inexistente
        client_repo=repos.clients,
        targets_repo=repos.targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        config=container.config_provider.get_nutrition_config(),
        llm=None,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
    )
    assert job.status == JobStatus.FAILED
    assert job.error and "no existe" in job.error
    stored = await repos.jobs.get(job.id)
    assert stored is not None and stored.status == JobStatus.FAILED
