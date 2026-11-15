"""Administration: users, departments, role permissions, approval workflow, system activity."""
import math
import re

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

import datetime as dt
from core import audit, perm_required
from db import get_db, now_str
from forms import ROLE_TYPES
from views.auth import PASSWORD_RULE, strong_password
from views.common import page_arg

bp = Blueprint("admin", __name__, url_prefix="/admin")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
PHONE_RE = re.compile(r"^[0-9+\-\s()]{7,20}$")
STAGES_FOR = {"student": ["faculty", "hod", "principal"], "faculty": ["hod", "principal"], "hod": ["principal"]}


# ---------------------------------------------------------------------- users
@bp.route("/users")
@perm_required("users.manage")
def users():
    db = get_db()
    where, params = [], []
    q = (request.args.get("q") or "").strip()[:60]
    if q:
        where.append("(u.full_name LIKE %s OR u.email LIKE %s OR u.college_id LIKE %s)")
        params += ["%" + q + "%"] * 3
    if request.args.get("role"):
        where.append("r.code = %s")
        params.append(request.args["role"])
    if request.args.get("dept"):
        where.append("d.code = %s")
        params.append(request.args["dept"])
    if request.args.get("status") in ("active", "inactive"):
        where.append("u.is_active = %s")
        params.append(1 if request.args["status"] == "active" else 0)
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = "FROM users u JOIN roles r ON r.id = u.role_id LEFT JOIN departments d ON d.id = u.department_id" + w
    total = db.scalar("SELECT COUNT(*) " + base, params) or 0
    per = 15
    pages = max(1, math.ceil(total / per))
    page = min(page_arg(request.args), pages)
    rows = db.query("SELECT u.id, u.college_id, u.email, u.full_name, u.is_active, u.locked_until, u.last_login, "
                    "r.code AS role, r.name AS role_name, d.code AS dept_code " + base +
                    " ORDER BY r.id, u.full_name LIMIT %s OFFSET %s", params + [per, (page - 1) * per])
    now = now_str()
    for r in rows:
        r["locked"] = bool(r["locked_until"] and r["locked_until"] > now)
    return render_template("admin/users.html", rows=rows, pg=dict(total=total, page=page, pages=pages),
                           roles=db.query("SELECT * FROM roles ORDER BY id"),
                           depts=db.query("SELECT * FROM departments ORDER BY code"), args=request.args)


def _user_form_ctx(values, errors, editing):
    db = get_db()
    return dict(values=values, errors=errors, editing=editing, rule=PASSWORD_RULE,
                roles=db.query("SELECT * FROM roles ORDER BY id"),
                depts=db.query("SELECT * FROM departments WHERE is_active = 1 ORDER BY code"),
                faculty=db.query("SELECT u.id, u.full_name, d.code AS dept_code FROM users u JOIN roles r ON "
                                 "r.id = u.role_id LEFT JOIN departments d ON d.id = u.department_id "
                                 "WHERE r.code = 'faculty' AND u.is_active = 1 ORDER BY d.code, u.full_name"))


