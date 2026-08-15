"""Check-in semanal de peso + adaptación de kcal.

Una pesada por semana ISO (lunes). Al guardar: actualiza el perfil, decide el
ajuste de kcal con reglas del dominio, persiste targets nuevos y (opcional)
pide a la IA una nota corta que narra el resultado — sin inventar números.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.prompts import load_prompt
from nutriplan.domain.adapt_targets import AdaptationDecision, decision_from_targets
from nutriplan.domain.errors import LLMError, ValidationError
from nutriplan.domain.models import Client, NutritionTargets, WeightEntry
from nutriplan.domain.week import iso_week_start
from nutriplan.domain.week_close import WeekClosure, is_valid_comment
from nutriplan.ports.config_provider import ConfigProvider
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.repository import ClientRepository, TargetsRepository

logger = structlog.get_logger(__name__)

CHECKIN_NOTE_PROMPT_VERSION = 1
WEIGHT_MIN_KG = 30.0
WEIGHT_MAX_KG = 300.0


class WeightRepository(Protocol):
    async def upsert(self, entry: WeightEntry) -> WeightEntry: ...
    async def for_week(self, client_id: UUID, week_start: date) -> WeightEntry | None: ...
    async def latest(self, client_id: UUID) -> WeightEntry | None: ...
    async def previous_before(self, client_id: UUID, week_start: date) -> WeightEntry | None: ...


class RatingCounter(Protocol):
    async def count_for_plan(self, plan_cycle_id: UUID) -> int: ...


class CheckinNote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=1, max_length=280)


@dataclass(frozen=True)
class CheckinResult:
    entry: WeightEntry
    client: Client
    targets: NutritionTargets | None
    decision: AdaptationDecision | None
    note: str | None


async def needs_weekly_checkin(
    *, client: Client, weights: WeightRepository, today: date | None = None
) -> bool:
    """True si falta el pesaje de la semana en curso."""
    return await weights.for_week(client.id, iso_week_start(today)) is None


async def week_closure(
    *,
    client: Client,
    weights: WeightRepository,
    ratings: RatingCounter,
    meals_in_plan: int = 0,
    today: date | None = None,
) -> WeekClosure:
    """El estado del cierre: peso y comentario de la semana.

    Las calificaciones se cuentan para enseñarlas, no para bloquear. `meals_in_plan`
    se acepta por compatibilidad con quien ya llamaba así.
    """
    _ = meals_in_plan
    entry = await weights.for_week(client.id, iso_week_start(today))
    if client.active_plan_id is None:
        return WeekClosure(
            has_weight=entry is not None,
            ratings=0,
            ratings_required=0,
            has_comment=False,
            is_first_week=True,
        )
    return WeekClosure(
        has_weight=entry is not None,
        ratings=await ratings.count_for_plan(client.active_plan_id),
        ratings_required=0,
        has_comment=is_valid_comment(entry.client_comment if entry else None),
    )


async def seed_weight_from_profile(
    *,
    client: Client,
    weights: WeightRepository,
    today: date | None = None,
) -> WeightEntry:
    """Al crear el perfil, el peso de alta cuenta como check-in de esa semana."""
    entry = WeightEntry(
        id=uuid4(),
        tenant_id=client.tenant_id,
        client_id=client.id,
        weight_kg=client.weight_kg,
        week_start=iso_week_start(today),
        logged_at=datetime.now(UTC),
        note=None,
    )
    return await weights.upsert(entry)


async def submit_weekly_checkin(
    *,
    client: Client,
    weight_kg: float,
    client_comment: str | None = None,
    client_repo: ClientRepository,
    weights: WeightRepository,
    targets_repo: TargetsRepository,
    config_provider: ConfigProvider,
    llm: LLMClient | None = None,
    prompts_dir: Path | None = None,
    model: str = "",
    today: date | None = None,
) -> CheckinResult:
    """Guarda el peso, adapta kcal si hay historial, y escribe nota opcional."""
    if not WEIGHT_MIN_KG <= weight_kg <= WEIGHT_MAX_KG:
        raise ValidationError(
            f"El peso debe estar entre {WEIGHT_MIN_KG:.0f} y {WEIGHT_MAX_KG:.0f} kg."
        )

    week = iso_week_start(today)
    previous_entry = await weights.previous_before(client.id, week)
    # Si re-envía la misma semana, el “anterior” sigue siendo el de antes.
    this_week = await weights.for_week(client.id, week)

    client = client.model_copy(update={"weight_kg": weight_kg})
    await client_repo.update(client)

    entry = WeightEntry(
        id=this_week.id if this_week else uuid4(),
        tenant_id=client.tenant_id,
        client_id=client.id,
        weight_kg=weight_kg,
        week_start=week,
        logged_at=datetime.now(UTC),
        note=None,
        client_comment=(client_comment or "").strip()[:1200] or None,
    )
    entry = await weights.upsert(entry)

    previous_weight = previous_entry.weight_kg if previous_entry is not None else None
    previous_targets = await targets_repo.latest_for_client(client.id)
    # Si re-envía el check-in de la MISMA semana, la base es el target del
    # pesaje anterior — no el ya adaptado hoy, o el Δ se apilaría.
    if previous_weight is not None:
        baseline = await targets_repo.latest_at_weight(client.id, previous_weight)
        if baseline is not None:
            previous_targets = baseline

    decision: AdaptationDecision | None = None
    targets: NutritionTargets | None = None
    note: str | None = None

    config = config_provider.get_nutrition_config().for_slots(client.meal_slots)

    if previous_weight is not None and previous_targets is not None:
        decision = decision_from_targets(
            client=client,
            config=config,
            previous_weight_kg=previous_weight,
            current_weight_kg=weight_kg,
            previous=previous_targets,
        )
        targets = await compute_and_store_targets(
            client=client,
            config_provider=config_provider,
            targets_repo=targets_repo,
            formula=decision.formula,
            overrides=previous_targets.overrides or None,
        )
        note = await _optional_note(
            decision=decision,
            llm=llm,
            prompts_dir=prompts_dir,
            model=model,
            goal=client.goal.value,
        )
        if note:
            entry = await weights.upsert(entry.model_copy(update={"note": note}))
    else:
        targets = await compute_and_store_targets(
            client=client,
            config_provider=config_provider,
            targets_repo=targets_repo,
            formula=previous_targets.formula if previous_targets else None,
            overrides=(previous_targets.overrides or None) if previous_targets else None,
        )

    logger.info(
        "weekly_checkin",
        client_id=str(client.id),
        weight_kg=weight_kg,
        action=decision.action.value if decision else "first",
        delta_kcal=decision.delta_kcal if decision else 0,
    )
    return CheckinResult(entry=entry, client=client, targets=targets, decision=decision, note=note)


async def _optional_note(
    *,
    decision: AdaptationDecision,
    llm: LLMClient | None,
    prompts_dir: Path | None,
    model: str,
    goal: str,
) -> str | None:
    if llm is None or prompts_dir is None or not model:
        return None
    try:
        system = load_prompt(prompts_dir, "checkin_note", CHECKIN_NOTE_PROMPT_VERSION).text
        text = (
            f"Objetivo: {goal}\n"
            f"Cambio de peso: {decision.weight_delta_kg:+.2f} kg\n"
            f"Acción: {decision.action.value} ({decision.reason_code})\n"
            f"Kcal antes: {decision.previous_kcal:.0f}\n"
            f"Kcal ahora: {decision.new_kcal:.0f}\n"
            f"Delta kcal: {decision.delta_kcal:+.0f}\n"
            "Escribe la nota para la persona."
        )
        generated = await llm.extract(system=system, text=text, schema=CheckinNote, model=model)
        return generated.note.strip()[:280] or None
    except LLMError as exc:
        logger.warning("checkin_note_skipped", error=str(exc))
        return None
