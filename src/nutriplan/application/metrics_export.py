"""El informe del BETA en un solo CSV.

Un archivo y no diez: quien lo abre quiere mirarlo entero de una sentada, y
media hoja de cálculo se pierde en el camino si hay que descargar siete.
Por eso el archivo son BLOQUES: cada uno con su título, su cabecera y sus filas,
separados por una línea en blanco. Cualquier hoja de cálculo lo abre así y se
lee de arriba abajo como un informe.

Aquí no hay SQL ni framework: entran datos ya consultados y sale texto.
"""

import csv
import io
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

# Excel en Windows abre un CSV sin BOM en su codificación local y parte todos los
# acentos. Sheets y pandas lo ignoran, así que sale gratis.
BOM = "\ufeff"


@dataclass(frozen=True)
class Bloque:
    """Un trozo del informe: qué se está mirando y con qué columnas.

    `columnas` va de clave del dato a título de la columna, para que el CSV se
    lea con palabras y el código siga hablando con claves.
    """

    titulo: str
    explicacion: str
    columnas: dict[str, str]
    filas: Sequence[dict[str, Any]]


def to_csv(bloques: Sequence[Bloque]) -> str:
    """Los bloques, uno detrás de otro, en un CSV que se lee sin manual."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    for bloque in bloques:
        writer.writerow([bloque.titulo.upper()])
        writer.writerow([bloque.explicacion])
        writer.writerow(list(bloque.columnas.values()))
        if bloque.filas:
            for fila in bloque.filas:
                writer.writerow([_plano(fila.get(c, "")) for c in bloque.columnas])
        else:
            writer.writerow(["(todavía no hay nada)"])
        writer.writerow([])
    return BOM + buffer.getvalue()


@dataclass(frozen=True)
class Datos:
    """Todo lo consultado, ya traducido a palabras. Un campo por bloque."""

    embudo: Sequence[dict[str, Any]]
    platos: Sequence[dict[str, Any]]
    comentarios_de_platos: Sequence[dict[str, Any]]
    cierres: Sequence[dict[str, Any]]
    peticiones: Sequence[dict[str, Any]]
    opiniones: Sequence[dict[str, Any]]
    perfiles: Sequence[dict[str, Any]]
    alimentos: Sequence[dict[str, Any]]
    recetas: Sequence[dict[str, Any]]
    semanas: Sequence[dict[str, Any]]
    uso: Sequence[dict[str, Any]]


def informe(datos: Datos) -> list[Bloque]:
    """El orden en que se lee: primero cuánta gente, luego qué comió, luego qué
    dijo, y al final la letra pequeña del motor."""
    return [
        Bloque(
            "Resumen",
            "Cuánta gente llegó a cada paso. El laboratorio no cuenta.",
            {"paso": "Paso", "personas": "Personas"},
            datos.embudo,
        ),
        Bloque(
            "Platos",
            "Cada plato: cuántas veces se sirvió, cuántas se calificó y con qué notas.",
            {
                "plato": "Plato",
                "veces_servido": "Veces servido",
                "calificaciones": "Calificaciones",
                "nota_media": "Nota media",
                "cinco": "5 estrellas",
                "cuatro": "4",
                "tres": "3",
                "dos": "2",
                "una": "1",
                "plantilla": "Id interno",
            },
            datos.platos,
        ),
        Bloque(
            "Comentarios sobre platos",
            "El porqué de cada nota, en las palabras de quien comió.",
            {
                "fecha": "Fecha",
                "plato": "Plato",
                "comida": "Comida",
                "nota": "Nota",
                "comentario": "Lo que escribió",
            },
            datos.comentarios_de_platos,
        ),
        Bloque(
            "Cierres de semana",
            "El peso de cada semana y cómo dijo que le fue.",
            {
                "semana": "Semana",
                "persona": "Persona (seudónimo)",
                "peso_kg": "Peso (kg)",
                "comentario": "Cómo le fue",
            },
            datos.cierres,
        ),
        Bloque(
            "Lo que piden cambiar",
            "Alimentos que no quieren repetir, los que piden más y peticiones sueltas.",
            {
                "semana": "Semana",
                "persona": "Persona (seudónimo)",
                "tipo": "Qué pidió",
                "detalle": "Sobre qué",
                "lo_dedujo": "Lo dedujo",
            },
            datos.peticiones,
        ),
        Bloque(
            "Opiniones",
            "Lo que nos escriben desde Opinar.",
            {
                "fecha": "Fecha",
                "tipo": "Tipo",
                "nps": "Nota (0-10)",
                "desde": "Desde",
                "mensaje": "Mensaje",
            },
            datos.opiniones,
        ),
        Bloque(
            "Quiénes son y cómo comen",
            "Un perfil por persona, sin nada que diga quién es.",
            {
                "objetivo": "Objetivo",
                "sexo": "Sexo",
                "edad": "Edad",
                "actividad": "Actividad",
                "ciudad": "Ciudad",
                "comidas_al_dia": "Comidas al día",
                "restricciones": "Restricciones",
                "no_le_gusta": "No le gusta",
                "contexto": "Contexto",
                "como_come": "Cómo come (en sus palabras)",
            },
            datos.perfiles,
        ),
        Bloque(
            "Alimentos elegidos y quitados",
            "Qué marcó la gente como suyo en Mis alimentos, y qué echó fuera.",
            {
                "alimento": "Alimento",
                "grupo": "Grupo",
                "que_paso": "Qué pasó",
                "personas": "Personas",
            },
            datos.alimentos,
        ),
        Bloque(
            "Recetas",
            "Las recetas escritas, cuánto se han servido y qué nota tienen.",
            {
                "receta": "Receta",
                "veces_servida": "Veces servida",
                "nota_media": "Nota media",
                "votos": "Votos",
                "quien_la_escribio": "Quién la escribió",
                "minutos": "Minutos",
                "dificultad": "Dificultad",
                "retirada": "Retirada",
                "plantilla": "Id interno",
            },
            datos.recetas,
        ),
        Bloque(
            "Semanas generadas",
            "Cada menú que salió: cuándo, con qué motor y en qué versión.",
            {
                "semana": "Semana",
                "generada_el": "Generada el",
                "persona": "Persona (seudónimo)",
                "version": "Versión",
                "estado": "Estado",
                "motor": "Motor",
                "config": "Configuración",
            },
            datos.semanas,
        ),
        Bloque(
            "Uso día a día",
            "Cuántas veces pasó cada cosa en la app, últimos 90 días.",
            {"dia": "Día", "evento": "Qué pasó", "veces": "Veces"},
            datos.uso,
        ),
    ]


def _plano(valor: Any) -> str:
    """Un salto de línea dentro de una celda parte la fila en dos al abrirla en
    algunas hojas de cálculo; los comentarios largos vienen llenos."""
    texto = "" if valor is None else str(valor)
    return " ".join(texto.split())
