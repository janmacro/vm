"""Authentication blueprint: register, login, logout."""
from __future__ import annotations

from typing import List

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..db import db
from ..models import User


bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("swimmers.index"))

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    errors: List[str] = []

    if request.method == "POST":
        if not email:
            errors.append("Email is required.")
        if not password:
            errors.append("Password is required.")
        if password and len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if not errors:
            existing = db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()
            if existing:
                errors.append("An account with this email already exists.")
            else:
                user = User(email=email, password_hash=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                login_user(user)
                return redirect(url_for("swimmers.index"))

    return render_template("auth/register.html", email=email, errors=errors)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("swimmers.index"))

    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    errors: List[str] = []

    if request.method == "POST":
        if not email or not password:
            errors.append("Email and password are required.")
        else:
            user = db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()
            if not user or not check_password_hash(user.password_hash, password):
                errors.append("Invalid email or password.")
            else:
                login_user(user)
                next_url = request.args.get("next")
                return redirect(next_url or url_for("swimmers.index"))

    return render_template("auth/login.html", email=email, errors=errors)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))

