from flask import Flask
from sqlalchemy import inspect, text
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, login_manager, migrate
from app.models import AppSetting, Card, User, ensure_default_cards


def ensure_user_schema():
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "must_change_password" not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0"))
    if "password_reset_token" not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN password_reset_token VARCHAR(255)"))
    if "password_reset_expires_at" not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN password_reset_expires_at DATETIME"))
    db.session.commit()


def _rebuild_sqlite_table_with_nullable_column(table_name, column_name):
    if db.engine.dialect.name != "sqlite":
        return

    current_columns = inspect(db.engine).get_columns(table_name)
    if not current_columns:
        return

    legacy_table = f"{table_name}_legacy"
    info_rows = db.session.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    if not info_rows:
        return

    column_names = [row[1] for row in info_rows]
    if column_name not in column_names:
        return

    column_spec = []
    for row in info_rows:
        name = row[1]
        data_type = row[2] or "TEXT"
        is_pk = bool(row[5])
        not_null = bool(row[3])
        spec = f'"{name}" {data_type}'
        if is_pk:
            spec += " PRIMARY KEY"
        elif name != column_name and not_null:
            spec += " NOT NULL"
        column_spec.append(spec)

    db.session.execute(text(f"ALTER TABLE {table_name} RENAME TO {legacy_table}"))
    db.session.execute(text(f"CREATE TABLE {table_name} ({', '.join(column_spec)})"))
    db.session.execute(text(f"INSERT INTO {table_name} ({', '.join(f'"{name}"' for name in column_names)}) SELECT {', '.join(f'"{name}"' for name in column_names)} FROM {legacy_table}"))
    db.session.execute(text(f"DROP TABLE {legacy_table}"))
    db.session.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS ix_active_checkout_per_card ON {table_name} (card_id) WHERE returned_at IS NULL"))
    db.session.commit()


def ensure_checkout_schema():
    inspector = inspect(db.engine)
    columns = {column["name"]: column for column in inspector.get_columns("checkouts")}
    if "reminder_sent" not in columns:
        db.session.execute(text("ALTER TABLE checkouts ADD COLUMN reminder_sent BOOLEAN NOT NULL DEFAULT 0"))
    if "senior_pastor_notified" not in columns:
        db.session.execute(text("ALTER TABLE checkouts ADD COLUMN senior_pastor_notified BOOLEAN NOT NULL DEFAULT 0"))
    if "indefinite_booking" not in columns:
        db.session.execute(text("ALTER TABLE checkouts ADD COLUMN indefinite_booking BOOLEAN NOT NULL DEFAULT 0"))
    if "booking_note" not in columns:
        db.session.execute(text("ALTER TABLE checkouts ADD COLUMN booking_note TEXT"))

    for column_name in ["original_due_at", "due_at"]:
        column = columns.get(column_name)
        if column is not None and column.get("nullable") is False:
            _rebuild_sqlite_table_with_nullable_column("checkouts", column_name)
            columns = {column["name"]: column for column in inspector.get_columns("checkouts")}

    db.session.commit()


def ensure_default_settings(app):
    defaults = {
        "SMTP_HOST": app.config.get("EMAIL_HOST") or "",
        "SMTP_PORT": str(app.config.get("EMAIL_PORT", "587")),
        "SMTP_USERNAME": app.config.get("EMAIL_USERNAME") or "",
        "SMTP_PASSWORD": app.config.get("EMAIL_PASSWORD") or "",
        "SMTP_USE_TLS": "true" if app.config.get("EMAIL_USE_TLS", True) else "false",
        "MAIL_FROM": app.config.get("MAIL_FROM", "no-reply@solidground.co.za"),
        "FINANCE_EMAIL": app.config.get("FINANCE_EMAIL", "finance@solidground.co.za"),
        "SENIOR_PASTOR_EMAIL": app.config.get("SENIOR_PASTOR_EMAIL", "seniorpastor@solidground.co.za"),
        "TIMEZONE_OFFSET_HOURS": str(app.config.get("TIMEZONE_OFFSET_HOURS", 2)),
        "CHECKOUT_DURATION_OPTIONS": "30,60,120,240",
        "SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES": str(app.config.get("SENIOR_PASTOR_NOTIFICATION_THRESHOLD_MINUTES", 60)),
    }
    for key, value in defaults.items():
        if AppSetting.get(key) is None and value not in (None, ""):
            AppSetting.set(key, value)


def ensure_default_admin(app):
    admin_name = app.config.get("INITIAL_ADMIN_NAME", "Church Admin")
    admin_email = (app.config.get("INITIAL_ADMIN_EMAIL", "server@solidground.co.za") or "server@solidground.co.za").strip().lower()
    admin_password = app.config.get("INITIAL_ADMIN_PASSWORD", "CardAdmin123")

    admin = User.query.filter_by(email=admin_email).first()
    if admin is None:
        admin = User.query.filter_by(role="ADMIN").order_by(User.id.asc()).first()

    if admin is None:
        admin = User(name=admin_name, email=admin_email, role="ADMIN", active=True, must_change_password=True)
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()
        app.logger.warning("Initial admin created with email %s. Please change your password on first login.", admin_email)
        return

    admin.name = admin_name
    admin.email = admin_email
    admin.role = "ADMIN"
    admin.active = True
    if not admin.password_hash:
        admin.set_password(admin_password)
        admin.must_change_password = True

    if admin.email == admin_email and admin.role == "ADMIN" and admin.last_login is None and admin.check_password(admin_password):
        admin.must_change_password = True

    db.session.add(admin)
    db.session.commit()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(Config)

    if test_config:
        app.config.update(test_config)

    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    migrate.init_app(app, db)

    login_manager.login_view = "login"
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    from app.routes import register_routes

    register_routes(app)
    register_error_handlers(app)

    with app.app_context():
        db.create_all()
        ensure_user_schema()
        ensure_checkout_schema()
        ensure_default_cards()
        ensure_default_settings(app)
        ensure_default_admin(app)

    return app


def register_error_handlers(app):
    @app.errorhandler(404)
    def page_not_found(error):
        return "Page not found", 404

    @app.errorhandler(403)
    def forbidden(error):
        return "You do not have permission to do that.", 403

    @app.errorhandler(500)
    def internal_server_error(error):
        return "Something went wrong. Please try again later.", 500
