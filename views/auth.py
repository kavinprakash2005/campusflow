"""Sign in / sign out, profile and password change."""
import re

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import seed
from core import ROLE_HOME, audit, login_required, safe_next, start_session
from db import get_db, now_str
import datetime as dt

bp = Blueprint("auth", __name__)

PASSWORD_RULE = "Use at least 8 characters with a letter, a number and a symbol or capital letter."


def strong_password(pw):
    return bool(pw) and len(pw) >= 8 and re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw) \
        and (re.search(r"[^A-Za-z0-9]", pw) or re.search(r"[A-Z]", pw))


@bp.route("/")
def index():
    if g.user:
        return redirect(url_for(ROLE_HOME[g.user["role"]]))
    return redirect(url_for("auth.login"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for(ROLE_HOME[g.user["role"]]))
    errors, ident = {}, ""
    if request.method == "POST":
        ident = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""
        if not ident:
            errors["identifier"] = "Enter your college email or ID."
        if not password:
            errors["password"] = "Enter your password."
        if not errors:
            db = get_db()
            user = db.one("SELECT u.*, r.code AS role FROM users u JOIN roles r ON r.id = u.role_id "
                          "WHERE LOWER(u.email) = LOWER(%s) OR LOWER(u.college_id) = LOWER(%s)", (ident, ident))
            now = now_str()
            if user and user["locked_until"] and user["locked_until"] > now:
                audit("login_blocked", "user", user["college_id"], "account locked", user_id=user["id"])
                errors["form"] = "Too many failed attempts. Try again in a few minutes."
            elif not user or not user["is_active"] or not check_password_hash(user["password_hash"], password):
                if user:
                    fails = user["failed_attempts"] + 1
                    locked = None
                    if fails >= current_app.config["MAX_FAILED_LOGINS"]:
                        locked = (dt.datetime.now() + dt.timedelta(
                            minutes=current_app.config["LOCKOUT_MINUTES"])).strftime("%Y-%m-%d %H:%M:%S")
                        fails = 0
                    db.execute("UPDATE users SET failed_attempts = %s, locked_until = %s WHERE id = %s",
                               (fails, locked, user["id"]))
                audit("login_failed", "user", ident[:40], "invalid credentials" if user else "unknown account",
                      user_id=user["id"] if user else None)
                errors["form"] = "Invalid credentials. Check your ID/email and password."
            else:
                db.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login = %s WHERE id = %s",
                           (now, user["id"]))
                start_session(user)
                audit("login", "user", user["college_id"], "role %s" % user["role"], user_id=user["id"])
                return redirect(safe_next(request.args.get("next")) or url_for(ROLE_HOME[user["role"]]))
    demo = seed.DEMO_LOGINS if current_app.config["SHOW_DEMO_ACCOUNTS"] else []
    return render_template("login.html", errors=errors, ident=ident, demo=demo,
                           demo_password=seed.DEMO_PASSWORD), (401 if errors.get("form") else 200)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit("logout", "user", g.user["college_id"])
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/profile")
@login_required
def profile():
    u = g.user
    advisor = None
    if u.get("advisor_id"):
        advisor = get_db().one("SELECT full_name, email FROM users WHERE id = %s", (u["advisor_id"],))
    perms = get_db().query("SELECT p.code, p.description FROM role_permissions rp JOIN permissions p "
                           "ON p.id = rp.permission_id WHERE rp.role_id = %s ORDER BY p.code", (u["role_id"],))
    return render_template("profile.html", advisor=advisor, perms=perms, rule=PASSWORD_RULE)


@bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    cur, new, conf = (request.form.get(k) or "" for k in ("current", "new", "confirm"))
    db = get_db()
    row = db.one("SELECT password_hash FROM users WHERE id = %s", (g.user["id"],))
    if not check_password_hash(row["password_hash"], cur):
        flash("Your current password is incorrect.", "danger")
    elif new != conf:
        flash("New password and confirmation do not match.", "danger")
    elif not strong_password(new):
        flash(PASSWORD_RULE, "danger")
    elif check_password_hash(row["password_hash"], new):
        flash("Choose a password different from the current one.", "danger")
    else:
        h = generate_password_hash(new)
        db.execute("UPDATE users SET password_hash = %s WHERE id = %s", (h, g.user["id"]))
        session["pwv"] = h[-16:]             # keep this session, invalidate all others
        audit("password_changed", "user", g.user["college_id"])
        flash("Password updated.", "success")
    return redirect(url_for("auth.profile"))
