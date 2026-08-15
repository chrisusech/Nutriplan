"""Adaptadores de envío de correo: consola en local, SMTP en producción."""

import asyncio
import smtplib
from email.message import EmailMessage

import structlog

from nutriplan.domain.errors import NutriPlanError

logger = structlog.get_logger(__name__)


class EmailError(NutriPlanError):
    """No se pudo entregar el correo."""


class ConsoleEmailSender:
    """Escribe el correo en el log en vez de enviarlo.

    Es el adaptador de local: deja ver el enlace de verificación sin montar un
    SMTP, y sin mandarle nada a nadie por accidente durante las pruebas.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        # Sin destinatario, sin cuerpo, sin token: el enlace vive en `sent`
        # para las pruebas, no en el log.
        logger.info("verification_email_sent")


class SmtpEmailSender:
    """SMTP con STARTTLS. Síncrono por dentro, en un hilo para no bloquear el loop."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        timeout: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._timeout = timeout

    def _send_blocking(self, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
            smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(message)

    async def send(self, *, to: str, subject: str, body: str) -> None:
        try:
            await asyncio.to_thread(self._send_blocking, to, subject, body)
        except (smtplib.SMTPException, OSError) as exc:
            # El asunto sí, el cuerpo no: lleva el token de un solo uso.
            logger.warning("email_failed", subject=subject, error=str(exc))
            raise EmailError("No se pudo enviar el correo") from exc
        logger.info("email_sent", subject=subject)
