"""Los iconos de la app existen en la fuente que se sirve.

La fuente va subseteada a los iconos que se usan. Añadir uno nuevo y olvidar
regenerarla no rompe nada visible en el código: simplemente sale el NOMBRE del
icono escrito en la pantalla. Pasó con el spinner de "generando tu menú" y otra
vez con el de la receta, que lo pinta el JS y no ninguna plantilla.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "src" / "nutriplan" / "ui" / "web"
FUENTE = WEB / "static" / "fonts" / "material-symbols-rounded.woff2"


SPAN = re.compile(r'class="icon[^"]*"[^>]*>(.*?)</span>', re.S)
LITERAL = re.compile(r"'([a-z_]+)'")


def _iconos_de_la_plantilla(texto: str) -> set[str]:
    """Los nombres escritos en un `<span class="icon">`, también dentro de Jinja.

    Un `{{ 'check_circle' if … else 'radio_button_unchecked' }}` es tan icono
    como uno escrito a pelo, y olvidar el del `else` sale en pantalla.
    """
    encontrados: set[str] = set()
    for dentro in SPAN.findall(texto):
        crudo = dentro.strip()
        if "{" in crudo:
            encontrados |= set(LITERAL.findall(crudo))
        elif re.fullmatch(r"[a-z_]+", crudo):
            encontrados.add(crudo)
    return encontrados


def _iconos_usados() -> set[str]:
    encontrados: set[str] = set()
    for ruta in [
        *WEB.joinpath("templates").rglob("*.html"),
        # El JS pinta iconos que no están en ninguna plantilla (el spinner de la
        # receta), y por eso el nombre salió girando en la tarjeta del plato.
        *WEB.joinpath("static", "js").glob("*.js"),
        WEB / "presenter.py",
        WEB / "week_view.py",
    ]:
        texto = ruta.read_text(encoding="utf-8")
        encontrados |= _iconos_de_la_plantilla(texto)
        encontrados |= set(re.findall(r'"icon":\s*"([a-z_]+)"', texto))
    return encontrados


def _ligaduras_de_la_fuente() -> set[str]:
    fonttools = pytest.importorskip("fontTools.ttLib")
    fuente = fonttools.TTFont(FUENTE)
    letra = {glifo: chr(punto) for punto, glifo in fuente.getBestCmap().items()}
    nombres: set[str] = set()
    for lookup in fuente["GSUB"].table.LookupList.Lookup:
        for subtabla in lookup.SubTable:
            # Las ligaduras vienen envueltas en subtablas de extensión.
            real = getattr(subtabla, "ExtSubTable", subtabla)
            for primero, conjuntos in getattr(real, "ligatures", {}).items():
                for liga in conjuntos:
                    nombres.add(
                        letra.get(primero, "?") + "".join(letra.get(g, "?") for g in liga.Component)
                    )
    return nombres


def test_todos_los_iconos_de_la_app_estan_en_la_fuente() -> None:
    """Si esto falla: `uv run python scripts/fetch_fonts.py`."""
    faltan = sorted(_iconos_usados() - _ligaduras_de_la_fuente())
    assert not faltan, (
        f"Estos iconos saldrían como texto: {faltan}. "
        "Regenera la fuente con `uv run python scripts/fetch_fonts.py`."
    )


def test_la_fuente_de_iconos_no_engorda_sin_darnos_cuenta() -> None:
    """Subsetear existe para no servir 3 MB en un móvil con datos."""
    assert FUENTE.stat().st_size < 120_000
