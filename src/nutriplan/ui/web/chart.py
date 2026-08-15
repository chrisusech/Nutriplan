"""Gráficas mínimas en SVG.

La geometría de un `<rect x= y= width= height=>` son atributos de datos, no un
estilo: pasa la CSP sin pedir permiso y no necesita ni una librería ni un canvas.
El color y el redondeo los pone la hoja de estilos.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Barra:
    """Una barra ya resuelta en coordenadas del `viewBox`."""

    x: float
    y: float
    w: float
    h: float
    destacada: bool


def barras(
    values: list[float],
    *,
    width: float = 320.0,
    height: float = 120.0,
    hueco: float = 0.34,
    ancho_max: float = 40.0,
) -> list[Barra]:
    """Un peso por barra, en el orden en que ocurrieron.

    La escala no arranca en cero: contra el cero, dos kilos de diferencia sobre
    setenta serían seis barras idénticas. Se encuadra el rango real con un
    margen, de modo que la más baja siga teniendo cuerpo y la diferencia entre
    semanas se lea de un vistazo. La última va destacada: es la de hoy.

    El ancho tiene tope: con tres semanas, repartir el hueco entre tres barras
    daría tres tablones y no una gráfica.
    """
    if not values:
        return []
    paso = width / len(values)
    ancho = min(paso * (1 - hueco), ancho_max)
    bajo, alto = min(values), max(values)
    margen = max(0.4, (alto - bajo) * 0.6)
    suelo, techo = bajo - margen, alto + margen
    ultima = len(values) - 1
    return [
        Barra(
            x=round(paso * i + (paso - ancho) / 2, 1),
            y=round(height * (1 - (v - suelo) / (techo - suelo)), 1),
            w=round(ancho, 1),
            h=round(height * (v - suelo) / (techo - suelo), 1),
            destacada=i == ultima,
        )
        for i, v in enumerate(values)
    ]
