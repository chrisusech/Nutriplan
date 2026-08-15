"""Carga de prompts versionados (/prompts/*.vN.md).

La versión usada se guarda en cada plan (`prompt_version`). Cambiar un prompt =
nueva versión de archivo, nunca edición silenciosa.

Vive en `application` y no en `adapters` porque es esta capa la que decide qué
versión corre (`PLAN_PROMPT_VERSION` y compañía); el directorio se lo pasa el
composition root, así que aquí no se decide dónde están los archivos, solo cuál
se lee.
"""

from pathlib import Path
from typing import NamedTuple


class Prompt(NamedTuple):
    text: str
    version: str  # p. ej. "plan_generation.v4"


def load_prompt(prompts_dir: Path, name: str, version: int = 1) -> Prompt:
    version_id = f"{name}.v{version}"
    path = prompts_dir / f"{version_id}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt no encontrado: {path}")
    return Prompt(text=path.read_text(encoding="utf-8"), version=version_id)
