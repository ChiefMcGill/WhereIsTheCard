import os
from datetime import datetime

from flask_login import UserMixin
from sqlalchemy import Index, text
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


ROLE_CHOICES = ("USER", "FINANCE", "SENIOR_PASTOR", "ADMIN")


def utc_now():
    return datetime.utcnow()


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(32), nullable=False, default="USER")
    active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True)

    checkouts = db.relationship("Checkout", back_populates="user", foreign_keys="Checkout.user_id")
    extension_requests = db.relationship("ExtensionRequest", back_populates="user", foreign_keys="ExtensionRequest.user_id")
    audit_logs = db.relationship("AuditLog", back_populates="user")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def current_checkout(self):
        return Checkout.query.filter_by(user_id=self.id, returned_at=None).first()

    def has_role(self, *roles):
        return self.role in roles

    def __repr__(self):
        return f"<User {self.email}>"


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(128), unique=True, index=True, nullable=False)
    value = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(key, default=None):
        setting = AppSetting.query.filter_by(key=key).first()
        if setting is None:
            return default
        return setting.value

    @staticmethod
    def get_int(key, default=0):
        value = AppSetting.get(key)
        if value is None:
            return int(default)
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return int(default)

    @staticmethod
    def set(key, value):
        setting = AppSetting.query.filter_by(key=key).first()
        if setting is None:
            setting = AppSetting(key=key, value=str(value) if value is not None else "")
        else:
            setting.value = str(value) if value is not None else ""
        db.session.add(setting)
        db.session.commit()
        return setting.value

    @staticmethod
    def get_bool(key, default=False):
        value = AppSetting.get(key)
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Card(db.Model):
    __tablename__ = "cards"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    checkouts = db.relationship("Checkout", back_populates="card")
    extension_requests = db.relationship("ExtensionRequest", back_populates="card")

    @property
    def current_checkout(self):
        return Checkout.query.filter_by(card_id=self.id, returned_at=None).first()

    @property
    def status_text(self):
        current = self.current_checkout
        if current is None:
            return "AVAILABLE"
        if current.is_overdue:
            return "OVERDUE"
        return "CHECKED OUT"

    @property
    def holder_name(self):
        current = self.current_checkout
        if current and current.user:
            return current.user.name
        return ""


class Checkout(db.Model):
    __tablename__ = "checkouts"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    purpose = db.Column(db.String(300), nullable=False)
    checked_out_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    original_due_at = db.Column(db.DateTime, nullable=False)
    due_at = db.Column(db.DateTime, nullable=False)
    returned_at = db.Column(db.DateTime, nullable=True)
    overdue_notified = db.Column(db.Boolean, default=False, nullable=False)
    reminder_sent = db.Column(db.Boolean, default=False, nullable=False)
    senior_pastor_notified = db.Column(db.Boolean, default=False, nullable=False)
    overdue_reason = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    card = db.relationship("Card", back_populates="checkouts")
    user = db.relationship("User", back_populates="checkouts", foreign_keys=[user_id])
    extension_requests = db.relationship("ExtensionRequest", back_populates="checkout")

    __table_args__ = (
        Index(
            "ix_active_checkout_per_card",
            "card_id",
            unique=True,
            sqlite_where=text("returned_at IS NULL"),
        ),
    )

    @property
    def is_overdue(self):
        return self.returned_at is None and self.due_at < datetime.utcnow()

    @property
    def current_status(self):
        if self.returned_at is not None:
            return "RETURNED"
        if self.is_overdue:
            return "OVERDUE"
        return "CHECKED OUT"


class ExtensionRequest(db.Model):
    __tablename__ = "extension_requests"

    id = db.Column(db.Integer, primary_key=True)
    checkout_id = db.Column(db.Integer, db.ForeignKey("checkouts.id"), nullable=False)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    current_due_at = db.Column(db.DateTime, nullable=False)
    requested_due_at = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(32), default="PENDING", nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    decision_time = db.Column(db.DateTime, nullable=True)
    approving_pastor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    decision_notes = db.Column(db.Text, nullable=True)

    checkout = db.relationship("Checkout", back_populates="extension_requests")
    card = db.relationship("Card", back_populates="extension_requests")
    user = db.relationship("User", back_populates="extension_requests", foreign_keys=[user_id])
    approving_pastor = db.relationship("User", foreign_keys=[approving_pastor_id])


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=utc_now, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.String(120), nullable=False)
    object_type = db.Column(db.String(80), nullable=True)
    object_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)

    user = db.relationship("User", back_populates="audit_logs")


def ensure_default_cards():
    if Card.query.count() == 0:
        default_cards = [
            "Church Card 1",
            "Church Card 2",
            "Church Card 3",
            "Church Card 4",
        ]
        for name in default_cards:
            db.session.add(Card(name=name, active=True, description="Default church card"))
        db.session.commit()


def log_event(user, action, object_type=None, object_id=None, details=None):
    audit_entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=str(details) if details is not None else None,
    )
    db.session.add(audit_entry)
    db.session.commit()
