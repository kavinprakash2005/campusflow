"""The five role dashboards."""
import datetime as dt

from flask import Blueprint, g, render_template

import workflow
from core import role_required
from db import get_db
from views import stats
from views.common import list_requests

bp = Blueprint("dashboards", __name__)


def _month_start():
    return dt.date.today().replace(day=1).isoformat()


def _types(user):
    import forms
    allowed = forms.ROLE_TYPES.get(user["role"], [])
    return [t for t in get_db().query("SELECT * FROM request_types WHERE is_active = 1 ORDER BY id")
            if t["code"] in allowed]


def _my_counts(user):
    w, p = stats.where_for(user, "mine")
    return stats.status_counts(w, p)


def _queue(user, n=6):
    rows, pg = list_requests(user, "queue", {}, per_page=n)
    return rows, pg["total"]


def _recent_decisions(user, n=6):
    return get_db().query(
        "SELECT h.created_at, h.action, r.id AS rid, r.request_uid, r.is_confidential, t.name AS type_name, "
        "u.full_name AS requester_name FROM approval_history h JOIN requests r ON r.id = h.request_id "
        "JOIN request_types t ON t.id = r.request_type_id JOIN users u ON u.id = r.requester_id "
        "WHERE h.actor_id = %s AND h.action IN ('approved','rejected','returned','forwarded') "
        "ORDER BY h.id DESC LIMIT %s", (user["id"], n))


@bp.route("/student")
@role_required("student")
def student():
    u = g.user
    counts = _my_counts(u)
    recent, _ = list_requests(u, "mine", {}, per_page=6)
    attention, _ = list_requests(u, "mine", {"status": "returned"}, per_page=5)
    return render_template("student/dashboard.html", counts=counts, recent=recent, attention=attention,
                           types=_types(u))


@bp.route("/faculty")
@role_required("faculty")
def faculty():
    u = g.user
    queue, awaiting = _queue(u)
    reviewed = get_db().scalar(
        "SELECT COUNT(*) FROM approval_history WHERE actor_id = %s AND action IN ('approved','rejected','returned') "
        "AND created_at >= %s", (u["id"], _month_start())) or 0
    recent, _ = list_requests(u, "mine", {}, per_page=5)
    return render_template("faculty/dashboard.html", counts=_my_counts(u), queue=queue, awaiting=awaiting,
                           reviewed=reviewed, recent=recent, types=_types(u),
                           advisees=get_db().scalar("SELECT COUNT(*) FROM users WHERE advisor_id = %s AND is_active = 1",
                                                    (u["id"],)) or 0,
                           decisions=_recent_decisions(u, 5))


@bp.route("/hod")
@role_required("hod")
def hod():
    u = g.user
    queue, pending = _queue(u, 7)
    w, p = stats.where_for(u, "dept_all")
    dept_counts = stats.status_counts(w, p)
    approved_month = get_db().scalar(
        "SELECT COUNT(*) FROM approval_history h JOIN requests r ON r.id = h.request_id "
        "WHERE h.actor_id = %s AND h.action IN ('approved','forwarded') AND h.created_at >= %s",
        (u["id"], _month_start())) or 0
    return render_template("hod/dashboard.html", queue=queue, pending=pending, dept_counts=dept_counts,
                           approved_month=approved_month, types_chart=stats.by_type(w, p),
                           donut=stats.donut(dept_counts), trend=stats.trend(w, p),
                           mine=_my_counts(u), decisions=_recent_decisions(u, 6),
                           faculty_count=get_db().scalar(
                               "SELECT COUNT(*) FROM users x JOIN roles r ON r.id = x.role_id "
                               "WHERE r.code = 'faculty' AND x.department_id = %s AND x.is_active = 1",
                               (u["department_id"],)) or 0,
                           student_count=get_db().scalar(
                               "SELECT COUNT(*) FROM users x JOIN roles r ON r.id = x.role_id "
                               "WHERE r.code = 'student' AND x.department_id = %s AND x.is_active = 1",
                               (u["department_id"],)) or 0)


@bp.route("/principal")
@role_required("principal")
def principal():
    u = g.user
    queue, pending = _queue(u, 7)
    w, p = stats.where_for(u, "all")
    counts = stats.status_counts(w, p)
    depts = stats.by_department(w, p)
    routed_w, routed_p = stats.where_for(u, "principal")
    return render_template("principal/dashboard.html", queue=queue, pending=pending, counts=counts, depts=depts,
                           donut=stats.donut(counts), trend=stats.trend(w, p), types_chart=stats.by_type(w, p),
                           turnaround=stats.avg_turnaround_days(w, p),
                           routed=stats.status_counts(routed_w, routed_p), decisions=_recent_decisions(u, 6))


@bp.route("/admin")
@role_required("admin")
def admin():
    db = get_db()
    w, p = stats.where_for(g.user, "all")
    counts = stats.status_counts(w, p)
    roles = db.query("SELECT r.name AS k, COUNT(u.id) AS n FROM roles r LEFT JOIN users u ON u.role_id = r.id "
                     "AND u.is_active = 1 GROUP BY r.name, r.id ORDER BY r.id")
    rules = db.query("SELECT t.name AS type_name, w.requester_role, w.step_role, w.min_days FROM workflow_rules w "
                     "JOIN request_types t ON t.id = w.request_type_id ORDER BY t.id, w.requester_role, w.step_order")
    summary = {}
    for r in rules:
        step = r["step_role"].title() + (" (≥%d days)" % r["min_days"] if r["min_days"] else "")
        summary.setdefault((r["type_name"], r["requester_role"]), []).append(step)
    activity = db.query(
        "SELECT a.*, u.full_name AS actor FROM audit_logs a LEFT JOIN users u ON u.id = a.user_id "
        "ORDER BY a.id DESC LIMIT 8")
    return render_template(
        "admin/dashboard.html", counts=counts, donut=stats.donut(counts), roles=[(r["k"], int(r["n"])) for r in roles],
        users_total=db.scalar("SELECT COUNT(*) FROM users WHERE is_active = 1"),
        users_inactive=db.scalar("SELECT COUNT(*) FROM users WHERE is_active = 0"),
        depts=db.scalar("SELECT COUNT(*) FROM departments WHERE is_active = 1"),
        summary=summary, activity=activity, trend=stats.trend(w, p),
        locked=db.scalar("SELECT COUNT(*) FROM users WHERE locked_until IS NOT NULL AND locked_until > %s",
                         (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)))
