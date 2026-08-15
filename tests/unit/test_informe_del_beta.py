"""El CSV que se descarga desde Métricas: qué se lee al abrirlo."""

from nutriplan.application.metrics_export import Bloque, to_csv
from nutriplan.ui.web.metrics_view import relabel, with_dish_name
from nutriplan.ui.web.presenter import EVENT_LABELS


def _bloque(filas: list[dict[str, object]]) -> Bloque:
    return Bloque(
        titulo="Platos",
        explicacion="Qué se sirvió y qué tal salió.",
        columnas={"plato": "Plato", "nota_media": "Nota media"},
        filas=filas,
    )


def test_las_columnas_se_leen_con_palabras_y_no_con_claves() -> None:
    texto = to_csv([_bloque([{"plato": "Pollo con arroz", "nota_media": 4.5}])])
    assert "Plato,Nota media" in texto
    assert "nota_media" not in texto
    assert "Pollo con arroz,4.5" in texto


def test_cada_bloque_dice_de_que_va_antes_de_su_tabla() -> None:
    texto = to_csv([_bloque([])]).splitlines()
    assert texto[0].lstrip("\ufeff") == "PLATOS"
    assert texto[1] == "Qué se sirvió y qué tal salió."


def test_un_bloque_vacio_lo_dice_en_vez_de_parecer_un_error() -> None:
    assert "(todavía no hay nada)" in to_csv([_bloque([])])


def test_un_comentario_de_varias_lineas_no_parte_la_fila_en_dos() -> None:
    texto = to_csv([_bloque([{"plato": "Ensalada", "nota_media": "me\nsupo\nraro"}])])
    assert "me supo raro" in texto
    assert len(texto.strip().splitlines()) == 4


def test_el_informe_se_abre_bien_en_excel_con_acentos() -> None:
    """Sin BOM, Excel en Windows convierte cada tilde en dos símbolos."""
    assert to_csv([_bloque([])]).startswith("\ufeff")


def test_quien_administra_lee_frases_y_no_nombres_de_evento() -> None:
    filas = relabel([{"evento": "dish_rated", "total": 25}], evento=EVENT_LABELS)
    assert filas[0]["evento"] == "Calificaron un plato"


def test_un_evento_nuevo_sin_traducir_se_ve_tal_cual_en_vez_de_esconderse() -> None:
    filas = relabel([{"evento": "cocino_algo", "total": 1}], evento=EVENT_LABELS)
    assert filas[0]["evento"] == "cocino_algo"


def test_cada_plato_sale_con_su_nombre_al_lado_de_su_id() -> None:
    filas = with_dish_name(
        [{"plantilla": "proteina_carbo_ensalada"}],
        {"proteina_carbo_ensalada": "Proteína con carbohidrato y ensalada"},
    )
    assert filas[0]["plato"] == "Proteína con carbohidrato y ensalada"
    assert filas[0]["plantilla"] == "proteina_carbo_ensalada"
