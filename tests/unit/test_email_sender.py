"""Los dos adaptadores de correo, sin tocar la red."""

import smtplib

import pytest

from nutriplan.adapters.email import ConsoleEmailSender, EmailError, SmtpEmailSender


async def test_en_local_el_correo_se_queda_a_la_vista_y_no_sale_a_ningun_lado() -> None:
    sender = ConsoleEmailSender()
    await sender.send(to="ana@correo.com", subject="Hola", body="Cuerpo")
    assert sender.sent == [{"to": "ana@correo.com", "subject": "Hola", "body": "Cuerpo"}]


async def test_el_log_de_alta_no_deja_correo_ni_enlace_de_verificacion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El enlace vive en memoria para las pruebas; el log no lleva PII.

    No usamos `capture_logs`: `configure_logging` cachea el logger y, en la
    suite completa, los processors de structlog ya no son los de la captura.
    """
    recorded: list[tuple[str, dict[str, str]]] = []

    def _info(event: str, **kw: str) -> None:
        recorded.append((event, kw))

    monkeypatch.setattr("nutriplan.adapters.email.logger.info", _info)
    sender = ConsoleEmailSender()
    await sender.send(
        to="ana@correo.com",
        subject="Confirma tu correo",
        body="https://app.nutriplan.test/verificar/token-secreto",
    )
    assert recorded == [("console_email_sent", {"subject": "Confirma tu correo"})]
    blob = " ".join(str(item) for item in recorded)
    assert "ana@correo.com" not in blob
    assert "/verificar/" not in blob


async def test_en_local_el_asunto_sale_por_stderr_sin_el_token(capsys) -> None:
    sender = ConsoleEmailSender(dump_body=True)
    await sender.send(
        to="ana@correo.com",
        subject="Restablece tu contraseña",
        body="https://app.nutriplan.test/recuperar/token-secreto",
    )
    err = capsys.readouterr().err
    assert "Restablece tu contraseña" in err
    assert "token-secreto" not in err
    assert "/recuperar/" not in err


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
