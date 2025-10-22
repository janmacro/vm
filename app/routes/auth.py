"""Authentication blueprint: register, login, logout."""
from __future__ import annotations

from typing import List

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from sqlalchemy import select
from ..limiter import limiter

from ..db import db
from ..models import User


bp = Blueprint("auth", __name__, url_prefix="")


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("50/day;10/hour")  # throttle account creation by IP
def register():
    if current_user.is_authenticated:
        return redirect(url_for("swimmers.index"))

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")
    errors: List[str] = []

    if request.method == "POST":
        if not username:
            errors.append("Username is required.")
        if not password:
            errors.append("Password is required.")
        if password and len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if not errors:
            existing = db.session.execute(select(User).filter_by(username=username)).scalar_one_or_none()
            if existing:
                errors.append("An account with this username already exists.")
            else:
                user = User(username=username, password_hash=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                login_user(user)
                return redirect(url_for("swimmers.index"))

    return render_template("auth/register.html", username=username, errors=errors)


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("50/hour;10/minute")  # throttle login attempts by IP
def login():
    if current_user.is_authenticated:
        return redirect(url_for("swimmers.index"))

    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    errors: List[str] = []

    if request.method == "POST":
        if not username or not password:
            errors.append("Username and password are required.")
        else:
            user = db.session.execute(select(User).filter_by(username=username)).scalar_one_or_none()
            if not user or not check_password_hash(user.password_hash, password):
                errors.append("Invalid email or password.")
            else:
                login_user(user)
                next_url = request.args.get("next")
                return redirect(next_url or url_for("swimmers.index"))

    return render_template("auth/login.html", username=username, errors=errors)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET"])
@login_required
def account():
    return render_template("auth/account.html", errors=[], messages=[])


@bp.post("/account/password")
@login_required
@limiter.limit("5/minute")
def change_password():
    errors: List[str] = []
    messages: List[str] = []

    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    if not current or not new or not confirm:
        errors.append("All password fields are required.")
    elif not check_password_hash(current_user.password_hash, current):
        errors.append("Current password is incorrect.")
    elif len(new) < 8:
        errors.append("New password must be at least 8 characters.")
    elif new != confirm:
        errors.append("New passwords do not match.")

    if not errors:
        current_user.password_hash = generate_password_hash(new)
        db.session.commit()
        messages.append("Password updated.")

    return render_template("auth/account.html", errors=errors, messages=messages)


@bp.post("/account/delete")
@login_required
# @limiter.limit("3/hour")
def delete_account():
    # Delete user and cascade to swimmers/PBs via FK ondelete=CASCADE
    user = db.session.get(User, current_user.id)
    logout_user()
    if user is not None:
        db.session.delete(user)
        db.session.commit()
    return redirect(url_for("auth.login"))
