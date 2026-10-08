import logging
import os
import time
from datetime import datetime

from app.extensions import db
from app.models import AppSetting, AuditLog, Checkout, log_event
from app.notifications.email import send_email

logger = logging.getLogger(__name__)


def check_overdue_checkouts():
    now = datetime.utcnow()
    active_checkouts = Checkout.query.filter(Checkout.returned_at.is_(None)).all()
    for checkout in active_checkouts:
        card = checkout.card
        holder = checkout.user
        if holder is None:
            continue

        if not checkout.reminder_sent and checkout.due_at > now and (checkout.due_at - now).total_seconds() <= 300:
            subject = f"Reminder: please return {card.name} soon"
            body = (
                f"Hello {holder.name},\n\n"
                f"This is a friendly reminder that the card {card.name} is due for return in 5 minutes.\n\n"
                f"Current due time:\n{checkout.due_at.strftime('%d %B %Y %H:%M')}\n\n"
                f"Purpose:\n{checkout.purpose}\n\n"
                "Please return the card when you are finished so it can be signed back in. Thank you."
            )
            sent = send_email(subject, body, holder.email)
            if sent:
                checkout.reminder_sent = True
                db.session.add(checkout)
                db.session.commit()
                log_event(holder, "checkout_reminder_sent", "checkout", checkout.id, f"Reminder sent for {card.name}")

        if checkout.due_at <= now and not checkout.overdue_notified:
            finance_email = AppSetting.get("FINANCE_EMAIL", "finance@solidground.co.za")
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

        if checkout.due_at <= now and not checkout.senior_pastor_notified:
            threshold_minutes = AppSetting.get_int("SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES", 60)
            overdue_minutes = max(0, int((now - checkout.due_at).total_seconds() // 60))
            if overdue_minutes >= threshold_minutes:
                senior_email = AppSetting.get("SENIOR_PASTOR_EMAIL", "seniorpastor@solidground.co.za")
                if senior_email:
                    subject = f"Senior pastor alert: {card.name} overdue"
                    body = (
                        f"The card {card.name} has been overdue for {overdue_minutes} minutes.\n\n"
                        f"Current holder:\n{holder.name}\n\n"
                        f"Purpose:\n{checkout.purpose}\n\n"
                        f"Due time:\n{checkout.due_at.strftime('%d %B %Y %H:%M')}\n\n"
                        "Please review this card and follow up with the holder."
                    )
                    sent = send_email(subject, body, senior_email)
                    if sent:
                        checkout.senior_pastor_notified = True
                        db.session.add(checkout)
                        db.session.commit()
                        log_event(holder, "senior_pastor_notification", "checkout", checkout.id, f"Senior pastor notified for {card.name}")


def run_overdue_loop(interval_seconds=60):
    logger.info("Starting overdue worker loop")
    while True:
        try:
            check_overdue_checkouts()
        except Exception:
            logger.exception("Unexpected error while checking overdue cards")
        time.sleep(interval_seconds)
