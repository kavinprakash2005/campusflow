"""Authentication helpers, CSRF, permission decorators, audit log and notifications."""
import functools
import hmac
import secrets
from urllib.parse import urlparse

from flask import abort, current_app, flash, g, redirect, request, session, url_for

from db import get_db, now_str

ROLE_LABEL = {"student": "Student", "faculty": "Faculty", "hod": "HOD",
              "principal": "Principal", "admin": "Admin"}
STEP_LABEL = {"faculty": "Faculty advisor", "hod": "HOD", "principal": "Principal"}
ROLE_HOME = {"student": "dashboards.student", "faculty": "dashboards.faculty",
             "hod": "dashboards.hod", "principal": "dashboards.principal",
             "admin": "dashboards.admin"}


# ---------------------------------------------------------------- sessions
def load_user():
    g.user = None
    uid = session.get("uid")
    if not uid:
        return
    db = get_db()
    user = db.one(
        "SELECT u.*, r.code AS role, r.name AS role_name, d.name AS dept_name, d.code AS dept_code "
        "FROM users u JOIN roles r ON r.id = u.role_id "
        "LEFT JOIN departments d ON d.id = u.department_id "
        "WHERE u.id = %s AND u.is_active = 1", (uid,))
    # a password change invalidates every other session of that user
    if not user or session.get("pwv") != user["password_hash"][-16:]:
        session.clear()
        return
    rows = db.query(
        "SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
        "WHERE rp.role_id = %s", (user["role_id"],))
    user["perms"] = {r["code"] for r in rows}
    g.user = user
    session.permanent = True


def start_session(user):
    session.clear()
    session["uid"] = user["id"]
    session["pwv"] = user["password_hash"][-16:]
    session["_csrf"] = secrets.token_urlsafe(32)   # new token after login (fixation defence)
    session.permanent = True


def has_perm(code):
    return bool(g.get("user")) and code in g.user["perms"]


def safe_next(target):
    """Only allow same-site relative redirects."""
    if not target:
        return None
    parts = urlparse(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def login_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not g.get("user"):
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapped


def role_required(*roles):
    def deco(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if g.user["role"] not in roles:
                audit("access_denied", "route", request.path, "role %s" % g.user["role"])
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def perm_required(*perms):
    """User must hold ALL listed permissions (checked server-side on every call)."""
    def deco(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if not all(p in g.user["perms"] for p in perms):
                audit("access_denied", "route", request.path, "missing %s" % ",".join(perms))
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def any_perm_required(*perms):
    def deco(view):
        @functools.wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if not any(p in g.user["perms"] for p in perms):
                audit("access_denied", "route", request.path, "missing any of %s" % ",".join(perms))
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


# -------------------------------------------------------------------- CSRF
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_protect():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or ""
        expected = session.get("_csrf", "")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400, "Invalid or missing CSRF token. Refresh the page and try again.")


# ------------------------------------------------------------ audit / notify
def client_ip():
    return (request.remote_addr or "")[:45]


def audit(action, entity=None, entity_id=None, details=None, user_id=None):
    uid = user_id if user_id is not None else (g.user["id"] if g.get("user") else None)
    get_db().execute(
        "INSERT INTO audit_logs (user_id, action, entity, entity_id, details, ip_address, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (uid, action, entity, str(entity_id) if entity_id is not None else None,
         (details or "")[:500], client_ip(), now_str()))


def notify(user_id, title, message, request_id=None):
    get_db().execute(
        "INSERT INTO notifications (user_id, request_id, title, message, is_read, created_at) "
        "VALUES (%s,%s,%s,%s,0,%s)", (user_id, request_id, title[:150], message[:255], now_str()))


def unread_count(user_id):
    return get_db().scalar(
        "SELECT COUNT(*) FROM notifications WHERE user_id = %s AND is_read = 0", (user_id,)) or 0
