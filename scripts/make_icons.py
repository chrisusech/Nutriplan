"""Genera los iconos de la app a partir de la marca.

Se versionan en el repo porque el manifest los pide por URL y un 404 rompe la
instalación en el móvil. Regenerar tras cambiar el color:

    uv run --with pillow python scripts/make_icons.py

También deja el icono y el splash del envoltorio nativo, para que el icono del
teléfono sea el mismo que el de la pestaña. Las fuentes viven en
`mobile/assets/` (las lee `@capacitor/assets` al regenerar plataformas) y se
copian al catálogo de iOS si existe: `mobile/ios/` está fuera de git, así que
tras un `cap add ios` hay que volver a pasar este script.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "nutriplan" / "ui" / "web" / "static"
MOBILE_ASSETS = ROOT / "mobile" / "assets"
IOS_ASSETS = ROOT / "mobile" / "ios" / "App" / "App" / "Assets.xcassets"

BRAND = (242, 109, 91)  # #F26D5B
CREAM = (251, 246, 243)  # #FBF6F3
NIGHT = (19, 16, 16)  # #131010 — el fondo de la app y del splash

# El cubierto se dibuja con la propia fuente de iconos de la app (`restaurant`,
# la insignia de la cabecera): así el icono del teléfono es exactamente el mismo
# símbolo que ve dentro, y no un parecido hecho a mano.
ICON_FONT = ROOT / "src" / "nutriplan" / "ui" / "web" / "static" / "fonts"
ICON_FONT /= "material-symbols-rounded.woff2"
CUTLERY = "\ue56c"

# `any` lleva la esquina redondeada; `maskable` va a sangre porque Android le
# recorta encima la forma que quiera y una esquina propia se vería mal.
# El de 32 es el de la pestaña del navegador: escalar el de 192 a ese tamaño
# emborrona el cubierto, y es el icono que más se mira sin darse cuenta.
SIZES = [
    ("icon-192.png", 192, True),
    ("icon-512.png", 512, True),
    ("icon-maskable.png", 512, False),
    ("icon-180.png", 180, True),
    ("icon-32.png", 32, True),
]


def draw_cutlery(img: Image.Image, *, inset: float) -> None:
    """El tenedor y el cuchillo, centrados. Se engorda el trazo (`wght`) porque
    el grosor de pantalla desaparece a 32 px en la pestaña del navegador."""
    size = img.size[0]
    font = ImageFont.truetype(str(ICON_FONT), size=round(size * (1 - inset * 2)))
    font.set_variation_by_axes([0.0, 48.0, 600.0])  # FILL, opsz, wght
    ImageDraw.Draw(img).text((size / 2, size / 2), CUTLERY, font=font, fill=CREAM, anchor="mm")


def render(size: int, *, rounded: bool) -> Image.Image:
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

    draw_cutlery(big, inset=0.24 if rounded else 0.30)
    return big.resize((size, size), Image.LANCZOS)


def build(name: str, size: int, rounded: bool) -> None:
    render(size, rounded=rounded).save(STATIC / name, "PNG", optimize=True)
    print(f"  {name}  {size}x{size}")


def build_app_icon() -> Image.Image:
    """El de la App Store va a sangre y sin alfa: la esquina la recorta iOS, y
    un canal alfa es motivo de rechazo al subir."""
    icon = Image.new("RGB", (1024, 1024), BRAND)
    art = render(1024, rounded=False)
    icon.paste(art, (0, 0), art)
    return icon


def build_splash() -> Image.Image:
    """Cuadrado porque el `LaunchScreen` lo recorta a lo ancho: la marca tiene
    que caber en el centro. Oscuro siempre, como la app."""
    canvas = Image.new("RGB", (2732, 2732), NIGHT)
    badge = render(560, rounded=True)
    canvas.paste(badge, (1086, 1086), badge)
    return canvas


def build_native() -> None:
    icon, splash = build_app_icon(), build_splash()
    MOBILE_ASSETS.mkdir(parents=True, exist_ok=True)
    icon.save(MOBILE_ASSETS / "icon.png", "PNG", optimize=True)
    for name in ("splash.png", "splash-dark.png"):
        splash.save(MOBILE_ASSETS / name, "PNG", optimize=True)
    print("  mobile/assets/icon.png  1024x1024")
    print("  mobile/assets/splash*.png  2732x2732")

    if not IOS_ASSETS.exists():
        print("  (sin mobile/ios: falta `npx cap add ios`)")
        return
    icon.save(IOS_ASSETS / "AppIcon.appiconset" / "AppIcon-512@2x.png", "PNG", optimize=True)
    for name in ("splash-2732x2732.png", "splash-2732x2732-1.png", "splash-2732x2732-2.png"):
        splash.save(IOS_ASSETS / "Splash.imageset" / name, "PNG", optimize=True)
    print("  catálogo de iOS al día")


if __name__ == "__main__":
    print("Iconos:")
    for name, size, rounded in SIZES:
        build(name, size, rounded)
    build_native()
