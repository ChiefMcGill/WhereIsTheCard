import logging
import os
import time
from datetime import datetime

from app.extensions import db
from app.models import AuditLog, Checkout, log_event
from app.notifications.email import send_email

logger = logging.getLogger(__name__)


def check_overdue_checkouts():
    now = datetime.utcnow()
    active_checkouts = Checkout.query.filter(Checkout.returned_at.is_(None), Checkout.due_at < now).all()
    for checkout in active_checkouts:
        if checkout.overdue_notified:
            continue

        finance_email = os.getenv("FINANCE_EMAIL", "finance@solidground.co.za")
        card = checkout.card
        holder = checkout.user
        overdue_minutes = max(1, int((now - checkout.due_at).total_seconds() // 60))
        subject = f"OVERDUE: {card.name}"
        body = (
            f"{card.name} is overdue.\n\n"
            f"Current holder:\n{holder.name}\n\n"
            f"Checked out:\n{checkout.checked_out_at.strftime('%d %B %Y %H:%M')}\n\n"
            f"Due:\n{checkout.due_at.strftime('%d %B %Y %H:%M')}\n\n"
            f"Current time:\n{now.strftime('%d %B %Y %H:%M')}\n\n"
            f"Overdue:\n{overdue_minutes} minutes\n\n"
            f"Purpose:\n{checkout.purpose}\n\n"
            "Please locate the card."
        )

        sent = send_email(subject, body, finance_email)
        if sent:
            checkout.overdue_notified = True
            db.session.add(checkout)
            db.session.commit()
            logger.warning("Overdue email sent for checkout %s", checkout.id)
            log_event(holder, "overdue_notification", "checkout", checkout.id, f"Sent overdue email for {card.name}")


def run_overdue_loop(interval_seconds=60):
    logger.info("Starting overdue worker loop")
    while True:
        try:
            check_overdue_checkouts()
        except Exception:
            logger.exception("Unexpected error while checking overdue cards")
        time.sleep(interval_seconds)
