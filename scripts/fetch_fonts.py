"""Descarga las fuentes que la app sirve desde `static/fonts/`.

La app no puede depender del CDN de Google: una CSP estricta lo bloquea, en
Capacitor no hay red garantizada, y cada carga filtra la IP del usuario a un
tercero. Aquí se bajan una vez y se versionan en el repo.

Material Symbols se baja SUBSETEADA a los iconos que las plantillas usan de
verdad (la fuente completa pesa 3 MB). Si agregas o quitas iconos, vuelve a
correr este script:

    uv run python scripts/fetch_fonts.py
"""

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src" / "nutriplan" / "ui" / "web"
FONTS_DIR = WEB / "static" / "fonts"

# Google sirve un CSS distinto según el User-Agent; con uno moderno devuelve woff2.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
POPPINS_WEIGHTS = "400;500;600;700;800"
LATIN_RANGE = "U+0000-00FF"  # cubre áéíóúñü; no hace falta latin-ext


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        return r.read().decode("utf-8")


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        dest.write_bytes(r.read())
    print(f"  {dest.name}  {dest.stat().st_size // 1024} KB")


def icon_names() -> list[str]:
    """Los iconos que aparecen en plantillas y presenter, por nombre de ligadura."""
    found: set[str] = set()
    for path in [*WEB.joinpath("templates").rglob("*.html"), WEB / "presenter.py"]:
        text = path.read_text(encoding="utf-8")
        found |= set(re.findall(r'class="icon[^"]*"[^>]*>\s*([a-z_]+)\s*<', text))
        found |= set(re.findall(r'"icon":\s*"([a-z_]+)"', text))
    return sorted(found)


def fetch_poppins() -> None:
    print("Poppins:")
    css = _get(
        "https://fonts.googleapis.com/css2"
        f"?family=Poppins:wght@{POPPINS_WEIGHTS}&display=swap"
    )
    for block in re.findall(r"@font-face\s*\{(.*?)\}", css, re.S):
        if LATIN_RANGE not in (re.search(r"unicode-range:\s*([^;]+)", block) or [""])[0]:
            continue
        weight = re.search(r"font-weight:\s*(\d+)", block)
        url = re.search(r"url\((https://[^)]+)\)", block)
        if weight and url:
            _download(url.group(1), FONTS_DIR / f"poppins-{weight.group(1)}.woff2")


def fetch_material_symbols() -> None:
    names = icon_names()
    print(f"Material Symbols ({len(names)} iconos):")
    css = _get(
        "https://fonts.googleapis.com/css2"
        "?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@20..48,300..700,0..1,0"
        f"&icon_names={','.join(names)}"
    )
    url = re.search(r"url\((https://[^)]+)\)", css)
    if url is None:
        sys.exit("No se pudo leer la URL de la fuente en el CSS de Google")
    _download(url.group(1), FONTS_DIR / "material-symbols-rounded.woff2")


if __name__ == "__main__":
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    fetch_poppins()
    fetch_material_symbols()
