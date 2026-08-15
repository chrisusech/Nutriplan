"""Resumen de lo que la gente escribió, para la consola.

Los números los cuenta SQL. Aquí solo se leen comentarios y se agrupan en
temas. Soft-fail: sin IA o si el modelo cae, la consola sigue con los números.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from nutriplan.application.prompts import load_prompt
from nutriplan.domain.errors import LLMError
from nutriplan.ports.llm_client import LLMClient

VOICES_PROMPT_VERSION = 1
CACHE_TTL = timedelta(hours=24)


class VoiceTheme(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=0, le=200)
    quotes: list[str] = Field(default_factory=list, max_length=3)


class VoicesReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    themes: list[VoiceTheme] = Field(default_factory=list, max_length=6)


class CachedVoices:
    """Un informe por proceso. Fly tiene una máquina."""

    def __init__(self) -> None:
        self.report: VoicesReport | None = None
        self.at: datetime | None = None

    def get(self) -> VoicesReport | None:
        if self.report is None or self.at is None:
            return None
        if datetime.now(UTC) - self.at > CACHE_TTL:
            return None
        return self.report

    def put(self, report: VoicesReport) -> None:
        self.report = report
        self.at = datetime.now(UTC)


def _lines(items: list[str], *, tope: int) -> str:
    clean = [t.strip() for t in items if t and t.strip()]
    if not clean:
        return "(nada)"
    return "\n".join(f"- {t}" for t in clean[:tope])


async def summarize_voices(
    *,
    closures: list[dict[str, Any]],
    opinions: list[dict[str, Any]],
    dish_notes: list[dict[str, Any]],
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
) -> VoicesReport | None:
    """Agrupa comentarios. None si no hay modelo o si falla."""
    if llm is None:
        return None
    cierre = _lines([str(c.get("comentario") or "") for c in closures], tope=40)
    opiniones = _lines(
        [
            f"{o.get('categoria', '')} NPS={o.get('nps', '')}: {o.get('mensaje', '')}"
            for o in opinions
        ],
        tope=40,
    )
    platos = _lines(
        [f"{n.get('plato') or n.get('plantilla')}: {n.get('comentario', '')}" for n in dish_notes],
        tope=30,
    )
    prompt = load_prompt(prompts_dir, "admin_voices", VOICES_PROMPT_VERSION)
    text = f"Cierres de semana:\n{cierre}\n\nOpiniones:\n{opiniones}\n\nPlatos:\n{platos}\n"
    try:
        return await llm.extract(system=prompt.text, text=text, schema=VoicesReport, model=model)
    except (LLMError, ValueError):
        return None
