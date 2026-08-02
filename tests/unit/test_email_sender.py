"""Los dos adaptadores de correo, sin tocar la red."""

import smtplib

import pytest

from nutriplan.adapters.email import ConsoleEmailSender, EmailError, SmtpEmailSender


async def test_en_local_el_correo_se_queda_a_la_vista_y_no_sale_a_ningun_lado() -> None:
    sender = ConsoleEmailSender()
    await sender.send(to="ana@correo.com", subject="Hola", body="Cuerpo")
    assert sender.sent == [{"to": "ana@correo.com", "subject": "Hola", "body": "Cuerpo"}]


async def test_un_smtp_caido_falla_ruidosamente_y_no_en_silencio(monkeypatch) -> None:
    """Un correo perdido sin traza es una cuenta que nadie puede verificar."""

    def _boom(*args, **kwargs):
        raise smtplib.SMTPException("servidor caído")

    monkeypatch.setattr("smtplib.SMTP", _boom)
    sender = SmtpEmailSender(
        host="smtp.test", port=587, username="u", password="p", sender="no-reply@test"
    )
    with pytest.raises(EmailError):
        await sender.send(to="ana@correo.com", subject="Hola", body="Cuerpo")
