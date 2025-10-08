"""Blueprint for running and viewing lineup optimizations."""
from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("optimize", __name__, url_prefix="/optimize")


@bp.get("/")
def index():
    """Placeholder optimizer dashboard until wiring is complete."""
    return render_template("optimize/index.html")
