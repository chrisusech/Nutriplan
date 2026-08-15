"""La CSP no admite estilos inline. Ni en el HTML, ni desde JavaScript.

Es un fallo silencioso: el navegador bloquea el estilo, no rompe nada visible
en el código, y la pantalla sale mal. Pasó dos veces —la pantalla de login sin
ancho, y la barra de progreso del onboarding que no avanzaba— así que la regla
se comprueba en vez de recordarse.
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "src" / "nutriplan" / "ui" / "web"


def test_ninguna_plantilla_usa_el_atributo_style() -> None:
    culpables = [
        f"{ruta.name}:{i}"
        for ruta in WEB.joinpath("templates").rglob("*.html")
        for i, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1)
        if re.search(r"\sstyle\s*=\s*[\"']", linea)
    ]
    assert not culpables, f"La CSP bloqueará estos estilos: {culpables}. Usa una clase."


def test_el_javascript_no_pinta_estilos_a_mano() -> None:
    """`element.style.x = …` y `setProperty` los bloquea la CSP igual que el
    atributo del HTML. Lo que cambia desde JS se cambia con clases o `data-`."""
    culpables = []
    for ruta in WEB.joinpath("static").rglob("*.js"):
        for i, linea in enumerate(ruta.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.style\.\w+\s*=|setProperty\s*\(", linea):
                culpables.append(f"{ruta.name}:{i}")
    assert not culpables, (
        f"La CSP bloqueará estos estilos: {culpables}. "
        "Cambia una clase o un atributo `data-` y deja el ancho al CSS."
    )
