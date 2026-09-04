from datetime import timedelta

import bcrypt
from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

auth_bp = Blueprint("auth", __name__)


def init_auth(app, config):
    app.secret_key = config.get("Auth", "SECRET_KEY")
    app.config["AUTH_USERNAME"] = config.get("Auth", "USERNAME")
    app.config["AUTH_PASSWORD_HASH"] = config.get("Auth", "PASSWORD_HASH").encode("utf-8")
    app.config["SESSION_COOKIE_SECURE"] = config.getboolean("Auth", "SECURE_COOKIES", fallback=True)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

    app.register_blueprint(auth_bp)

    @app.before_request
    def require_login():
        if request.endpoint in ("auth.login", "auth.logout", "static"):
            return None
        if not session.get("logged_in"):
            return redirect(url_for("auth.login"))
        return None


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "").encode("utf-8")
        expected_username = current_app.config["AUTH_USERNAME"]
        expected_hash = current_app.config["AUTH_PASSWORD_HASH"]
        if username == expected_username and bcrypt.checkpw(password, expected_hash):
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
