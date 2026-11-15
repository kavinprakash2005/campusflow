"""Shared list/query helpers used by several blueprints."""
import math

import workflow
from db import get_db

PER_PAGE = 12
STATUSES = ["pending", "approved", "rejected", "returned", "cancelled"]

LIST_SELECT = (
    "SELECT r.id, r.request_uid, r.title, r.status, r.start_date, r.end_date, r.days, r.created_at, "
    "r.updated_at, r.is_confidential, r.requester_id, r.department_id, t.name AS type_name, t.icon AS type_icon, "
    "t.code AS type_code, u.full_name AS requester_name, ur.code AS requester_role, d.code AS dept_code, "
    "(SELECT s.step_role FROM request_steps s WHERE s.request_id = r.id "
    " AND s.status IN ('pending','returned') ORDER BY s.step_order LIMIT 1) AS current_role "
)
LIST_FROM = (
    "FROM requests r JOIN request_types t ON t.id = r.request_type_id "
    "JOIN users u ON u.id = r.requester_id JOIN roles ur ON ur.id = u.role_id "
    "LEFT JOIN departments d ON d.id = r.department_id "
)


def scope_clause(user, scope):
    """(sql, args) restricting rows to what `scope` allows for this user. Raises KeyError if the
    scope is not valid for the user - callers must have already checked the permission."""
    if scope == "mine":
        return "r.requester_id = %s", [user["id"]]
    if scope == "queue":
        frag, args = workflow.review_scope_sql(user)
        return ("r.status = 'pending' AND r.requester_id <> %s AND EXISTS (SELECT 1 FROM request_steps s "
                "WHERE s.request_id = r.id AND s.status = 'pending' AND " + frag + ")", [user["id"]] + args)
    if scope == "department":
        return "r.department_id = %s AND r.requester_id <> %s", [user["department_id"], user["id"]]
    if scope == "dept_all":
        return "r.department_id = %s", [user["department_id"]]
    if scope == "principal":
        return ("EXISTS (SELECT 1 FROM request_steps s WHERE s.request_id = r.id AND s.step_role = 'principal' "
                "AND s.status IN ('pending','approved','rejected','returned'))", [])
    if scope == "all":
        return "1 = 1", []
    raise KeyError(scope)


def filter_clause(args, scope):
    where, params = [], []
    q = (args.get("q") or "").strip()[:60]
    if q:
        like = "%" + q + "%"
        where.append("(r.request_uid LIKE %s OR u.full_name LIKE %s OR (r.is_confidential = 0 AND r.title LIKE %s))")
        params += [like, like, like]
    if args.get("status") in STATUSES:
        where.append("r.status = %s")
        params.append(args["status"])
    if args.get("type"):
        where.append("t.code = %s")
        params.append(args["type"][:20])
    if args.get("dept") and scope in ("all", "principal"):
        where.append("d.code = %s")
        params.append(args["dept"][:10])
    if args.get("role") in ("student", "faculty", "hod"):
        where.append("ur.code = %s")
        params.append(args["role"])
    if args.get("from"):
        where.append("r.created_at >= %s")
        params.append(args["from"][:10])
    if args.get("to"):
        where.append("r.created_at <= %s")
        params.append(args["to"][:10] + " 23:59:59")
    return where, params


def page_arg(args):
    try:
        return max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        return 1


def list_requests(user, scope, args, per_page=PER_PAGE, order=None):
    db = get_db()
    sc, sp = scope_clause(user, scope)
    fw, fp = filter_clause(args, scope)
    where = " WHERE " + " AND ".join([sc] + fw)
    total = db.scalar("SELECT COUNT(*) " + LIST_FROM + where, sp + fp) or 0
    pages = max(1, math.ceil(total / per_page))
    page = min(page_arg(args), pages)
    order = order or ("r.created_at ASC, r.id ASC" if scope == "queue" else "r.id DESC")
    rows = db.query(LIST_SELECT + LIST_FROM + where + " ORDER BY " + order + " LIMIT %s OFFSET %s",
                    sp + fp + [per_page, (page - 1) * per_page])
    mask_confidential(user, rows)
    return rows, dict(total=total, page=page, pages=pages, per_page=per_page)


def mask_confidential(user, rows):
    for r in rows:
        r["viewable"] = workflow.can_view(user, r)
        r["masked"] = bool(r["is_confidential"]) and not workflow.can_see_confidential(user, r)
        if r["masked"]:
            r["title"] = "Confidential request"


def filter_options():
    db = get_db()
    return dict(types=db.query("SELECT code, name FROM request_types ORDER BY id"),
                depts=db.query("SELECT code, name FROM departments ORDER BY code"), statuses=STATUSES)
