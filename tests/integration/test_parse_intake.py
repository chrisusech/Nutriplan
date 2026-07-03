"""Módulo 1 end-to-end con mock LLM: Word fixture → IntakeDocument persistido."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nutriplan.adapters.db.repositories import SqlFoodRepository, SqlIntakeRepository
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local
from nutriplan.adapters.db.session import Base
from nutriplan.adapters.intake.docx_reader import read_docx_text
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.application.parse_intake import parse_intake
from nutriplan.domain.errors import IntakeAmbiguityError
from nutriplan.domain.models import IntakeStatus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "intakes"
PROMPTS = ROOT / "prompts"
CSV_PATH = ROOT / "data" / "foods" / "curated_foods.csv"

ANA = {
    "name": "Ana Pérez",
    "sex": "female",
    "age_years": 28,
    "height_cm": 165.0,
    "weight_kg": 62.0,
    "weight_is_approximate": False,
    "city": "Medellín",
    "goal_raw": "bajar grasa y tonificar",
    "liked_foods": {
        "proteins": ["pollo", "huevos", "atún"],
        "carbs": ["arroz", "arepa", "papa"],
        "fats": ["aguacate", "maní"],
        "fruits": ["banano", "fresa", "mango"],
        "vegetables": ["brócoli", "espinaca", "tomate"],
    },
    "restrictions_raw": ["sin mariscos"],
    "uses_protein_shake": False,
    "training_raw": "pesas 4 veces por semana",
}

CARLOS = {
    "name": "Carlos Ruiz",
    "sex": "male",
    "age_years": 35,
    "height_cm": 178.0,
    "weight_kg": 84.0,
    "weight_is_approximate": True,  # "aproximado, lo confirmo"
    "city": "Bogotá",
    "goal_raw": "ganar masa muscular",
    "liked_foods": {
        "proteins": ["carne de res", "pollo", "huevos"],
        "carbs": ["pasta", "arroz", "pan"],
        "fats": ["aceite de oliva", "almendras"],
        "fruits": ["manzana", "uvas"],
        "vegetables": ["lechuga", "zanahoria"],
    },
    "restrictions_raw": [],
    "uses_protein_shake": True,
    "training_raw": "gimnasio 5 días",
}

LAURA = {
    "name": "Laura Gómez",
    "sex": "female",
    "age_years": 42,
    "height_cm": None,  # falta en el documento — la IA NO lo inventa
    "weight_kg": 70.0,
    "weight_is_approximate": False,
    "city": None,
    "goal_raw": "mantenerme y comer mejor",
    "liked_foods": {
        "proteins": ["tilapia", "pechuga de pavo"],
        "carbs": ["quinoa", "batata"],
        "fats": ["nueces"],
        "fruits": ["papaya", "kiwi"],
        "vegetables": ["coliflor", "pepino"],
    },
    "restrictions_raw": ["sin gluten", "sin lácteos"],
    "uses_protein_shake": None,
    "training_raw": None,
}


@pytest.fixture
async def ctx(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed_local(session, CSV_PATH)
        yield {
            "intake_repo": SqlIntakeRepository(session, DEFAULT_TENANT_ID),
            "food_repo": SqlFoodRepository(session, DEFAULT_TENANT_ID),
            "session": session,
        }
        await session.commit()
    await engine.dispose()


async def run(ctx, fixture_name: str, payload: dict):
    mock = MockLLMClient()
    mock.enqueue(payload)
    doc = await parse_intake(
        file_bytes=(FIXTURES / fixture_name).read_bytes(),
        filename=fixture_name,
        tenant_id=DEFAULT_TENANT_ID,
        llm=mock,
        intake_repo=ctx["intake_repo"],
        food_repo=ctx["food_repo"],
        prompts_dir=PROMPTS,
        model="claude-haiku-4-5",
    )
    return doc, mock


def test_docx_reader_extracts_text() -> None:
    text = read_docx_text((FIXTURES / "intake_limpio.docx").read_bytes())
    assert "Ana Pérez" in text
    assert "sin mariscos" in text


def test_docx_reader_rejects_garbage() -> None:
    with pytest.raises(IntakeAmbiguityError):
        read_docx_text(b"esto no es un docx")


async def test_clean_intake_is_parsed(ctx) -> None:
    doc, mock = await run(ctx, "intake_limpio.docx", ANA)
    assert doc.status == IntakeStatus.PARSED
    assert doc.ambiguities == []
    # todos los alimentos matchearon (incluye fuzzy: pollo→pechuga de pollo)
    assert doc.parsed["unmatched_foods"] == []
    assert len(doc.parsed["food_matches"]) == 14
    assert doc.parsed["prompt_version"] == "intake_extraction.v1"
    # el texto crudo del Word llegó al LLM
    assert "Ana Pérez" in mock.calls[0]["user"]
    # quedó persistido
    stored = await ctx["intake_repo"].get(doc.id)
    assert stored is not None and stored.status == IntakeStatus.PARSED


async def test_approximate_weight_needs_review(ctx) -> None:
    doc, _ = await run(ctx, "intake_peso_aproximado.docx", CARLOS)
    assert doc.status == IntakeStatus.NEEDS_REVIEW
    assert any("peso aproximado" in a for a in doc.ambiguities)


async def test_missing_height_needs_review(ctx) -> None:
    doc, _ = await run(ctx, "intake_incompleto.docx", LAURA)
    assert doc.status == IntakeStatus.NEEDS_REVIEW
    assert any("estatura" in a for a in doc.ambiguities)
    # los alimentos sí se mapean aunque falten datos del cliente
    assert doc.parsed["unmatched_foods"] == []


async def test_unknown_food_flagged_for_review(ctx) -> None:
    payload = {
        **ANA,
        "liked_foods": {**ANA["liked_foods"], "fruits": ["banano", "chontaduro"]},
    }
    doc, _ = await run(ctx, "intake_limpio.docx", payload)
    assert doc.status == IntakeStatus.NEEDS_REVIEW
    assert "alimento no reconocido: chontaduro" in doc.ambiguities
    assert doc.parsed["unmatched_foods"] == ["chontaduro"]
