"""Branding por tenant en filesystem (Nivel 1): config/branding/<tenant>/branding.yaml.

El color de marca del entrenador se aplica a toda la UI y a los PDF
exportados; el selector de la cabecera lo persiste aquí.
"""

from pathlib import Path

import yaml

from nutriplan.domain.models import Branding

DEFAULT_BRANDING = Branding(
    tenant_name="NutriPlan",  # neutral: el entrenador pone su marca (no un nombre falso)
    primary_color="#F26D5B",  # coral del diseño
    accent_color="#F0925E",
)


def _path(branding_dir: Path, tenant: str) -> Path:
    return branding_dir / tenant / "branding.yaml"


def load_branding(branding_dir: Path, tenant: str = "default") -> Branding:
    path = _path(branding_dir, tenant)
    if not path.exists():
        return DEFAULT_BRANDING.model_copy()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Branding.model_validate({**DEFAULT_BRANDING.model_dump(), **data})


def save_branding(branding_dir: Path, branding: Branding, tenant: str = "default") -> None:
    path = _path(branding_dir, tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(branding.model_dump(), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class FileBrandingStore:
    """`BrandingStore` sobre el directorio de config."""

    def __init__(self, branding_dir: Path) -> None:
        self._dir = branding_dir

    def save(self, branding: Branding, *, tenant: str) -> None:
        save_branding(self._dir, branding, tenant)
