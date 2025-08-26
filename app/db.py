from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine
import sqlite3

db = SQLAlchemy()


def init_app(app: Flask) -> None:
    """Bind SQLAlchemy to the app and register a small CLI command."""
    db.init_app(app)

    # Enable SQLite foreign keys (needed for ON DELETE CASCADE)
    @event.listens_for(Engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        if isinstance(dbapi_connection, sqlite3.Connection):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.close()

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Create all tables (idempotent)"""
        # Import models so metadata is populated
        from . import models

        with app.app_context():
            db.create_all()
        print("Initialized the database")