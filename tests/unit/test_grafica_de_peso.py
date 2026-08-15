"""La gráfica de barras de Mi progreso: qué ve quien mira su historia."""

from nutriplan.ui.web.chart import barras


def test_quien_no_tiene_pesajes_no_ve_ninguna_barra() -> None:
    assert barras([]) == []


def test_la_semana_de_hoy_es_la_ultima_barra_y_va_destacada() -> None:
    dibujo = barras([70.0, 69.2, 68.5])
    assert [b.destacada for b in dibujo] == [False, False, True]
    assert dibujo[0].x < dibujo[1].x < dibujo[2].x


def test_bajar_un_kilo_sobre_setenta_se_nota_en_la_altura() -> None:
    """Contra un eje que empiece en cero, 70 y 69 serían la misma barra."""
    primera, ultima = barras([70.0, 69.0])
    assert primera.h - ultima.h > 20


def test_ninguna_barra_se_sale_del_lienzo_ni_se_queda_sin_cuerpo() -> None:
    dibujo = barras([70.0, 69.4, 69.6, 68.8, 68.1, 68.3, 67.4], width=320, height=120)
    for b in dibujo:
        assert b.h > 8, "una semana peor no puede desaparecer del gráfico"
        assert 0 <= b.x and b.x + b.w <= 320
        assert round(b.y + b.h, 1) == 120


def test_con_tres_semanas_las_barras_no_se_convierten_en_tablones() -> None:
    assert all(b.w <= 40 for b in barras([70.0, 69.4, 68.8]))


def test_pesar_lo_mismo_toda_la_vida_dibuja_barras_iguales() -> None:
    alturas = {b.h for b in barras([70.0, 70.0, 70.0])}
    assert len(alturas) == 1
