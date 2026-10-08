from flask import Flask
from sqlalchemy import inspect, text
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, login_manager, migrate
from app.models import Card, User, ensure_default_cards


def ensure_user_schema():
    inspector = inspect(db.engine)
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "must_change_password" not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN NOT NULL DEFAULT 0"))
        db.session.commit()


def ensure_default_admin(app):
    admin_name = app.config.get("INITIAL_ADMIN_NAME", "Church Admin")
    admin_email = (app.config.get("INITIAL_ADMIN_EMAIL", "production@solidground.co.za") or "production@solidground.co.za").strip().lower()
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
        ensure_default_cards()
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
