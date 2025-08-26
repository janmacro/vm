import os
from pathlib import Path
from typing import Any, Mapping

from flask import Flask


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
    # The db module will provide: init_app(app), and a CLI command to init schema.
    from . import db
    db.init_app(app)

    # --- Blueprint registration (files we’ll add next) ---
    # Keep imports inside the factory to avoid circular imports during tests.
    # from .routes import swimmers
    # from .routes import pbs
    # from .routes import optimize
    # from .routes import importer

    # app.register_blueprint(swimmers.bp, url_prefix="/swimmers")
    # app.register_blueprint(pbs.bp, url_prefix="/pbs")
    # app.register_blueprint(optimize.bp, url_prefix="/optimize")
    # app.register_blueprint(importer.bp, url_prefix="/import")

    # # Root route convenience: send users to swimmers list first
    # @app.get("/")
    # def index():
    #     from flask import redirect, url_for
    #     return redirect(url_for("swimmers.index"))

    return app
