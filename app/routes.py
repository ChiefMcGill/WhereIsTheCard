import os
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.forms import LoginForm
from app.models import AppSetting, AuditLog, Card, Checkout, ExtensionRequest, User, local_now, log_event
from app.notifications.email import send_email


def _get_checkout_or_404(checkout_id):
    checkout = Checkout.query.get_or_404(checkout_id)
    return checkout


def _require_roles(*roles):
    def decorator(func):
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(403)
            if current_user.role not in roles:
                abort(403)
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


def _effective_role(role_name):
    if role_name == "SENIOR_PASTOR":
        return "ADMIN"
    return role_name


def _parse_requested_minutes(form):
    selected = form.get("duration_minutes")
    if selected:
        try:
            return max(15, int(selected))
        except ValueError:
            return None
    return int(os.getenv("CARD_CHECKOUT_MINUTES", "60"))


def _checkout_duration_options():
    raw_value = AppSetting.get("CHECKOUT_DURATION_OPTIONS", "30,60,120,240")
    values = []
    for part in str(raw_value).split(","):
        try:
            minutes = int(str(part).strip())
        except (TypeError, ValueError):
            continue
        if minutes > 0 and minutes not in values:
            values.append(minutes)
    if not values:
        return [30, 60, 120, 240]
    return values


