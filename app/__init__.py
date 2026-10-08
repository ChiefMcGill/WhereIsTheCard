from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Config
from app.extensions import csrf, db, login_manager, migrate
from app.models import Card, User, ensure_default_cards


def ensure_default_admin(app):
    if User.query.count() > 0:
        return

    admin_name = app.config.get("INITIAL_ADMIN_NAME", "Church Admin")
    admin_email = (app.config.get("INITIAL_ADMIN_EMAIL", "admin@solidground.co.za") or "admin@solidground.co.za").strip().lower()
    admin_password = app.config.get("INITIAL_ADMIN_PASSWORD", "ChangeMeNow123!")

    admin = User(name=admin_name, email=admin_email, role="ADMIN", active=True)
    admin.set_password(admin_password)
    db.session.add(admin)
    db.session.commit()
    app.logger.warning("Initial admin created with email %s. Change the password in the web UI after logging in.", admin_email)


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
