"""CampusFlow – Unified Digital Request and Approval Portal (Flask application factory)."""
import datetime as dt
import json
import os
import sys

from flask import Flask, g, jsonify, render_template, request, session

from config import Config
import core
import db as database
from core import ROLE_LABEL, STEP_LABEL, csrf_protect, csrf_token, has_perm, load_user
from workflow import extra_of, status_text


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY"):
        sys.exit("SECRET_KEY is not set. Copy .env.example to .env and set a long random value "
                 "(python -c \"import secrets; print(secrets.token_hex(32))\").")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.before_request(load_user)
    app.before_request(csrf_protect)
    app.teardown_request(database.close_db)

    from views import register_blueprints
    register_blueprints(app)

    # ------------------------------------------------------------ template helpers
    @app.context_processor
    def inject():
        user = g.get("user")
        ctx = {"csrf_token": csrf_token, "has_perm": has_perm, "ROLE_LABEL": ROLE_LABEL,
               "STEP_LABEL": STEP_LABEL, "college_name": app.config["COLLEGE_NAME"],
               "asset_mode": app.config["ASSET_MODE"], "now_year": dt.date.today().year,
               "status_text": status_text, "extra_of": extra_of, "unread_count": 0,
               "recent_notifications": [], "nav": []}
        if user:
            from views.nav import nav_for
            ctx["unread_count"] = core.unread_count(user["id"])
            ctx["recent_notifications"] = database.get_db().query(
                "SELECT * FROM notifications WHERE user_id = %s ORDER BY id DESC LIMIT 5", (user["id"],))
            ctx["nav"] = nav_for(user)
        return ctx

    @app.template_filter("fdt")
    def fdt(value):
        if not value:
            return "—"
        try:
            d = dt.datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
            return d.strftime("%d %b %Y, %I:%M %p").replace(" 0", " ")
        except ValueError:
            try:
                return dt.date.fromisoformat(value[:10]).strftime("%d %b %Y")
            except ValueError:
                return value

    @app.template_filter("fd")
    def fd(value):
        try:
            return dt.date.fromisoformat(value[:10]).strftime("%d %b %Y")
        except (TypeError, ValueError):
            return "—"

    @app.template_filter("initials")
    def initials(name):
        parts = [p for p in (name or "?").replace(".", " ").split() if p]
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    @app.template_filter("tojson_safe")
    def tojson_safe(value):
        return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    # ------------------------------------------------------------ security headers
    @app.after_request
    def headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "script-src 'self' https://cdn.jsdelivr.net; font-src 'self' https://cdn.jsdelivr.net; "
            "frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ errors
    def _err(code, title, msg):
        return render_template("error.html", code=code, title=title, message=msg), code

    @app.errorhandler(400)
    def e400(e):
        return _err(400, "Request rejected", getattr(e, "description", "Bad request."))

    @app.errorhandler(403)
    def e403(e):
        return _err(403, "Access denied", "You don't have permission to open this page. "
                    "This attempt has been recorded.")

    @app.errorhandler(404)
    def e404(e):
        return _err(404, "Page not found", "The page you are looking for doesn't exist or was moved.")

    @app.errorhandler(413)
    def e413(e):
        return _err(413, "Upload too large", "Files must be 5 MB or smaller.")

    @app.errorhandler(500)
    def e500(e):
        return _err(500, "Something went wrong", "An unexpected error occurred. Please try again.")

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG", "0") == "1",
                     host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")))
