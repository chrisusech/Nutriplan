"""Descarga las fuentes que la app sirve desde `static/fonts/`.

La app no puede depender del CDN de Google: una CSP estricta lo bloquea, en
Capacitor no hay red garantizada, y cada carga filtra la IP del usuario a un
tercero. Aquí se bajan una vez y se versionan en el repo.

Material Symbols se baja SUBSETEADA a los iconos que las plantillas usan de
verdad (la fuente completa pesa 3 MB). Si agregas o quitas iconos, vuelve a
correr este script:

    uv run python scripts/fetch_fonts.py
"""

import hashlib
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


SPAN = re.compile(r'class="icon[^"]*"[^>]*>(.*?)</span>', re.S)
LITERAL = re.compile(r"'([a-z_]+)'")


def icon_names() -> list[str]:
    """Los iconos que aparecen en plantillas, presenter y JS, por nombre de ligadura."""
    found: set[str] = set()
    paths = [
        *WEB.joinpath("templates").rglob("*.html"),
        # El JS también pinta iconos (el spinner de la receta): si se olvida,
        # sale la palabra "autorenew" girando en la tarjeta.
        *WEB.joinpath("static", "js").glob("*.js"),
        WEB / "presenter.py",
        WEB / "week_view.py",  # SLOT_META: wb_sunny, dinner_dining, …
    ]
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for inner in SPAN.findall(text):
            raw = inner.strip()
            # Un icono elegido en la plantilla (`{{ 'check' if … else 'close' }}`)
            # cuenta igual que uno escrito a pelo.
            if "{" in raw:
                found |= set(LITERAL.findall(raw))
            elif re.fullmatch(r"[a-z_]+", raw):
                found.add(raw)
        found |= set(re.findall(r'["\']icon["\']:\s*["\']([a-z_]+)["\']', text))
    return sorted(found)


def fetch_poppins() -> None:
    print("Poppins:")
    css = _get(
        f"https://fonts.googleapis.com/css2?family=Poppins:wght@{POPPINS_WEIGHTS}&display=swap"
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
    destino = FONTS_DIR / "material-symbols-rounded.woff2"
    _download(url.group(1), destino)
    _bump_cache_buster(destino)


def _bump_cache_buster(fuente: Path) -> None:
    """Cambia el `?v=` del @font-face al contenido real de la fuente.

    Quien ya tuvo la app abierta guarda la fuente vieja: sin esto vería el
    nombre del icono escrito en la pantalla aunque el repo esté correcto.
    """
    css = WEB / "static" / "styles" / "base.css"
    huella = hashlib.sha256(fuente.read_bytes()).hexdigest()[:8]
    texto, cambios = re.subn(
        r"(material-symbols-rounded\.woff2\?v=)[^']*",
        rf"\g<1>{huella}",
        css.read_text(encoding="utf-8"),
    )
    if cambios != 1:
        sys.exit(f"No se encontró el @font-face de los iconos en {css.name}")
    css.write_text(texto, encoding="utf-8")
    print(f"  base.css → ?v={huella}")


if __name__ == "__main__":
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    fetch_poppins()
    fetch_material_symbols()
