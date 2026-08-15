"""Cerrar la semana: peso y una frase de cómo fue.

Calificar platos ayuda, pero ya no es puerta. Estas historias fijan lo que se
le pide a la persona y lo que se le dice cuando le falta algo.
"""

from __future__ import annotations

from nutriplan.domain.week_close import MIN_COMMENT_CHARS, WeekClosure, is_valid_comment


def _cierre(
    *,
    peso: bool = True,
    notas: int = 0,
    comentario: bool = True,
    primera: bool = False,
) -> WeekClosure:
    return WeekClosure(
        has_weight=peso,
        ratings=notas,
        ratings_required=0,
        has_comment=comentario,
        is_first_week=primera,
    )


def test_la_primera_semana_solo_pide_el_peso() -> None:
    """No hubo plan que calificar: pedir notas sería pedir lo imposible."""
    assert _cierre(peso=True, notas=0, comentario=False, primera=True).is_closed


def test_la_primera_semana_sin_peso_no_esta_cerrada() -> None:
    cierre = _cierre(peso=False, notas=0, comentario=False, primera=True)
    assert not cierre.is_closed
    assert cierre.missing == ["registrar tu peso de esta semana"]


def test_con_peso_y_comentario_la_semana_queda_cerrada() -> None:
    cierre = _cierre(notas=0)
    assert cierre.is_closed
    assert cierre.missing == []
    assert cierre.hint == ""
    assert cierre.progress == 100


def test_sin_calificar_tambien_se_cierra() -> None:
    """Las notas alimentan el motor; no bloquean el domingo."""
    assert _cierre(notas=0, comentario=True).is_closed


def test_a_quien_le_falta_todo_se_le_enumera() -> None:
    cierre = _cierre(peso=False, notas=0, comentario=False)
    assert cierre.missing == [
        "registrar tu peso de esta semana",
        "contarnos cómo te fue",
    ]
    assert cierre.hint.startswith("Para tu semana siguiente te falta:")


def test_cuando_falta_una_sola_cosa_la_frase_no_lleva_lista() -> None:
    cierre = _cierre(comentario=False)
    assert cierre.hint == ("Para tu semana siguiente te falta contarnos cómo te fue.")


def test_la_barra_avanza_a_medida_que_completa_el_cierre() -> None:
    assert _cierre(peso=False, notas=0, comentario=False).progress == 0
    assert 0 < _cierre(comentario=False).progress < 100
    assert _cierre().progress == 100


def test_la_barra_se_pinta_por_decenas_porque_la_csp_prohibe_estilos() -> None:
    assert _cierre().progress_bucket == 10
    assert _cierre(peso=False, comentario=False).progress_bucket == 0


def test_un_espacio_no_es_contarnos_como_te_fue() -> None:
    assert not is_valid_comment("   ")
    assert not is_valid_comment(None)
    assert not is_valid_comment("ok")
    assert is_valid_comment("x" * MIN_COMMENT_CHARS)


def test_una_frase_de_verdad_si_cuenta() -> None:
    assert is_valid_comment("Me fue bien, aunque el pescado no lo repetiría")
