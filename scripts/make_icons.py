"""Genera los iconos de la app a partir de la marca.

Se versionan en el repo porque el manifest los pide por URL y un 404 rompe la
instalación en el móvil. Regenerar tras cambiar el color:

    uv run --with pillow python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "nutriplan" / "ui" / "web" / "static"

BRAND = (242, 109, 91)  # #F26D5B
CREAM = (251, 246, 243)  # #FBF6F3

# `any` lleva la esquina redondeada; `maskable` va a sangre porque Android le
# recorta encima la forma que quiera y una esquina propia se vería mal.
SIZES = [("icon-192.png", 192, True), ("icon-512.png", 512, True),
         ("icon-maskable.png", 512, False), ("icon-180.png", 180, True)]


def draw_bowl(img: Image.Image, *, inset: float) -> None:
    """Un tazón visto de frente: legible incluso a 48 px en una pantalla."""
    size = img.size[0]
    d = ImageDraw.Draw(img)
    pad = size * inset
    box = (pad, pad, size - pad, size - pad)
    w = box[2] - box[0]

    # El cuenco: media luna.
    bowl_top = box[1] + w * 0.42
    d.pieslice(
        (box[0], bowl_top - w * 0.30, box[2], bowl_top + w * 0.58),
        start=0, end=180, fill=CREAM,
    )
    # La comida asoma POR ENCIMA del borde y lo toca: separada parecen dos ojos.
    d.ellipse(
        (box[0] + w * 0.20, bowl_top - w * 0.17, box[0] + w * 0.52, bowl_top + w * 0.06),
        fill=CREAM,
    )
    d.ellipse(
        (box[0] + w * 0.50, bowl_top - w * 0.13, box[0] + w * 0.78, bowl_top + w * 0.06),
        fill=CREAM,
    )
    # La base.
    d.rounded_rectangle(
        (box[0] + w * 0.36, bowl_top + w * 0.56, box[0] + w * 0.64, bowl_top + w * 0.66),
        radius=w * 0.05, fill=CREAM,
    )


def build(name: str, size: int, rounded: bool) -> None:
    # Se dibuja al cuádruple y se reduce: los bordes salen suaves sin
    # antialiasing manual.
    scale = 4
    big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    if rounded:
        d.rounded_rectangle(
            (0, 0, size * scale, size * scale), radius=size * scale * 0.22, fill=BRAND
        )
    else:
        d.rectangle((0, 0, size * scale, size * scale), fill=BRAND)

    draw_bowl(big, inset=0.30 if not rounded else 0.22)
    big.resize((size, size), Image.LANCZOS).save(STATIC / name, "PNG", optimize=True)
    print(f"  {name}  {size}x{size}")


if __name__ == "__main__":
    print("Iconos:")
    for name, size, rounded in SIZES:
        build(name, size, rounded)
