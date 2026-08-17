"""El último día de TU tira arma la siguiente; no el domingo del calendario."""

from datetime import date, datetime, timedelta

from nutriplan.domain.auto_week import (
    closed_and_target_weeks,
    in_auto_window,
    now_bogota,
    should_activate,
)

# Quien se registró un sábado: tira sáb 15 → vie 21, siguiente sáb 22.
_SABADO = date(2026, 8, 15)


def test_el_ultimo_dia_por_la_tarde_esta_en_ventana() -> None:
    viernes = datetime(2026, 8, 21, 18, 5, tzinfo=now_bogota().tzinfo)
    assert in_auto_window(_SABADO, viernes)


def test_el_ultimo_dia_por_la_manana_todavia_no() -> None:
    viernes = datetime(2026, 8, 21, 10, 0, tzinfo=now_bogota().tzinfo)
    assert not in_auto_window(_SABADO, viernes)


def test_el_dia_ocho_a_media_manana_recoge_a_quien_cerro_tarde() -> None:
    siguiente = datetime(2026, 8, 22, 9, 0, tzinfo=now_bogota().tzinfo)
    assert in_auto_window(_SABADO, siguiente)


def test_un_dia_en_medio_de_la_tira_no_genera() -> None:
    martes = datetime(2026, 8, 18, 19, 0, tzinfo=now_bogota().tzinfo)
    assert not in_auto_window(_SABADO, martes)


def test_el_cierre_apunta_a_los_siete_dias_siguientes() -> None:
    cerrada, objetivo = closed_and_target_weeks(_SABADO)
    assert cerrada == _SABADO
    assert objetivo == date(2026, 8, 22)


def test_el_ultimo_dia_no_activa_el_menu_que_todavia_no_empieza() -> None:
    viernes = datetime(2026, 8, 21, 19, 0, tzinfo=now_bogota().tzinfo)
    _, objetivo = closed_and_target_weeks(_SABADO)
    assert not should_activate(objetivo, viernes)


def test_el_dia_ocho_si_activa_el_menu_nuevo() -> None:
    siguiente = datetime(2026, 8, 22, 9, 0, tzinfo=now_bogota().tzinfo)
    _, objetivo = closed_and_target_weeks(_SABADO)
    assert should_activate(objetivo, siguiente)


def test_una_tira_que_empieza_lunes_sigue_cerrando_el_domingo() -> None:
    """Quien generó un lunes no pierde el ritmo de siempre."""
    lunes = date(2026, 8, 10)
    domingo = datetime(2026, 8, 16, 18, 5, tzinfo=now_bogota().tzinfo)
    assert in_auto_window(lunes, domingo)
    assert closed_and_target_weeks(lunes) == (lunes, lunes + timedelta(days=7))
