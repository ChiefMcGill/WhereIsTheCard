import logging
import os
import smtplib
from email.message import EmailMessage

from flask import current_app

from app.models import AppSetting

logger = logging.getLogger(__name__)


def _get_email_setting(key, default=""):
    value = AppSetting.get(key, os.getenv(key, default))
    if value is None:
        return default
    return str(value)


def send_email(subject, body, recipient, *, sender=None):
    host = _get_email_setting("SMTP_HOST")
    if not host or not recipient:
        logger.warning("Email not sent because SMTP configuration is missing or recipient is empty.")
        return False

    port = int(_get_email_setting("SMTP_PORT", "587"))
    username = _get_email_setting("SMTP_USERNAME")
    password = _get_email_setting("SMTP_PASSWORD")
    use_tls = _get_email_setting("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
    from_addr = sender or _get_email_setting("MAIL_FROM", "no-reply@solidground.co.za")

    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = from_addr
        message["To"] = recipient
        message.set_content(body)

        with smtplib.SMTP(host, port, timeout=15) as smtp:
            if use_tls:
                smtp.starttls()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)

        logger.info("Email sent successfully to %s", recipient)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", recipient)
        return False
