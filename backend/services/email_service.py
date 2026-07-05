"""EmailService — envío de correos vía SMTP (stdlib, sin dependencia nueva).

Síncrono a propósito: se llama desde tareas Celery (`def`), que no son async.
Soporta STARTTLS (puerto 587, p.ej. Gmail) y SSL implícito (puerto 465).
Vacío en config → `is_available` False y el llamante omite el envío.
"""

import logging
import smtplib
from email.message import EmailMessage

from config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Envía emails por SMTP con la configuración de settings (SMTP_*)."""

    def __init__(self) -> None:
        self._host = settings.SMTP_HOST
        self._port = settings.SMTP_PORT
        self._user = settings.SMTP_USER
        self._password = settings.SMTP_PASSWORD
        self._from = settings.SMTP_FROM or settings.SMTP_USER
        self._starttls = settings.SMTP_STARTTLS

    @property
    def is_available(self) -> bool:
        return bool(self._host and self._user and self._password and self._from)

    def send(
        self,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
    ) -> None:
        """Envía un email de texto (con alternativa HTML opcional). Lanza en error."""
        if not self.is_available:
            raise RuntimeError("SMTP no configurado (SMTP_HOST/USER/PASSWORD/FROM)")

        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body_text)
        if body_html:
            msg.add_alternative(body_html, subtype="html")

        # Puerto 465 = SSL implícito; el resto = SMTP normal + STARTTLS opcional.
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=30) as smtp:
                smtp.login(self._user, self._password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
                if self._starttls:
                    smtp.starttls()
                smtp.login(self._user, self._password)
                smtp.send_message(msg)
