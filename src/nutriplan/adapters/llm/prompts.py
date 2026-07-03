"""Carga de prompts versionados (/prompts/*.vN.md).

La versión usada se guarda en cada plan (prompt_version). Cambiar un prompt
= nueva versión de archivo, nunca edición silenciosa (sección 13.3).
"""

from pathlib import Path
from typing import NamedTuple


class Prompt(NamedTuple):
    text: str
    version: str  # p. ej. "intake_extraction.v1"


def load_prompt(prompts_dir: Path, name: str, version: int = 1) -> Prompt:
    version_id = f"{name}.v{version}"
    path = prompts_dir / f"{version_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {path}")
    return Prompt(text=path.read_text(encoding="utf-8"), version=version_id)
