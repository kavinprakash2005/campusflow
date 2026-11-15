"""Reports for HOD (department) and Principal/Admin (college). Aggregates + metadata-only CSV."""
import csv
import io

from flask import Blueprint, abort, g, make_response, render_template, request

import workflow
from core import audit, login_required
from db import get_db
from views import stats
from views.common import LIST_FROM, LIST_SELECT, filter_options, list_requests, mask_confidential

bp = Blueprint("reports", __name__)


def _scope():
    perms = g.user["perms"]
    if "reports.college" in perms:
        return "all"
    if "reports.department" in perms:
        return "dept_all"
    audit("access_denied", "route", request.path, "reports")
    abort(403)


@bp.route("/reports")
@login_required
def index():
    scope = _scope()
    w, p = stats.where_for(g.user, scope, request.args)
    counts = stats.status_counts(w, p)
    rows, pg = list_requests(g.user, scope, request.args, per_page=10)
    done = counts["approved"] + counts["rejected"]
    return render_template(
        "reports.html", scope=scope, counts=counts, donut=stats.donut(counts), types_chart=stats.by_type(w, p),
        depts=stats.by_department(w, p) if scope == "all" else [], trend=stats.trend(w, p),
        turnaround=stats.avg_turnaround_days(w, p), approval_rate=round(100 * counts["approved"] / done) if done else None,
        rows=rows, pg=pg, opts=filter_options(), args=request.args)


def _safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v      # neutralise spreadsheet formulas


@bp.route("/reports/export.csv")
@login_required
def export():
    scope = _scope()
    w, p = stats.where_for(g.user, scope, request.args)
    rows = get_db().query(LIST_SELECT + ", r.completed_at " + LIST_FROM + w + " ORDER BY r.id DESC LIMIT 5000", p)
    mask_confidential(g.user, rows)
    buf = io.StringIO()
    out = csv.writer(buf)
    college = scope == "all"
    head = ["Request ID", "Type", "Department", "Requester role", "Status", "Start date", "End date", "Days",
            "Submitted", "Completed"]
    if not college:
        head[3:3] = ["Requester", "Title"]
    out.writerow(head)
    for r in rows:
        line = [r["request_uid"], r["type_name"], r["dept_code"], r["requester_role"], r["status"], r["start_date"],
                r["end_date"], r["days"], r["created_at"], r["completed_at"]]
        if not college:
            line[3:3] = [r["requester_name"], r["title"]]
        out.writerow([_safe(x) for x in line])
    audit("report_exported", "report", scope, "%d rows" % len(rows))
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = "text/csv; charset=utf-8"
    resp.headers["Content-Disposition"] = 'attachment; filename="campusflow-report.csv"'
    return resp
