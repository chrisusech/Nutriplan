"""El domingo no rehace esta semana: arma la del lunes siguiente."""

from datetime import datetime

from nutriplan.domain.auto_week import (
    closed_and_target_weeks,
    in_auto_window,
    now_bogota,
    should_activate,
)


def test_el_domingo_por_la_tarde_esta_en_ventana() -> None:
    domingo = datetime(2026, 8, 16, 18, 5, tzinfo=now_bogota().tzinfo)
    assert in_auto_window(domingo)


def test_el_domingo_por_la_manana_todavia_no() -> None:
    domingo = datetime(2026, 8, 16, 10, 0, tzinfo=now_bogota().tzinfo)
    assert not in_auto_window(domingo)


def test_el_lunes_a_media_manana_recoge_a_quien_cerro_tarde() -> None:
    lunes = datetime(2026, 8, 17, 9, 0, tzinfo=now_bogota().tzinfo)
    assert in_auto_window(lunes)


def test_el_martes_no_genera() -> None:
    martes = datetime(2026, 8, 18, 19, 0, tzinfo=now_bogota().tzinfo)
    assert not in_auto_window(martes)


def test_el_domingo_apunta_al_lunes_siguiente() -> None:
    domingo = datetime(2026, 8, 16, 19, 0, tzinfo=now_bogota().tzinfo)
    cerrada, objetivo = closed_and_target_weeks(domingo)
    assert cerrada.isoformat() == "2026-08-10"
    assert objetivo.isoformat() == "2026-08-17"


def test_el_lunes_apunta_a_esta_semana_y_cierra_la_pasada() -> None:
    lunes = datetime(2026, 8, 17, 9, 0, tzinfo=now_bogota().tzinfo)
    cerrada, objetivo = closed_and_target_weeks(lunes)
    assert cerrada.isoformat() == "2026-08-10"
    assert objetivo.isoformat() == "2026-08-17"


def test_el_domingo_no_activa_el_menu_que_todavia_no_empieza() -> None:
    domingo = datetime(2026, 8, 16, 19, 0, tzinfo=now_bogota().tzinfo)
    _, objetivo = closed_and_target_weeks(domingo)
    assert not should_activate(objetivo, domingo)


def test_el_lunes_si_activa_el_menu_de_esta_semana() -> None:
    lunes = datetime(2026, 8, 17, 9, 0, tzinfo=now_bogota().tzinfo)
    _, objetivo = closed_and_target_weeks(lunes)
    assert should_activate(objetivo, lunes)
