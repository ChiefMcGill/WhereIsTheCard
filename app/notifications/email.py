import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_email(subject, body, recipient, *, sender=None):
    host = os.getenv("SMTP_HOST")
    if not host or not recipient:
        logger.warning("Email not sent because SMTP configuration is missing or recipient is empty.")
        return False

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in {"1", "true", "yes", "on"}
    from_addr = sender or os.getenv("MAIL_FROM", "no-reply@solidground.co.za")

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
