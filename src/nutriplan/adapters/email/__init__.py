"""Adaptadores de envío de correo: consola en local, SMTP en producción."""

import asyncio
import smtplib
import sys
from email.message import EmailMessage

import structlog

from nutriplan.domain.errors import NutriPlanError

logger = structlog.get_logger(__name__)


class EmailError(NutriPlanError):
    """No se pudo entregar el correo."""


class ConsoleEmailSender:
    """Adaptador de local: el enlace no viaja por SMTP.

    El log estructurado solo anota el asunto (sin destinatario ni token). En
    local el asunto sale por stderr para confirmar el envío; el enlace con el
    token de un solo uso no se imprime.
    """

    def __init__(self, *, dump_body: bool = False) -> None:
        self.sent: list[dict[str, str]] = []
        self._dump_body = dump_body

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        logger.info("console_email_sent", subject=subject)
        if self._dump_body:
            print(f"[mail] {subject}", file=sys.stderr)


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
