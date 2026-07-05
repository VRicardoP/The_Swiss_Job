"""Tests para EmailService — envío SMTP con smtplib mockeado."""

from unittest.mock import MagicMock, patch

import pytest

from services.email_service import EmailService


def _service(**over) -> EmailService:
    cfg = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": 587,
        "SMTP_USER": "user@example.com",
        "SMTP_PASSWORD": "app-password",
        "SMTP_FROM": "",
        "SMTP_STARTTLS": True,
    }
    cfg.update(over)
    with patch("services.email_service.settings") as s:
        for k, v in cfg.items():
            setattr(s, k, v)
        return EmailService()


def _mock_smtp() -> MagicMock:
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


class TestAvailability:
    def test_not_available_without_host(self):
        assert _service(SMTP_HOST="").is_available is False

    def test_not_available_without_password(self):
        assert _service(SMTP_PASSWORD="").is_available is False

    def test_available_with_full_config(self):
        assert _service().is_available is True

    def test_from_defaults_to_user(self):
        # SMTP_FROM vacío → usa SMTP_USER como remitente.
        svc = _service(SMTP_FROM="")
        assert svc._from == "user@example.com"


class TestSend:
    def test_raises_when_not_configured(self):
        with pytest.raises(RuntimeError, match="SMTP no configurado"):
            _service(SMTP_HOST="").send("to@x.com", "subj", "body")

    def test_starttls_path_logs_in_and_sends(self):
        svc = _service(SMTP_PORT=587)
        smtp = _mock_smtp()
        with patch("services.email_service.smtplib.SMTP", return_value=smtp) as ctor:
            svc.send("to@x.com", "Aviso", "cuerpo", "<p>cuerpo</p>")
        ctor.assert_called_once()
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("user@example.com", "app-password")
        smtp.send_message.assert_called_once()

    def test_ssl_path_on_465(self):
        svc = _service(SMTP_PORT=465)
        smtp = _mock_smtp()
        with patch(
            "services.email_service.smtplib.SMTP_SSL", return_value=smtp
        ) as ctor:
            svc.send("to@x.com", "Aviso", "cuerpo")
        ctor.assert_called_once()
        smtp.starttls.assert_not_called()  # SSL implícito, sin STARTTLS
        smtp.send_message.assert_called_once()

    def test_message_has_html_alternative(self):
        svc = _service()
        smtp = _mock_smtp()
        with patch("services.email_service.smtplib.SMTP", return_value=smtp):
            svc.send("to@x.com", "S", "texto", "<b>html</b>")
        sent = smtp.send_message.call_args[0][0]
        assert sent["To"] == "to@x.com"
        assert sent["Subject"] == "S"
        assert sent.get_content_type() == "multipart/alternative"