def register_routes(app):
    @app.before_request
    def enforce_password_change():
        if current_user.is_authenticated and current_user.must_change_password and request.path not in {"/change-password", "/logout"}:
            return redirect(url_for("change_password"))

    @app.route("/")
    def dashboard():
        cards = Card.query.filter_by(active=True).order_by(Card.id).all()
        duration_options = _checkout_duration_options()
        card_history = {
            card.id: sorted(card.checkouts, key=lambda item: item.checked_out_at or datetime.min, reverse=True)[:5]
            for card in cards
        }
        return render_template("dashboard.html", cards=cards, current_user=current_user, duration_options=duration_options, card_history=card_history)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            if current_user.must_change_password:
                return redirect(url_for("change_password"))
            return redirect(url_for("checkout"))

        form = LoginForm()
        if form.validate_on_submit():
            email = form.email.data.strip().lower()
            user = User.query.filter_by(email=email).first()
            if user and user.active and user.check_password(form.password.data):
                login_user(user, remember=False)
                user.last_login = local_now()
                db.session.add(user)
                db.session.commit()
                log_event(user, "login", "user", user.id, "Successful login")
                if user.must_change_password:
                    flash("For security, please change your password before continuing.", "warning")
                    return redirect(url_for("change_password"))
                next_page = request.args.get("next")
                return redirect(next_page or url_for("checkout"))

            log_event(None, "login_failed", "user", None, f"Failed login attempt for {email}")
            flash("Invalid email or password.", "danger")

        return render_template("login.html", form=form)

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if len(new_password) < 6:
                flash("Password must be at least 6 characters.", "warning")
            elif new_password != confirm_password:
                flash("Passwords do not match.", "warning")
            elif current_user.must_change_password is False and not current_user.check_password(request.form.get("current_password") or ""):
                flash("Your current password is incorrect.", "danger")
            else:
                if current_user.must_change_password and new_password == request.form.get("current_password"):
                    flash("Please choose a new password different from your temporary password.", "warning")
                else:
                    current_user.set_password(new_password)
                    current_user.must_change_password = False
                    current_user.clear_password_reset_token()
                    db.session.add(current_user)
                    db.session.commit()
                    log_event(current_user, "password_changed", "user", current_user.id, "Password changed via change-password flow")
                    flash("Password updated successfully.", "success")
                    return redirect(url_for("checkout"))

        return render_template("change_password.html", must_change=current_user.must_change_password)

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if current_user.is_authenticated:
            return redirect(url_for("checkout"))

        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            user = User.query.filter_by(email=email).first()
            if user:
                token = user.generate_password_reset_token()
                db.session.add(user)
                db.session.commit()
                reset_url = url_for("reset_password", token=token, _external=True)
                body = (
                    f"Hi {user.name},\n\n"
                    "You requested a password reset for Where Is The Card.\n"
                    f"Use this link to reset your password: {reset_url}\n\n"
                    "If you did not request this, you can ignore this email."
                )
                send_email("Reset your Where Is The Card password", body, user.email, sender=AppSetting.get("MAIL_FROM") or None)
            flash("If an account exists for that email address, a password reset link has been sent.", "info")
            return redirect(url_for("login"))

        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        if current_user.is_authenticated:
            return redirect(url_for("checkout"))

        user = User.query.filter_by(password_reset_token=token).first()
        if user is None or user.password_reset_expires_at is None or user.password_reset_expires_at < local_now():
            flash("This password reset link is invalid or has expired.", "danger")
            return redirect(url_for("forgot_password"))

        if request.method == "POST":
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            if len(new_password) < 6:
                flash("Password must be at least 6 characters.", "warning")
            elif new_password != confirm_password:
                flash("Passwords do not match.", "warning")
            else:
                user.set_password(new_password)
                user.must_change_password = False
                user.clear_password_reset_token()
                db.session.add(user)
                db.session.commit()
                log_event(user, "password_reset", "user", user.id, "Password reset via email link")
                flash("Password reset successfully. You can now log in.", "success")
                return redirect(url_for("login"))

        return render_template("reset_password.html", token=token)

    @app.route("/logout")
    @login_required
    def logout():
        user = current_user
        log_event(user, "logout", "user", user.id, "User logged out")
        logout_user()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/checkout", methods=["GET", "POST"])
    @login_required
    def checkout():
        cards = Card.query.order_by(Card.id).all()
        current_checkout = current_user.current_checkout()
        confirm_return = request.form.get("confirm_return") == "yes"

        if request.method == "POST":
            action = request.form.get("action")

            if action == "checkout":
                card_id = request.form.get("card_id")
                card = Card.query.get(int(card_id))
                if not card or not card.active:
                    flash("This card is not available.", "warning")
                    return redirect(url_for("checkout"))
                if card.current_checkout is not None:
                    flash("That card is already checked out.", "warning")
                    return redirect(url_for("checkout"))

                purpose = (request.form.get("purpose") or "").strip()
                if not purpose:
                    flash("Please enter a purpose for the checkout.", "warning")
                    return redirect(url_for("checkout"))

                booking_note = (request.form.get("booking_note") or "").strip()
                is_indefinite = _effective_role(current_user.role) == "ADMIN" and request.form.get("indefinite_booking") == "on"
                if is_indefinite:
                    due_at = None
                    booking_note = booking_note or "Leadership booking - no expiry set."
                else:
                    duration_minutes = _parse_requested_minutes(request.form)
                    if duration_minutes is None:
                        duration_minutes = int(os.getenv("CARD_CHECKOUT_MINUTES", "60"))
                    due_at = local_now() + timedelta(minutes=duration_minutes)
                    booking_note = None if not booking_note else booking_note

                checkout = Checkout(
                    card_id=card.id,
                    user_id=current_user.id,
                    purpose=purpose,
                    checked_out_at=local_now(),
                    original_due_at=due_at,
                    due_at=due_at,
                    returned_at=None,
                    indefinite_booking=is_indefinite,
                    booking_note=booking_note,
                    overdue_notified=False,
                    overdue_reason=None,
                    is_active=True,
                )
                db.session.add(checkout)
                db.session.commit()
                log_event(current_user, "checkout", "checkout", checkout.id, f"Checked out {card.name} with purpose: {purpose}")
                flash(f"{card.name} checked out successfully.", "success")
                return redirect(url_for("checkout"))

            if action == "prepare_return":
                checkout_id = request.form.get("checkout_id")
                checkout = _get_checkout_or_404(checkout_id)
                if checkout.user_id != current_user.id:
                    flash("You cannot return another person's card.", "danger")
                    return redirect(url_for("checkout"))
                return render_template("checkout.html", cards=cards, current_user=current_user, checkout_to_confirm=checkout, confirm_return=True)

            if action == "return":
                checkout_id = request.form.get("checkout_id")
                checkout = _get_checkout_or_404(checkout_id)
                if checkout.user_id != current_user.id:
                    flash("You cannot return another person's card.", "danger")
                    return redirect(url_for("checkout"))

                checkout.returned_at = local_now()
                checkout.is_active = False
                db.session.add(checkout)
                db.session.commit()
                log_event(current_user, "return", "checkout", checkout.id, f"Returned {checkout.card.name}")
                flash("Card returned successfully.", "success")
                return redirect(url_for("checkout"))

            if action == "request_extension":
                if current_user.role == "USER":
                    flash("Please contact an Admin directly for an extension. They can log the approved extension on your behalf.", "info")
                    return redirect(url_for("checkout"))

                checkout_id = request.form.get("checkout_id")
                checkout = _get_checkout_or_404(checkout_id)
                if checkout.user_id != current_user.id:
                    flash("You cannot request an extension for another user's card.", "danger")
                    return redirect(url_for("checkout"))

                pending = ExtensionRequest.query.filter_by(checkout_id=checkout.id, status="PENDING").first()
                if pending:
                    flash("An extension request is already pending for this card.", "warning")
                    return redirect(url_for("checkout"))

                requested_due = request.form.get("requested_due_at")
                reason = (request.form.get("reason") or "").strip()
                if not requested_due or not reason:
                    flash("Please provide the requested return time and a reason.", "warning")
                    return redirect(url_for("checkout"))

                try:
                    requested_due_at = datetime.strptime(requested_due, "%Y-%m-%dT%H:%M")
                except ValueError:
                    flash("The requested return time was not valid.", "warning")
                    return redirect(url_for("checkout"))

                extension = ExtensionRequest(
                    checkout_id=checkout.id,
                    card_id=checkout.card_id,
                    user_id=current_user.id,
                    current_due_at=checkout.due_at,
                    requested_due_at=requested_due_at,
                    reason=reason,
                    status="PENDING",
                )
                db.session.add(extension)
                db.session.commit()
                log_event(current_user, "extension_request", "extension_request", extension.id, f"Requested extension for {checkout.card.name}")

                senior_pastor_email = AppSetting.get("SENIOR_PASTOR_EMAIL", "seniorpastor@solidground.co.za")
                subject = f"Extension request: {checkout.card.name}"
                body = (
                    f"A user has requested an extension for {checkout.card.name}.\n\n"
                    f"User:\n{current_user.name}\n\n"
                    f"Current due time:\n{checkout.due_at.strftime('%d %B %Y %H:%M')}\n\n"
                    f"Requested new due time:\n{requested_due_at.strftime('%d %B %Y %H:%M')}\n\n"
                    f"Reason:\n{reason}\n\n"
                    f"Approve: https://whereisthecard.solidground.co.za/extensions/{extension.id}/decision/approve\n"
                    f"Reject: https://whereisthecard.solidground.co.za/extensions/{extension.id}/decision/reject"
                )
                send_email(subject, body, senior_pastor_email)
                flash("Your extension request has been sent to the Senior Pastor.", "success")
                return redirect(url_for("checkout"))

        duration_options = _checkout_duration_options()
        return render_template("checkout.html", cards=cards, current_user=current_user, checkout_to_confirm=None, confirm_return=False, duration_options=duration_options)

    @app.route("/history")
    @login_required
    def history():
        checkouts = Checkout.query.filter_by(user_id=current_user.id).order_by(Checkout.checked_out_at.desc()).all()
        return render_template("history.html", checkouts=checkouts)

    @app.route("/extensions")
    @login_required
    def extensions():
        requests = ExtensionRequest.query.filter_by(user_id=current_user.id).order_by(ExtensionRequest.created_at.desc()).all()
        return render_template("extensions.html", requests=requests)

    @app.route("/extensions/<int:request_id>/decision/<decision>", methods=["POST", "GET"])
    @login_required
    def extension_decision(request_id, decision):
        if _effective_role(current_user.role) != "ADMIN":
            abort(403)

        extension = ExtensionRequest.query.get_or_404(request_id)
        if extension.status != "PENDING":
            flash("This extension request has already been resolved.", "info")
            return redirect(url_for("extensions"))

        if decision not in {"approve", "reject"}:
            abort(400)

        decision_note = request.form.get("decision_notes") or ""
        now_time = local_now()

        if decision == "approve":
            extension.status = "APPROVED"
            extension.decision_time = now_time
            extension.approving_pastor_id = current_user.id
            extension.decision_notes = decision_note
            extension.checkout.due_at = extension.requested_due_at
            db.session.add(extension)
            db.session.commit()
            log_event(current_user, "extension_approval", "extension_request", extension.id, f"Approved extension for {extension.card.name}")
            send_email(
                f"Extension approved: {extension.card.name}",
                (
                    "Your extension request has been approved.\n\n"
                    f"Card:\n{extension.card.name}\n\n"
                    f"New return time:\n{extension.requested_due_at.strftime('%d %B %Y %H:%M')}\n\n"
                    f"Approved by:\n{current_user.name}\n\n"
                    "Please return the card by the new due time."
                ),
                extension.user.email,
            )
            flash("Extension approved.", "success")
        else:
            extension.status = "REJECTED"
            extension.decision_time = now_time
            extension.approving_pastor_id = current_user.id
            extension.decision_notes = decision_note
            db.session.add(extension)
            db.session.commit()
            log_event(current_user, "extension_rejection", "extension_request", extension.id, f"Rejected extension for {extension.card.name}")
            send_email(
                f"Extension rejected: {extension.card.name}",
                (
                    "Your extension request has been rejected.\n\n"
                    f"Card:\n{extension.card.name}\n\n"
                    f"Original due time:\n{extension.current_due_at.strftime('%d %B %Y %H:%M')}\n\n"
                    f"Reason:\n{decision_note or 'No reason supplied.'}\n\n"
                    "The original due time remains unchanged."
                ),
                extension.user.email,
            )
            flash("Extension rejected.", "success")

        return redirect(url_for("extensions"))

    @app.route("/admin")
    @login_required
    def admin():
        if current_user.role != "ADMIN":
            abort(403)
        cards = Card.query.order_by(Card.id).all()
        users = User.query.order_by(User.name).all()
        checkouts = Checkout.query.order_by(Checkout.checked_out_at.desc()).all()
        return render_template("admin.html", cards=cards, users=users, checkouts=checkouts)

    @app.route("/admin/users", methods=["GET", "POST"])
    @login_required
    def admin_users():
        if current_user.role != "ADMIN":
            abort(403)

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            role = request.form.get("role", "USER")
            if name and email and password:
                if User.query.filter_by(email=email).first():
                    flash("A user with that email already exists.", "warning")
                else:
                    user = User(name=name, email=email, role=role, active=True, must_change_password=True)
                    user.set_password(password)
                    db.session.add(user)
                    db.session.commit()
                    log_event(current_user, "user_created", "user", user.id, f"Created user {user.name} with role {user.role}")
                    flash(f"User created with a temporary password. They will be prompted to change it on first login.", "success")
            else:
                flash("Please fill in all required user fields.", "warning")

        users = User.query.order_by(User.name).all()
        return render_template("admin_users.html", users=users)

    @app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
    @login_required
    def toggle_user(user_id):
        if current_user.role != "ADMIN":
            abort(403)
        user = User.query.get_or_404(user_id)
        user.active = not user.active
        db.session.add(user)
        db.session.commit()
        log_event(current_user, "user_status_changed", "user", user.id, f"Set active={user.active}")
        flash(f"User status updated for {user.name}.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
    @login_required
    def delete_user(user_id):
        if current_user.role != "ADMIN":
            abort(403)

        user = User.query.get_or_404(user_id)

        if user.id == current_user.id:
            flash("You cannot permanently delete your own account.", "warning")
            return redirect(url_for("admin_users"))

        if user.email == "server@solidground.co.za":
            flash("The default administrator account cannot be permanently deleted.", "warning")
            return redirect(url_for("admin_users"))

        if Checkout.query.filter_by(user_id=user.id, returned_at=None).first():
            flash("Cannot permanently delete a user with an active card checkout. Return the card first.", "warning")
            return redirect(url_for("admin_users"))

        ExtensionRequest.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        ExtensionRequest.query.filter_by(approving_pastor_id=user.id).update({"approving_pastor_id": None}, synchronize_session=False)
        Checkout.query.filter_by(user_id=user.id).delete(synchronize_session=False)
        AuditLog.query.filter_by(user_id=user.id).delete(synchronize_session=False)

        db.session.delete(user)
        db.session.commit()

        log_event(current_user, "user_deleted", "user", user.id, f"Permanently deleted user {user.name} ({user.email})")
        flash(f"User {user.name} was permanently deleted.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/users/<int:user_id>/role", methods=["POST"])
    @login_required
    def change_user_role(user_id):
        if current_user.role != "ADMIN":
            abort(403)
        user = User.query.get_or_404(user_id)
        new_role = request.form.get("role")
        if new_role in {"USER", "FINANCE", "ADMIN"}:
            user.role = new_role
            db.session.add(user)
            db.session.commit()
            if str(user.id) == session.get("_user_id"):
                session.clear()
                login_user(user, remember=False)
            log_event(current_user, "user_role_changed", "user", user.id, f"Changed role to {new_role}")
            flash(f"Role changed to {new_role}.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/cards", methods=["GET", "POST"])
    @login_required
    def admin_cards():
        if current_user.role != "ADMIN":
            abort(403)

        if request.method == "POST":
            action = request.form.get("action")
            if action == "create_card":
                name = (request.form.get("name") or "").strip()
                if not name:
                    flash("Card name is required.", "warning")
                    return redirect(url_for("admin_cards"))
                card = Card(
                    name=name,
                    description=(request.form.get("description") or "").strip(),
                    active=request.form.get("active") == "on",
                )
                db.session.add(card)
                db.session.commit()
                log_event(current_user, "card_created", "card", card.id, f"Created {card.name}")
                flash("Card added.", "success")
                return redirect(url_for("admin_cards"))

            if action == "update_card":
                card = Card.query.get_or_404(int(request.form.get("card_id")))
                card.name = (request.form.get("name") or card.name).strip() or card.name
                card.description = request.form.get("description") or ""
                card.active = request.form.get("active") == "on"
                db.session.add(card)
                db.session.commit()
                log_event(current_user, "card_updated", "card", card.id, f"Updated {card.name}")
                flash("Card updated.", "success")
                return redirect(url_for("admin_cards"))

            if action == "delete_card":
                card = Card.query.get_or_404(int(request.form.get("card_id")))
                if card.current_checkout is not None:
                    flash("Cannot delete a card that is currently checked out.", "warning")
                    return redirect(url_for("admin_cards"))
                ExtensionRequest.query.filter_by(card_id=card.id).delete(synchronize_session=False)
                Checkout.query.filter_by(card_id=card.id).delete(synchronize_session=False)
                db.session.delete(card)
                db.session.commit()
                log_event(current_user, "card_deleted", "card", card.id, f"Deleted {card.name}")
                flash("Card removed.", "success")
                return redirect(url_for("admin_cards"))

        cards = Card.query.order_by(Card.id).all()
        return render_template("admin_cards.html", cards=cards)

    @app.route("/admin/cards/<int:card_id>/delete", methods=["POST"])
    @login_required
    def delete_card(card_id):
        if current_user.role != "ADMIN":
            abort(403)

        card = Card.query.get_or_404(card_id)
        if card.current_checkout is not None:
            flash("Cannot delete a card that is currently checked out.", "warning")
            return redirect(url_for("admin_cards"))

        ExtensionRequest.query.filter_by(card_id=card.id).delete(synchronize_session=False)
        Checkout.query.filter_by(card_id=card.id).delete(synchronize_session=False)
        db.session.delete(card)
        db.session.commit()
        log_event(current_user, "card_deleted", "card", card.id, f"Deleted {card.name}")
        flash("Card removed.", "success")
        return redirect(url_for("admin_cards"))

    @app.route("/admin/settings", methods=["GET", "POST"])
    @login_required
    def admin_settings():
        if current_user.role != "ADMIN":
            abort(403)

        if request.method == "POST":
            settings = {
                "SMTP_HOST": request.form.get("SMTP_HOST", "").strip(),
                "SMTP_PORT": request.form.get("SMTP_PORT", "587").strip(),
                "SMTP_USERNAME": request.form.get("SMTP_USERNAME", "").strip(),
                "SMTP_PASSWORD": request.form.get("SMTP_PASSWORD", "").strip(),
                "SMTP_USE_TLS": "true" if request.form.get("SMTP_USE_TLS") == "true" else "false",
                "MAIL_FROM": request.form.get("MAIL_FROM", "").strip(),
                "FINANCE_EMAIL": request.form.get("FINANCE_EMAIL", "").strip(),
                "SENIOR_PASTOR_EMAIL": request.form.get("SENIOR_PASTOR_EMAIL", "").strip(),
                "TIMEZONE_OFFSET_HOURS": request.form.get("TIMEZONE_OFFSET_HOURS", "2").strip(),
                "CHECKOUT_DURATION_OPTIONS": request.form.get("CHECKOUT_DURATION_OPTIONS", "30,60,120,240").strip(),
                "SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES": request.form.get("SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES", "60").strip(),
            }
            for key, value in settings.items():
                AppSetting.set(key, value)
            flash("Email settings saved.", "success")

        values = {
            key: AppSetting.get(key, default)
            for key, default in {
                "SMTP_HOST": "",
                "SMTP_PORT": "587",
                "SMTP_USERNAME": "",
                "SMTP_PASSWORD": "",
                "SMTP_USE_TLS": "true",
                "MAIL_FROM": "no-reply@solidground.co.za",
                "FINANCE_EMAIL": "finance@solidground.co.za",
                "SENIOR_PASTOR_EMAIL": "seniorpastor@solidground.co.za",
                "TIMEZONE_OFFSET_HOURS": "2",
                "CHECKOUT_DURATION_OPTIONS": "30,60,120,240",
                "SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES": "60",
            }.items()
        }
        return render_template("admin_settings.html", settings=values)

    @app.route("/admin/settings/test-email", methods=["POST"])
    @login_required
    def admin_settings_test_email():
        if current_user.role != "ADMIN":
            abort(403)

        target_email = AppSetting.get("FINANCE_EMAIL") or current_user.email or AppSetting.get("MAIL_FROM")
        if not target_email:
            flash("Set a finance email or sender address before sending a test email.", "warning")
            return redirect(url_for("admin_settings"))

        subject = "Where Is The Card test email"
        body = (
            "This is a test email from the Where Is The Card system.\n\n"
            "If you received this, the email settings are working correctly."
        )
        sent = send_email(subject, body, target_email, sender=AppSetting.get("MAIL_FROM") or None)
        if sent:
            flash(f"Test email sent to {target_email}.", "success")
        else:
            flash("The test email could not be sent. Please check the SMTP settings and server connection.", "warning")
        return redirect(url_for("admin_settings"))

    @app.route("/admin/history")
    @login_required
    def admin_history():
        if current_user.role not in {"ADMIN", "FINANCE"}:
            abort(403)
        checkouts = Checkout.query.order_by(Checkout.checked_out_at.desc()).all()
        return render_template("admin_history.html", checkouts=checkouts)

    @app.route("/admin/audit")
    @login_required
    def admin_audit():
        if current_user.role != "ADMIN":
            abort(403)
        from app.models import AuditLog

        entries = AuditLog.query.order_by(AuditLog.timestamp.desc()).all()
        return render_template("admin_audit.html", entries=entries)

    @app.route("/admin/overdue", methods=["GET", "POST"])
    @login_required
    def admin_overdue():
        if current_user.role not in {"ADMIN", "FINANCE"}:
            abort(403)

        if request.method == "POST":
            checkout = Checkout.query.get_or_404(int(request.form.get("checkout_id")))
            checkout.overdue_reason = (request.form.get("overdue_reason") or "").strip() or checkout.overdue_reason
            db.session.add(checkout)
            db.session.commit()
            log_event(current_user, "overdue_reason_updated", "checkout", checkout.id, checkout.overdue_reason)
            flash("Overdue reason saved.", "success")

        overdue_checkouts = Checkout.query.filter(Checkout.returned_at.is_(None), Checkout.due_at < local_now()).order_by(Checkout.due_at.asc()).all()
        return render_template("admin_overdue.html", checkouts=overdue_checkouts)

    @app.route("/admin/close_checkout/<int:checkout_id>", methods=["POST"])
    @login_required
    def admin_close_checkout(checkout_id):
        if current_user.role not in {"ADMIN", "FINANCE"}:
            abort(403)
        checkout = Checkout.query.get_or_404(checkout_id)
        checkout.returned_at = local_now()
        checkout.is_active = False
        db.session.add(checkout)
        db.session.commit()
        log_event(current_user, "administrative_override", "checkout", checkout.id, f"Forced closure of checkout for {checkout.card.name}")
        flash("Checkout closed administratively.", "success")
        return redirect(url_for("admin_history"))

    @app.route("/admin/reset_password/<int:user_id>", methods=["POST"])
    @login_required
    def admin_reset_password(user_id):
        if current_user.role != "ADMIN":
            abort(403)
        user = User.query.get_or_404(user_id)
        password = request.form.get("password") or ""
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "warning")
            return redirect(url_for("admin_users"))
        user.set_password(password)
        user.must_change_password = True
        db.session.add(user)
        db.session.commit()
        log_event(current_user, "password_reset", "user", user.id, f"Password reset for {user.name}")
        flash("Password updated.", "success")
        return redirect(url_for("admin_users"))

    @app.route("/admin/finance_reason/<int:checkout_id>", methods=["POST"])
    @login_required
    def finance_reason(checkout_id):
        if current_user.role not in {"ADMIN", "FINANCE"}:
            abort(403)
        checkout = Checkout.query.get_or_404(checkout_id)
        checkout.overdue_reason = (request.form.get("overdue_reason") or "").strip()
        db.session.add(checkout)
        db.session.commit()
        log_event(current_user, "overdue_reason_added", "checkout", checkout.id, checkout.overdue_reason)
        flash("Reason saved to finance notes.", "success")
        return redirect(url_for("admin_overdue"))