@bp.route("/users/new", methods=["GET", "POST"])
@bp.route("/users/<int:uid>/edit", methods=["GET", "POST"])
@perm_required("users.manage")
def user_form(uid=None):
    db = get_db()
    editing = db.one("SELECT u.*, r.code AS role FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s",
                     (uid,)) if uid else None
    if uid and not editing:
        abort(404)
    if request.method == "GET":
        return render_template("admin/user_form.html", **_user_form_ctx(editing or {"is_active": 1}, {}, editing))
    f = request.form
    errors = {}
    cid, email, name = (f.get(k, "").strip() for k in ("college_id", "email", "full_name"))
    role_code, dept_id = f.get("role", ""), f.get("department_id") or None
    advisor_id = f.get("advisor_id") or None
    password = f.get("password", "")
    phone, desig = f.get("phone", "").strip(), f.get("designation", "").strip()[:80]
    active = 1 if f.get("is_active") == "on" else 0
    role = db.one("SELECT * FROM roles WHERE code = %s", (role_code,))
    if not ID_RE.match(cid):
        errors["college_id"] = "3–30 letters, numbers, dot, dash or underscore."
    if not EMAIL_RE.match(email) or len(email) > 120:
        errors["email"] = "Enter a valid email address."
    if not 2 <= len(name) <= 120:
        errors["full_name"] = "Name must be 2–120 characters."
    if not role:
        errors["role"] = "Choose a role."
    if phone and not PHONE_RE.match(phone):
        errors["phone"] = "Enter a valid phone number."
    if role_code in ("student", "faculty", "hod") and not dept_id:
        errors["department_id"] = "Department is required for this role."
    if dept_id and not db.scalar("SELECT id FROM departments WHERE id = %s", (dept_id,)):
        errors["department_id"] = "Unknown department."
    if role_code != "student":
        advisor_id = None
    elif advisor_id and not db.scalar(
            "SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s AND r.code = 'faculty' "
            "AND u.is_active = 1 AND u.department_id = %s", (advisor_id, dept_id)):
        errors["advisor_id"] = "Advisor must be an active faculty member of the same department."
    dup = db.scalar("SELECT id FROM users WHERE (LOWER(college_id) = LOWER(%s) OR LOWER(email) = LOWER(%s)) AND id <> %s",
                    (cid, email, uid or 0))
    if dup:
        errors["college_id"] = errors.get("college_id") or "This college ID or email is already registered."
    if not editing and not strong_password(password):
        errors["password"] = PASSWORD_RULE
    if editing and password and not strong_password(password):
        errors["password"] = PASSWORD_RULE
    if editing and editing["id"] == g.user["id"] and (role_code != "admin" or not active):
        errors["role"] = "You cannot change your own role or deactivate your own account."
    if editing and editing["role"] == "admin" and (role_code != "admin" or not active) and not db.scalar(
            "SELECT COUNT(*) FROM users u JOIN roles r ON r.id = u.role_id WHERE r.code = 'admin' AND "
            "u.is_active = 1 AND u.id <> %s", (editing["id"],)):
        errors["role"] = "The last active administrator cannot be demoted or deactivated."
    if errors:
        return render_template("admin/user_form.html", **_user_form_ctx(f, errors, editing)), 400
    if editing:
        sql = ("UPDATE users SET college_id=%s, email=%s, full_name=%s, role_id=%s, department_id=%s, advisor_id=%s, "
               "designation=%s, phone=%s, is_active=%s WHERE id=%s")
        db.execute(sql, (cid, email, name, role["id"], dept_id, advisor_id, desig, phone or None, active, uid))
        if password:
            db.execute("UPDATE users SET password_hash = %s, failed_attempts = 0, locked_until = NULL WHERE id = %s",
                       (generate_password_hash(password), uid))
        audit("user_updated", "user", cid, "role=%s active=%s pw_reset=%s" % (role_code, active, bool(password)))
        flash("User updated.", "success")
    else:
        db.execute("INSERT INTO users (college_id, email, full_name, password_hash, role_id, department_id, advisor_id, "
                   "designation, phone, is_active, failed_attempts, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
                   (cid, email, name, generate_password_hash(password), role["id"], dept_id, advisor_id, desig,
                    phone or None, active, now_str()))
        audit("user_created", "user", cid, "role=%s" % role_code)
        flash("User created.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@perm_required("users.manage")
def user_toggle(uid):
    db = get_db()
    u = db.one("SELECT u.*, r.code AS role FROM users u JOIN roles r ON r.id = u.role_id WHERE u.id = %s", (uid,)) \
        or abort(404)
    if u["id"] == g.user["id"]:
        flash("You cannot deactivate your own account.", "danger")
    elif u["role"] == "admin" and u["is_active"] and not db.scalar(
            "SELECT COUNT(*) FROM users x JOIN roles r ON r.id = x.role_id WHERE r.code = 'admin' AND x.is_active = 1 "
            "AND x.id <> %s", (uid,)):
        flash("The last active administrator cannot be deactivated.", "danger")
    else:
        db.execute("UPDATE users SET is_active = %s WHERE id = %s", (0 if u["is_active"] else 1, uid))
        audit("user_deactivated" if u["is_active"] else "user_activated", "user", u["college_id"])
        flash("User %s." % ("deactivated" if u["is_active"] else "activated"), "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                    else url_for("admin.users"))


@bp.route("/users/<int:uid>/unlock", methods=["POST"])
@perm_required("users.manage")
def user_unlock(uid):
    db = get_db()
    u = db.one("SELECT college_id FROM users WHERE id = %s", (uid,)) or abort(404)
    db.execute("UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = %s", (uid,))
    audit("user_unlocked", "user", u["college_id"])
    flash("Account unlocked.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------- departments
@bp.route("/departments", methods=["GET", "POST"])
@perm_required("departments.manage")
def departments():
    db = get_db()
    errors, values = {}, {}
    if request.method == "POST":
        values = request.form
        code, name = request.form.get("code", "").strip().upper(), request.form.get("name", "").strip()
        did = request.form.get("id")
        if not re.match(r"^[A-Z0-9]{2,10}$", code):
            errors["code"] = "2–10 letters or digits."
        if not 3 <= len(name) <= 120:
            errors["name"] = "Name must be 3–120 characters."
        if not errors and db.scalar("SELECT id FROM departments WHERE (code = %s OR LOWER(name) = LOWER(%s)) AND id <> %s",
                                    (code, name, did or 0)):
            errors["code"] = "A department with this code or name already exists."
        if not errors:
            if did:
                db.execute("UPDATE departments SET code=%s, name=%s WHERE id=%s", (code, name, did))
                audit("department_updated", "department", code)
                flash("Department updated.", "success")
            else:
                db.execute("INSERT INTO departments (code, name, is_active) VALUES (%s,%s,1)", (code, name))
                audit("department_created", "department", code)
                flash("Department added.", "success")
            return redirect(url_for("admin.departments"))
    rows = db.query(
        "SELECT d.*, (SELECT COUNT(*) FROM users u WHERE u.department_id = d.id AND u.is_active = 1) AS users_n, "
        "(SELECT COUNT(*) FROM requests r WHERE r.department_id = d.id) AS requests_n, "
        "(SELECT u.full_name FROM users u JOIN roles r ON r.id = u.role_id WHERE r.code = 'hod' AND "
        " u.department_id = d.id AND u.is_active = 1 ORDER BY u.id LIMIT 1) AS hod_name "
        "FROM departments d ORDER BY d.code")
    return render_template("admin/departments.html", rows=rows, errors=errors, values=values,
                           edit_id=request.args.get("edit"))


@bp.route("/departments/<int:did>/toggle", methods=["POST"])
@perm_required("departments.manage")
def department_toggle(did):
    db = get_db()
    d = db.one("SELECT * FROM departments WHERE id = %s", (did,)) or abort(404)
    if d["is_active"] and db.scalar("SELECT COUNT(*) FROM users WHERE department_id = %s AND is_active = 1", (did,)):
        flash("Move or deactivate the department's active users before deactivating it.", "danger")
    else:
        db.execute("UPDATE departments SET is_active = %s WHERE id = %s", (0 if d["is_active"] else 1, did))
        audit("department_toggled", "department", d["code"])
        flash("Department updated.", "success")
    return redirect(url_for("admin.departments"))


# ---------------------------------------------------------- roles & permissions
@bp.route("/roles", methods=["GET", "POST"])
@perm_required("roles.manage")
def roles():
    db = get_db()
    roles_ = db.query("SELECT * FROM roles ORDER BY id")
    perms = db.query("SELECT * FROM permissions ORDER BY code")
    current = {(r["role_id"], r["permission_id"]) for r in db.query("SELECT * FROM role_permissions")}
    if request.method == "POST":
        wanted = {(ro["id"], p["id"]) for ro in roles_ for p in perms if request.form.get("p_%d_%d" % (ro["id"], p["id"]))}
        admin_id = next(r["id"] for r in roles_ if r["code"] == "admin")
        keep = [p["id"] for p in perms if p["code"] in ("roles.manage", "users.manage")]
        if any((admin_id, k) not in wanted for k in keep):
            flash("Administrators must keep the 'Manage users' and 'Manage role permissions' permissions "
                  "(lock-out protection).", "danger")
            return redirect(url_for("admin.roles"))
        added, removed = wanted - current, current - wanted
        for rid, pid in removed:
            db.execute("DELETE FROM role_permissions WHERE role_id = %s AND permission_id = %s", (rid, pid))
        for rid, pid in added:
            db.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES (%s,%s)", (rid, pid))
        if added or removed:
            rc = {r["id"]: r["code"] for r in roles_}
            pc = {p["id"]: p["code"] for p in perms}
            audit("permissions_changed", "role", None, "+%s -%s" % (
                ",".join("%s:%s" % (rc[a], pc[b]) for a, b in sorted(added)) or "none",
                ",".join("%s:%s" % (rc[a], pc[b]) for a, b in sorted(removed)) or "none"))
            flash("Permissions saved: %d granted, %d revoked." % (len(added), len(removed)), "success")
        else:
            flash("No changes to save.", "info")
        return redirect(url_for("admin.roles"))
    return render_template("admin/roles.html", roles=roles_, perms=perms, current=current)


# ------------------------------------------------------------------- workflow
def _rules_for(db, type_id, role):
    return db.query("SELECT step_role, min_days FROM workflow_rules WHERE request_type_id = %s AND requester_role = %s "
                    "ORDER BY step_order", (type_id, role))


@bp.route("/workflow", methods=["GET", "POST"])
@perm_required("workflow.manage")
def workflow_rules():
    db = get_db()
    types = db.query("SELECT * FROM request_types ORDER BY id")
    if request.method == "POST":
        tid = request.form.get("type_id", type=int)
        t = next((x for x in types if x["id"] == tid), None) or abort(400)
        if request.form.get("toggle_active"):
            db.execute("UPDATE request_types SET is_active = %s WHERE id = %s", (0 if t["is_active"] else 1, tid))
            audit("request_type_toggled", "request_type", t["code"])
            flash("%s %s." % (t["name"], "disabled" if t["is_active"] else "enabled"), "success")
            return redirect(url_for("admin.workflow_rules"))
        new_rules, problems = {}, []
        for role in ROLE_TYPES:
            if t["code"] not in ROLE_TYPES[role]:
                continue
            steps = []
            for stage in STAGES_FOR[role]:
                if request.form.get("s_%s_%s" % (role, stage)):
                    md = 0
                    if stage == "principal":
                        raw = request.form.get("min_%s" % role, "0").strip() or "0"
                        if not raw.isdigit() or int(raw) > 365:
                            problems.append("Minimum days for %s must be 0–365." % role)
                        else:
                            md = int(raw)
                    steps.append((stage, md))
            if not steps:
                problems.append("%s requests need at least one approval stage." % role.title())
            new_rules[role] = steps
        if problems:
            flash(" ".join(problems), "danger")
            return redirect(url_for("admin.workflow_rules") + "#type-%s" % t["code"])
        for role, steps in new_rules.items():
            db.execute("DELETE FROM workflow_rules WHERE request_type_id = %s AND requester_role = %s", (tid, role))
            for i, (stage, md) in enumerate(steps, start=1):
                db.execute("INSERT INTO workflow_rules (request_type_id, requester_role, step_order, step_role, min_days) "
                           "VALUES (%s,%s,%s,%s,%s)", (tid, role, i, stage, md))
        audit("workflow_updated", "request_type", t["code"], "; ".join(
            "%s: %s" % (r, ">".join(s for s, _ in st)) for r, st in new_rules.items()))
        flash("Workflow for %s saved. It applies to new requests; requests already in progress keep their route." % t["name"],
              "success")
        return redirect(url_for("admin.workflow_rules") + "#type-%s" % t["code"])
    data = []
    for t in types:
        roles_ = []
        for role in ROLE_TYPES:
            if t["code"] in ROLE_TYPES[role]:
                rules = _rules_for(db, t["id"], role)
                roles_.append(dict(role=role, stages=STAGES_FOR[role], on={r["step_role"] for r in rules},
                                   min_days=next((r["min_days"] for r in rules if r["step_role"] == "principal"), 0)))
        data.append(dict(type=t, roles=roles_))
    return render_template("admin/workflow.html", data=data)


# ------------------------------------------------------------------- activity
@bp.route("/activity")
@perm_required("audit.system")
def activity():
    db = get_db()
    where, params = [], []
    a = request.args
    if a.get("action"):
        where.append("a.action = %s")
        params.append(a["action"][:40])
    if a.get("q"):
        where.append("(u.full_name LIKE %s OR a.entity_id LIKE %s OR a.details LIKE %s)")
        params += ["%" + a["q"][:60] + "%"] * 3
    if a.get("from"):
        where.append("a.created_at >= %s")
        params.append(a["from"][:10])
    if a.get("to"):
        where.append("a.created_at <= %s")
        params.append(a["to"][:10] + " 23:59:59")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = "FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id LEFT JOIN roles r ON r.id = u.role_id" + w
    total = db.scalar("SELECT COUNT(*) " + base, params) or 0
    per = 25
    pages = max(1, math.ceil(total / per))
    page = min(page_arg(a), pages)
    rows = db.query("SELECT a.*, u.full_name AS actor, r.code AS actor_role " + base +
                    " ORDER BY a.id DESC LIMIT %s OFFSET %s", params + [per, (page - 1) * per])
    actions = [r["action"] for r in db.query("SELECT DISTINCT action FROM audit_logs ORDER BY action")]
    return render_template("admin/activity.html", rows=rows, pg=dict(total=total, page=page, pages=pages),
                           actions=actions, args=a)
