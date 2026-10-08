import html
from datetime import datetime, timedelta

import pytest

from app import create_app, ensure_default_admin
from app.extensions import db
from app.models import AppSetting, Card, Checkout, ExtensionRequest, User, ensure_default_cards, log_event
from app.tasks.overdue import check_overdue_checkouts


def build_app():
    app = create_app({
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,
        "SQLALCHEMY_DATABASE_URI": "sqlite://",
        "SERVER_NAME": "localhost",
    })
    with app.app_context():
        db.drop_all()
        db.create_all()
        ensure_default_cards()
        ensure_default_admin(app)
    return app


def create_user(email, name="Test User", role="USER", password="secret123"):
    user = User(name=name, email=email, role=role, active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return user


def test_user_can_log_in_and_invalid_password_fails():
    app = build_app()
    with app.app_context():
        create_user("john@church.org", "John", "USER", "abc123")

    with app.test_client() as client:
        response = client.post("/login", data={"email": "john@church.org", "password": "abc123"}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Checkout and return" in response.data or b"Where Is The Card" in response.data

    with app.test_client() as client:
        bad = client.post("/login", data={"email": "john@church.org", "password": "wrong"}, follow_redirects=True)
        assert bad.status_code == 200
        assert b"Invalid email or password" in bad.data


def test_initial_admin_must_change_password_on_first_login():
    app = build_app()
    with app.app_context():
        admin = User.query.filter_by(email="server@solidground.co.za").first()
        assert admin is not None
        assert admin.check_password("CardAdmin123")
        assert admin.must_change_password is True

    with app.test_client() as client:
        response = client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        assert response.status_code == 200
        assert b"change your password" in response.data.lower()


def test_checkout_form_has_no_custom_duration_and_default_admin_email_is_server():
    app = build_app()
    with app.app_context():
        admin = User.query.filter_by(email="server@solidground.co.za").first()
        assert admin is not None

    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        client.post("/change-password", data={"new_password": "NewAdminPass123", "confirm_password": "NewAdminPass123"}, follow_redirects=True)
        page = client.get("/checkout")
        assert page.status_code == 200
        assert b"Custom duration" not in page.data
        assert b"30 minutes" in page.data


def test_overdue_worker_sends_a_polite_reminder_before_due_and_requires_senior_pastor_threshold():
    app = build_app()
    with app.app_context():
        user = create_user("rachel@church.org", "Rachel", "USER", "pw")
        reminder_card = Card(name="Reminder Card", active=True)
        overdue_card = Card(name="Overdue Card", active=True)
        db.session.add_all([reminder_card, overdue_card])
        db.session.commit()

        AppSetting.set("FINANCE_EMAIL", "finance@solidground.co.za")
        AppSetting.set("SENIOR_PASTOR_EMAIL", "seniorpastor@solidground.co.za")
        AppSetting.set("SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES", "45")

        reminder_checkout = Checkout(
            card_id=reminder_card.id,
            user_id=user.id,
            purpose="Church event",
            checked_out_at=datetime.utcnow() - timedelta(minutes=55),
            original_due_at=datetime.utcnow() + timedelta(minutes=4),
            due_at=datetime.utcnow() + timedelta(minutes=4),
            is_active=True,
            reminder_sent=False,
            overdue_notified=False,
            senior_pastor_notified=False,
        )
        db.session.add(reminder_checkout)

        overdue_checkout = Checkout(
            card_id=overdue_card.id,
            user_id=user.id,
            purpose="Church event",
            checked_out_at=datetime.utcnow() - timedelta(hours=2),
            original_due_at=datetime.utcnow() - timedelta(minutes=90),
            due_at=datetime.utcnow() - timedelta(minutes=90),
            is_active=True,
            reminder_sent=False,
            overdue_notified=False,
            senior_pastor_notified=False,
        )
        db.session.add(overdue_checkout)
        db.session.commit()

    called = []

    def fake_send_email(subject, body, recipient, **kwargs):
        called.append((subject, recipient, body))
        return True

    from app import tasks as overdue_tasks
    original = overdue_tasks.overdue.send_email
    overdue_tasks.overdue.send_email = fake_send_email
    try:
        with app.app_context():
            check_overdue_checkouts()
    finally:
        overdue_tasks.overdue.send_email = original

    assert any("Reminder" in subject for subject, _, _ in called)
    assert any("OVERDUE" in subject for subject, _, _ in called)
    assert any("Senior pastor alert" in subject for subject, _, _ in called)


def test_admin_can_create_other_users_and_admins_with_default_passwords():
    app = build_app()

    with app.test_client() as client:
        login = client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        assert login.status_code == 200
        assert b"change your password" in login.data.lower()

        client.post(
            "/change-password",
            data={
                "new_password": "NewAdminPass123",
                "confirm_password": "NewAdminPass123",
            },
            follow_redirects=True,
        )

        response = client.post(
            "/admin/users",
            data={
                "name": "Assistant Admin",
                "email": "assistant.admin@church.org",
                "password": "TempPass123",
                "role": "ADMIN",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"user created with a temporary password" in response.data.lower()

        user = User.query.filter_by(email="assistant.admin@church.org").first()
        assert user is not None
        assert user.role == "ADMIN"
        assert user.must_change_password is True
        assert user.check_password("TempPass123")

        client.get("/logout")
        login = client.post("/login", data={"email": "assistant.admin@church.org", "password": "TempPass123"}, follow_redirects=True)
        assert login.status_code == 200
        assert b"change your password" in login.data.lower()

    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "NewAdminPass123"}, follow_redirects=True)
        client.post(
            "/admin/users",
            data={
                "name": "Volunteer User",
                "email": "volunteer@church.org",
                "password": "DefaultVol123",
                "role": "USER",
            },
            follow_redirects=True,
        )
        user = User.query.filter_by(email="volunteer@church.org").first()
        assert user is not None
        assert user.role == "USER"
        assert user.must_change_password is True
        assert user.check_password("DefaultVol123")


def test_admin_can_configure_duration_options_and_user_sees_them():
    app = build_app()
    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        client.post(
            "/change-password",
            data={
                "new_password": "NewAdminPass123",
                "confirm_password": "NewAdminPass123",
            },
            follow_redirects=True,
        )
        response = client.post(
            "/admin/settings",
            data={
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "mailer@example.com",
                "SMTP_PASSWORD": "secret-password",
                "SMTP_USE_TLS": "true",
                "MAIL_FROM": "no-reply@solidground.co.za",
                "FINANCE_EMAIL": "finance@solidground.co.za",
                "SENIOR_PASTOR_EMAIL": "seniorpastor@solidground.co.za",
                "CHECKOUT_DURATION_OPTIONS": "15,45,90,180",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert AppSetting.get("CHECKOUT_DURATION_OPTIONS") == "15,45,90,180"

        client.get("/logout")
        user = create_user("checkoutuser@church.org", "Checkout User", "USER", "secret123")
        login = client.post("/login", data={"email": user.email, "password": "secret123"}, follow_redirects=True)
        assert login.status_code == 200
        page = client.get("/checkout")
        assert b"15 minutes" in page.data
        assert b"45 minutes" in page.data
        assert b"30 minutes" not in page.data


def test_admin_can_book_a_card_indefinitely_with_a_visible_note():
    app = build_app()
    with app.app_context():
        card = Card.query.first()
        assert card is not None

    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        client.post(
            "/change-password",
            data={
                "new_password": "NewAdminPass123",
                "confirm_password": "NewAdminPass123",
            },
            follow_redirects=True,
        )
        response = client.post(
            "/checkout",
            data={
                "action": "checkout",
                "card_id": "1",
                "purpose": "Church leadership use",
                "indefinite_booking": "on",
                "booking_note": "Awaiting final approval from the board.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        with app.app_context():
            checkout = Checkout.query.filter_by(card_id=1).first()
            assert checkout is not None
            assert checkout.indefinite_booking is True
            assert checkout.booking_note == "Awaiting final approval from the board."
            assert checkout.due_at is None


def test_dashboard_available_cards_expand_for_logged_in_users():
    app = build_app()
    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        client.post(
            "/change-password",
            data={
                "new_password": "NewAdminPass123",
                "confirm_password": "NewAdminPass123",
            },
            follow_redirects=True,
        )
        page = client.get("/")
        assert page.status_code == 200
        assert b"<details" in page.data.lower()
        assert b"book out" in page.data.lower()


def test_timezone_offset_defaults_to_two_hours():
    app = build_app()
    with app.app_context():
        assert AppSetting.get_int("TIMEZONE_OFFSET_HOURS", 2) == 2
        assert app.config["TIMEZONE_OFFSET_HOURS"] == 2


def test_admin_nav_and_email_settings_are_available_in_ui():
    app = build_app()

    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        client.post(
            "/change-password",
            data={
                "new_password": "NewAdminPass123",
                "confirm_password": "NewAdminPass123",
            },
            follow_redirects=True,
        )

        home = client.get("/", follow_redirects=True)
        assert home.status_code == 200
        assert b"/admin" in home.data.lower()

        response = client.post(
            "/admin/settings",
            data={
                "SMTP_HOST": "smtp.example.com",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "mailer@example.com",
                "SMTP_PASSWORD": "secret-password",
                "SMTP_USE_TLS": "true",
                "MAIL_FROM": "no-reply@solidground.co.za",
                "FINANCE_EMAIL": "finance@solidground.co.za",
                "SENIOR_PASTOR_EMAIL": "seniorpastor@solidground.co.za",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"email settings saved" in response.data.lower()


def test_available_card_can_be_checked_out_and_cannot_be_checked_out_twice():
    app = build_app()
    with app.app_context():
        create_user("jane@church.org", "Jane", "USER", "secret")

    with app.test_client() as client:
        client.post("/login", data={"email": "jane@church.org", "password": "secret"}, follow_redirects=True)
        card_id = Card.query.first().id
        response = client.post("/checkout", data={"action": "checkout", "card_id": str(card_id), "purpose": "School run", "duration_minutes": "60"}, follow_redirects=True)
        assert response.status_code == 200
        assert b"checked out successfully" in response.data.lower()

        second = client.post("/checkout", data={"action": "checkout", "card_id": str(card_id), "purpose": "Second attempt", "duration_minutes": "60"}, follow_redirects=True)
        assert second.status_code == 200
        assert b"already checked out" in second.data.lower()


def test_user_can_return_their_own_card_and_cannot_return_another_users_card():
    app = build_app()
    with app.app_context():
        john = create_user("john2@church.org", "John", "USER", "pw")
        sarah = create_user("sarah2@church.org", "Sarah", "USER", "pw")
        card = Card.query.get(2)
        checkout = Checkout(
            card_id=card.id,
            user_id=john.id,
            purpose="Supplies",
            checked_out_at=datetime.utcnow(),
            original_due_at=datetime.utcnow() + timedelta(minutes=60),
            due_at=datetime.utcnow() + timedelta(minutes=60),
            is_active=True,
        )
        db.session.add(checkout)
        db.session.commit()

    with app.test_client() as client:
        client.post("/login", data={"email": "john2@church.org", "password": "pw"}, follow_redirects=True)
        checkout_id = Checkout.query.filter_by(user_id=User.query.filter_by(email="john2@church.org").first().id).first().id
        response = client.post("/checkout", data={"action": "prepare_return", "checkout_id": str(checkout_id)}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Return card" in response.data

        client.get("/logout")
        client.post("/login", data={"email": "sarah2@church.org", "password": "pw"}, follow_redirects=True)
        other = client.post("/checkout", data={"action": "return", "checkout_id": str(checkout_id)}, follow_redirects=True)
        assert other.status_code == 200
        page_text = html.unescape(other.data.decode("utf-8").lower())
        assert "cannot return another person's card" in page_text


def test_standard_user_cannot_request_extension_and_must_contact_admin_or_pastor():
    app = build_app()
    with app.app_context():
        user = create_user("mary@church.org", "Mary", "USER", "pw")
        card = Card.query.first()
        checkout = Checkout(
            card_id=card.id,
            user_id=user.id,
            purpose="General use",
            checked_out_at=datetime.utcnow() - timedelta(minutes=30),
            original_due_at=datetime.utcnow() + timedelta(minutes=30),
            due_at=datetime.utcnow() + timedelta(minutes=30),
            is_active=True,
        )
        db.session.add(checkout)
        db.session.commit()

    with app.test_client() as client:
        client.post("/login", data={"email": "mary@church.org", "password": "pw"}, follow_redirects=True)
        checkout_id = Checkout.query.filter_by(user_id=User.query.filter_by(email="mary@church.org").first().id).first().id
        response = client.post(
            "/checkout",
            data={
                "action": "request_extension",
                "checkout_id": str(checkout_id),
                "requested_due_at": (datetime.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "reason": "Need more time.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"contact an admin or senior pastor" in response.data.lower()
        assert ExtensionRequest.query.count() == 0


def test_extension_rules_and_approval():
    app = build_app()
    with app.app_context():
        senior = create_user("pastor@church.org", "Senior Pastor", "SENIOR_PASTOR", "pw")
        card = Card.query.first()
        checkout = Checkout(
            card_id=card.id,
            user_id=senior.id,
            purpose="General use",
            checked_out_at=datetime.utcnow() - timedelta(minutes=30),
            original_due_at=datetime.utcnow() + timedelta(minutes=30),
            due_at=datetime.utcnow() + timedelta(minutes=30),
            is_active=True,
        )
        db.session.add(checkout)
        db.session.commit()

    with app.test_client() as client:
        client.post("/login", data={"email": "pastor@church.org", "password": "pw"}, follow_redirects=True)
        checkout_id = Checkout.query.filter_by(user_id=User.query.filter_by(email="pastor@church.org").first().id).first().id
        response = client.post(
            "/checkout",
            data={
                "action": "request_extension",
                "checkout_id": str(checkout_id),
                "requested_due_at": (datetime.utcnow() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "reason": "Need more time.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"extension request has been sent" in response.data.lower()

        request = ExtensionRequest.query.first()
        approved = client.get(f"/extensions/{request.id}/decision/approve", follow_redirects=True)
        assert approved.status_code == 200
        request = ExtensionRequest.query.get(request.id)
        assert request.status == "APPROVED"


def test_overdue_detection_and_reason():
    app = build_app()
    with app.app_context():
        finance = create_user("finance@church.org", "Finance", "FINANCE", "pw")
        user = create_user("anna@church.org", "Anna", "USER", "pw")
        card = Card.query.get(3)
        checkout = Checkout(
            card_id=card.id,
            user_id=user.id,
            purpose="Audit needs",
            checked_out_at=datetime.utcnow() - timedelta(hours=2),
            original_due_at=datetime.utcnow() - timedelta(minutes=10),
            due_at=datetime.utcnow() - timedelta(minutes=10),
            is_active=True,
            overdue_notified=False,
        )
        db.session.add(checkout)
        db.session.commit()

    with app.test_client() as client:
        client.post("/login", data={"email": "finance@church.org", "password": "pw"}, follow_redirects=True)
        response = client.post("/admin/overdue", data={"checkout_id": "1", "overdue_reason": "Supplier delayed payment; pastor aware."}, follow_redirects=True)
        assert response.status_code == 200
        assert b"Supplier delayed payment" in response.data


def test_send_email_logs_smtp_failures_with_context(caplog, monkeypatch):
    app = build_app()
    with app.app_context():
        AppSetting.set("SMTP_HOST", "smtp.example.com")
        AppSetting.set("SMTP_PORT", "587")
        AppSetting.set("SMTP_USERNAME", "mailer@example.com")
        AppSetting.set("SMTP_USE_TLS", "true")
        AppSetting.set("MAIL_FROM", "no-reply@solidground.co.za")

        def raise_connection_error(*args, **kwargs):
            raise OSError("connection refused")

        import app.notifications.email as email_module

        monkeypatch.setattr(email_module.smtplib, "SMTP", raise_connection_error)
        with caplog.at_level("ERROR"):
            result = email_module.send_email("Test", "Body", "user@example.com")

        assert result is False
        assert "SMTP send failed" in caplog.text
        assert "smtp.example.com" in caplog.text
        assert "user@example.com" in caplog.text


def test_admin_can_permanently_delete_a_user():
    app = build_app()
    with app.app_context():
        create_user("remove@church.org", "Remove Me", "USER", "pw")

    with app.test_client() as client:
        client.post("/login", data={"email": "server@solidground.co.za", "password": "CardAdmin123"}, follow_redirects=True)
        client.post(
            "/change-password",
            data={
                "new_password": "NewAdminPass123",
                "confirm_password": "NewAdminPass123",
            },
            follow_redirects=True,
        )

        response = client.post("/admin/users/2/delete", follow_redirects=True)
        assert response.status_code == 200
        assert b"permanently deleted" in response.data.lower()
        assert User.query.filter_by(email="remove@church.org").first() is None


def test_admin_access_is_restricted_to_admins():
    app = build_app()
    with app.app_context():
        create_user("normal@church.org", "Normal User", "USER", "pw")

    with app.test_client() as client:
        client.post("/login", data={"email": "normal@church.org", "password": "pw"}, follow_redirects=True)
        response = client.get("/admin")
        assert response.status_code == 403
