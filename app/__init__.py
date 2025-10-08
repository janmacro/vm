import os
from pathlib import Path
from typing import Any, Mapping

from flask import Flask, redirect, url_for


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
    from . import db
    db.init_app(app)

    # --- Blueprint registration ---
    from .routes import swimmers, optimize

    app.register_blueprint(swimmers.bp)
    app.register_blueprint(optimize.bp)

    @app.get("/")
    def index():
        return redirect(url_for("swimmers.index"))

    return app
