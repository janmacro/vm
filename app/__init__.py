import os
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    """Flask application factory"""
    app = Flask(__name__, instance_relative_config=True)

    # --- Default config ---
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "SQLALCHEMY_DATABASE_URI",
            f"sqlite:///{Path(app.instance_path) / 'app.db'}",
        ),
    )

    # Override via env file or explicit mapping passed to create_app
    if config:
        app.config.from_mapping(config)

    # Production safety guard: require a strong SECRET_KEY in non-debug
    if not app.debug:
        secret = app.config.get("SECRET_KEY")
        if not secret or secret == "dev-secret":
            raise RuntimeError("SECRET_KEY must be set to a strong value in production.")
        # Harden session cookies in production
        app.config.setdefault("SESSION_COOKIE_SECURE", True)
        app.config.setdefault("SESSION_COOKIE_HTTPONLY", True)
        app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")

    # Ensure the instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    # --- Database setup ---
    from . import db  # module containing init_app
    from .db import db as sqldb  # SQLAlchemy instance
    db.init_app(app)

    # --- CSRF protection ---
    csrf = CSRFProtect()
    csrf.init_app(app)

    # --- Rate limiting ---
    from .limiter import limiter
    limiter.init_app(app)

    # --- Authentication setup ---
    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            return sqldb.session.get(User, int(user_id))
        except Exception:
            return None

    @app.context_processor
    def inject_csrf_token():
        # Expose a callable csrf_token() for templates
        return {"csrf_token": generate_csrf}

    # --- Blueprint registration ---
    from .routes import swimmers, optimize, auth

    app.register_blueprint(swimmers.bp)
    app.register_blueprint(optimize.bp)
    app.register_blueprint(auth.bp)

    @app.get("/")
    def index():
        return redirect(url_for("swimmers.index"))

    return app
