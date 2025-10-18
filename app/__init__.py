import os
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, redirect, url_for
from flask_login import LoginManager


def create_app(config: Mapping[str, Any] | None = None) -> Flask:
    """Flask application factory"""
    app = Flask(__name__, instance_relative_config=True)

    # --- Default config ---
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "SQLALCHEMY_DATABASE_URI",
            f"sqlite:///{Path(app.instance_path) / 'app.db'}",
        ),
    )

    # Override via env file or explicit mapping passed to create_app
    if config:
        app.config.from_mapping(config)

    # Ensure the instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    # --- Database setup ---
    from . import db  # module containing init_app
    from .db import db as sqldb  # SQLAlchemy instance
    db.init_app(app)

    # --- Authentication setup ---
    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.init_app(app)

    from .models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        # Let DB errors surface during development to aid debugging.
        return sqldb.session.get(User, int(user_id))

    # --- Blueprint registration ---
    from .routes import swimmers, optimize, auth

    app.register_blueprint(swimmers.bp)
    app.register_blueprint(optimize.bp)
    app.register_blueprint(auth.bp)

    @app.get("/")
    def index():
        return redirect(url_for("swimmers.index"))

    return app
