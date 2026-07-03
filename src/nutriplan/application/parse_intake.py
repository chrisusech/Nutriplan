"""Caso de uso ParseIntake (Módulo 1): Word → IntakeDocument validado.

Flujo (sección 8.2): leer .docx → extraer con Structured Outputs → validar →
detectar ambigüedades → mapear alimentos contra la base → persistir.
La IA normaliza y estructura; el humano resuelve lo dudoso.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from nutriplan.adapters.intake.docx_reader import read_docx_text
from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.domain.food_matching import match_food_names
from nutriplan.domain.intake_review import all_liked_food_names, detect_ambiguities
from nutriplan.domain.models import IntakeDocument, IntakeParsed, IntakeStatus
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.repository import IntakeRepository

logger = structlog.get_logger(__name__)


async def parse_intake(
    *,
    file_bytes: bytes,
    filename: str,
    tenant_id: UUID,
    llm: LLMClient,
    intake_repo: IntakeRepository,
    food_repo: FoodRepository,
    prompts_dir: Path,
    model: str,
) -> IntakeDocument:
    raw_text = read_docx_text(file_bytes)
    prompt = load_prompt(prompts_dir, "intake_extraction")

    parsed: IntakeParsed = await llm.extract(
        system=prompt.text, text=raw_text, schema=IntakeParsed, model=model
    )

    ambiguities = detect_ambiguities(parsed)

    # Mapeo determinista texto → FoodItem; lo no reconocido va a revisión.
    universe = await food_repo.list_universe()
    match = match_food_names(all_liked_food_names(parsed), universe)
    for name in match.unrecognized:
        ambiguities.append(f"alimento no reconocido: {name}")

    parsed_payload = parsed.model_dump(mode="json")
    parsed_payload["food_matches"] = {raw: str(f.id) for raw, f in match.matched.items()}
    parsed_payload["unmatched_foods"] = match.unrecognized
    parsed_payload["prompt_version"] = prompt.version

    status = IntakeStatus.NEEDS_REVIEW if ambiguities else IntakeStatus.PARSED
    document = IntakeDocument(
        id=uuid4(),
        tenant_id=tenant_id,
        client_id=None,
        source_filename=filename,
        raw_text=raw_text,
        parsed=parsed_payload,
        ambiguities=ambiguities,
        status=status,
        created_at=datetime.now(UTC),
    )
    await intake_repo.add(document)
    logger.info(
        "intake_parsed",
        intake_id=str(document.id),
        status=status.value,
        ambiguities=len(ambiguities),
        unmatched_foods=len(match.unrecognized),
    )
    return document
