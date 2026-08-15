"""El enlace firmado que confirma un correo.

Solo esto: confirmar el correo es idempotente y no abre ninguna cuenta. La
recuperación de contraseña, que sí la abre, se prueba en
`test_recuperacion_de_clave.py` y no pasa por aquí.
"""

from nutriplan.domain.signed_links import issue_token, verify_token


def test_el_enlace_dice_de_quien_es_el_correo() -> None:
    secret = "test-secret"
    token = issue_token(email="user@example.com", secret=secret, ttl_seconds=3600)
    assert verify_token(token, secret=secret) == "user@example.com"


def test_un_enlace_retocado_o_firmado_con_otra_llave_no_vale() -> None:
    secret = "test-secret"
    token = issue_token(email="user@example.com", secret=secret)
    assert verify_token(token + "x", secret=secret) is None
    assert verify_token(token, secret="other") is None
