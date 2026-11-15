"""Request lifecycle: create, list, review, resubmit, cancel, print/PDF, attachments."""
import datetime as dt
import math

from flask import (Blueprint, abort, current_app, flash, g, make_response, redirect, render_template, request,
                   send_from_directory, url_for)

import forms
import pdfgen
import workflow
from core import audit, login_required, perm_required
from db import get_db, now_str
from views.common import STATUSES, filter_options, list_requests, mask_confidential, page_arg

bp = Blueprint("reqs", __name__)


# ------------------------------------------------------------------ lists
def _list_page(scope, title, subtitle, **extra):
    rows, pg = list_requests(g.user, scope, request.args)
    return render_template("requests/list.html", rows=rows, pg=pg, scope=scope, title=title, subtitle=subtitle,
                           opts=filter_options(), args=request.args, **extra)


@bp.route("/requests")
@perm_required("request.create")
def mine():
    return _list_page("mine", "My requests", "Every request you have raised, with live status.")


@bp.route("/requests/queue")
@perm_required("request.review")
def queue():
    role = g.user["role"]
    if role not in ("faculty", "hod", "principal"):
        abort(403)
    subtitle = {"faculty": "Student requests assigned to you for review.",
                "hod": "Requests from your department waiting for your decision.",
                "principal": "Requests forwarded for Principal approval."}[role]
    return _list_page("queue", "Pending review queue", subtitle)


@bp.route("/requests/department")
@perm_required("request.view_department")
def department():
    return _list_page("department", "Department requests",
                      "All student and faculty requests in %s." % (g.user["dept_name"] or "your department"))


@bp.route("/requests/routed")
@perm_required("request.review")
def principal_list():
    if g.user["role"] != "principal":
        abort(403)
    return _list_page("principal", "Requests routed to the Principal",
                      "Only requests that required Principal approval appear here. Other requests are available as statistics.")


@bp.route("/requests/all")
@perm_required("request.view_all")
def all_requests():
    return _list_page("all", "All requests", "Read-only oversight. Details of confidential requests stay hidden.")


@bp.route("/audit/decisions")
@perm_required("audit.decisions")
def decision_audit():
    db = get_db()
    where, params = [], []
    if request.args.get("action") in ("approved", "rejected", "returned", "forwarded", "submitted", "assigned"):
        where.append("h.action = %s")
        params.append(request.args["action"])
    if request.args.get("dept"):
        where.append("d.code = %s")
        params.append(request.args["dept"][:10])
    if request.args.get("from"):
        where.append("h.created_at >= %s")
        params.append(request.args["from"][:10])
    if request.args.get("to"):
        where.append("h.created_at <= %s")
        params.append(request.args["to"][:10] + " 23:59:59")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    base = ("FROM approval_history h JOIN requests r ON r.id = h.request_id JOIN users a ON a.id = h.actor_id "
            "JOIN roles ar ON ar.id = a.role_id JOIN request_types t ON t.id = r.request_type_id "
            "LEFT JOIN departments d ON d.id = r.department_id" + w)
    total = db.scalar("SELECT COUNT(*) " + base, params) or 0
    per = 20
    pages = max(1, math.ceil(total / per))
    page = min(page_arg(request.args), pages)
    rows = db.query(
        "SELECT h.created_at, h.action, h.remark, h.from_status, h.to_status, r.id AS rid, r.request_uid, "
        "r.is_confidential, a.full_name AS actor_name, ar.code AS actor_role, t.name AS type_name, "
        "d.code AS dept_code, (SELECT COUNT(*) FROM request_steps s WHERE s.request_id = r.id AND "
        "s.step_role = 'principal' AND s.status IN ('pending','approved','rejected','returned')) AS routed "
        + base + " ORDER BY h.id DESC LIMIT %s OFFSET %s", params + [per, (page - 1) * per])
    for r in rows:   # remarks of confidential requests stay hidden unless explicitly authorised
        r["hide"] = bool(r["is_confidential"]) and "confidential.view" not in g.user["perms"] \
            and not (r["routed"] and r["actor_role"] == "principal" and r["actor_name"] == g.user["full_name"])
    return render_template("requests/decision_audit.html", rows=rows, pg=dict(total=total, page=page, pages=pages),
                           opts=filter_options(), args=request.args)


# ------------------------------------------------------------ create / edit
def _types_for(user):
    allowed = forms.ROLE_TYPES.get(user["role"], [])
    rows = get_db().query("SELECT * FROM request_types WHERE is_active = 1 ORDER BY id")
    out = []
    for r in rows:
        if r["code"] in allowed:
            out.append(dict(r, spec=forms.TYPE_DEFS[r["code"]]))
    return out


def _form_ctx(types, selected, values, errors, editing=None):
    return dict(types=types, selected=selected, values=values, errors=errors, editing=editing,
                today=dt.date.today().isoformat())


@bp.route("/requests/new")
@perm_required("request.create")
def new():
    types = _types_for(g.user)
    selected = request.args.get("type")
    if selected not in [t["code"] for t in types]:
        selected = None
    return render_template("requests/new.html", **_form_ctx(types, selected, {}, {}))


def _attachment_check():
    f = request.files.get("attachment")
    ok, err, info = forms.check_upload(f)
    return f, ok, err, info


def _store_attachment(req_id, f, info):
    stored = forms.save_upload(f, info)
    get_db().execute(
        "INSERT INTO attachments (request_id, original_name, stored_name, mime_type, size_bytes, uploaded_by, "
        "created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (req_id, info["name"], stored, info["mime"], info["size"], g.user["id"], now_str()))
    audit("attachment_uploaded", "request", req_id, info["name"])


@bp.route("/requests/new", methods=["POST"])
@perm_required("request.create")
def create():
    types = _types_for(g.user)
    code = request.form.get("type")
    type_row = next((t for t in types if t["code"] == code), None)
    if not type_row:
        abort(400, "Choose a valid request type.")
    cleaned, errors = forms.validate_request_form(request.form, code)
    f, ok, err, info = _attachment_check()
    if not ok:
        errors["attachment"] = err
    if errors:
        return render_template("requests/new.html", **_form_ctx(types, code, request.form, errors)), 400
    req = workflow.create_request(g.user, type_row, cleaned)
    if info:
        _store_attachment(req["id"], f, info)
    return redirect(url_for("reqs.detail", ref=req["id"], submitted=1))


@bp.route("/requests/<int:ref>/edit", methods=["GET", "POST"])
@login_required
def edit(ref):
    req = workflow.get_request(ref) or abort(404)
    if req["requester_id"] != g.user["id"]:
        audit("access_denied", "request", req["request_uid"], "edit")
        abort(403)
    if req["status"] != "returned":
        flash("Only requests returned for clarification can be edited.", "warning")
        return redirect(url_for("reqs.detail", ref=ref))
    types = [t for t in _types_for(g.user) if t["code"] == req["type_code"]] or abort(403)
    if request.method == "GET":
        values = dict(req, **workflow.extra_of(req))
        return render_template("requests/new.html", **_form_ctx(types, req["type_code"], values, {}, editing=req))
    cleaned, errors = forms.validate_request_form(request.form, req["type_code"])
    f, ok, err, info = _attachment_check()
    if not ok:
        errors["attachment"] = err
    if errors:
        return render_template("requests/new.html", **_form_ctx(types, req["type_code"], request.form, errors,
                                                               editing=req)), 400
    try:
        workflow.resubmit(g.user, req, cleaned)
    except workflow.WorkflowError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("reqs.detail", ref=ref))
    if info:
        _store_attachment(ref, f, info)
    flash("Request resubmitted for review.", "success")
    return redirect(url_for("reqs.detail", ref=ref))


@bp.route("/requests/<int:ref>/cancel", methods=["POST"])
@login_required
def cancel(ref):
    req = workflow.get_request(ref) or abort(404)
    try:
        workflow.cancel(g.user, req)
        flash("Request cancelled.", "success")
    except workflow.WorkflowError as exc:
        audit("access_denied", "request", req["request_uid"], "cancel")
        flash(str(exc), "danger")
    return redirect(url_for("reqs.detail", ref=ref))


# ------------------------------------------------------------------ detail
def _load_for_view(ref):
    req = workflow.get_request(ref) or abort(404)
    steps = workflow.get_steps(req["id"])
    if not workflow.can_view(g.user, req, steps):
        audit("access_denied", "request", req["request_uid"], "view")
        abort(403)
    return req, steps


def _field_rows(req):
    spec = forms.TYPE_DEFS.get(req["type_code"], {"fields": []})
    extra = workflow.extra_of(req)
    return [(f["label"], extra.get(f["name"])) for f in spec["fields"] if extra.get(f["name"])]


@bp.route("/requests/<int:ref>")
@login_required
def detail(ref):
    req, steps = _load_for_view(ref)
    user, db = g.user, get_db()
    show = workflow.can_see_confidential(user, req, steps)
    history = workflow.get_history(req["id"])
    attachments = db.query("SELECT * FROM attachments WHERE request_id = %s ORDER BY id", (req["id"],)) if show else []
    my_step = workflow.pending_step_for(user, req, steps)
    later = [s for s in steps if my_step and s["step_order"] > my_step["step_order"] and s["status"] == "waiting"]
    actions = {}
    if my_step:
        actions = dict(approve=True, reject=True, remark=True,
                       return_=("request.return" in user["perms"]),
                       forward=(my_step["step_role"] == "hod" and "request.forward" in user["perms"]
                                and not any(s["step_role"] == "principal" for s in later)),
                       final=not later)
    faculty_choices = []
    if user["role"] == "hod" and "request.assign" in user["perms"] and user["department_id"] == req["department_id"] \
            and req["status"] == "pending" and req["requester_id"] != user["id"]:
        cur = next((s for s in steps if s["status"] == "pending"), None)
        if cur and cur["step_role"] in ("faculty", "hod"):
            faculty_choices = db.query(
                "SELECT u.id, u.full_name FROM users u JOIN roles r ON r.id = u.role_id WHERE r.code = 'faculty' "
                "AND u.is_active = 1 AND u.department_id = %s AND u.id <> %s ORDER BY u.full_name",
                (req["department_id"], req["requester_id"]))
    approver = next((h for h in reversed(history) if h["action"] == "approved"), None)
    is_owner = req["requester_id"] == user["id"]
    return render_template(
        "requests/detail.html", req=req, steps=steps, history=history, attachments=attachments, show=show,
        fields=_field_rows(req), my_step=my_step, actions=actions, faculty_choices=faculty_choices,
        is_owner=is_owner, submitted=request.args.get("submitted") == "1", approver=approver,
        can_certificate=(req["type_code"] == "bonafide" and req["status"] == "approved"))


@bp.route("/requests/<int:ref>/act", methods=["POST"])
@login_required
def act(ref):
    req = workflow.get_request(ref) or abort(404)
    try:
        flash(workflow.act(g.user, req, request.form.get("action", ""), request.form.get("remark", "")), "success")
    except workflow.WorkflowError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("reqs.detail", ref=ref) if workflow.can_view(g.user, workflow.get_request(ref))
                    else url_for("reqs.queue"))


@bp.route("/requests/<int:ref>/assign", methods=["POST"])
@login_required
def assign(ref):
    req = workflow.get_request(ref) or abort(404)
    try:
        faculty_id = int(request.form.get("faculty_id", ""))
        flash(workflow.assign_reviewer(g.user, req, faculty_id, (request.form.get("remark") or "")[:300]), "success")
    except (ValueError, workflow.WorkflowError) as exc:
        flash(str(exc) if isinstance(exc, workflow.WorkflowError) else "Choose a faculty member.", "danger")
    return redirect(url_for("reqs.detail", ref=ref))


# --------------------------------------------------------- print / pdf / files
@bp.route("/requests/<int:ref>/print")
@login_required
def print_view(ref):
    req, steps = _load_for_view(ref)
    show = workflow.can_see_confidential(g.user, req, steps)
    audit("request_printed", "request", req["request_uid"])
    return render_template("requests/print.html", req=req, steps=steps, history=workflow.get_history(req["id"]),
                           show=show, fields=_field_rows(req), printed_at=now_str())


@bp.route("/requests/<int:ref>/pdf")
@login_required
def pdf(ref):
    req, steps = _load_for_view(ref)
    show = workflow.can_see_confidential(g.user, req, steps)
    data = pdfgen.request_pdf(req, steps, workflow.get_history(req["id"]), current_app.config["COLLEGE_NAME"], show)
    audit("request_pdf", "request", req["request_uid"])
    return _pdf_response(data, "%s.pdf" % req["request_uid"])


@bp.route("/requests/<int:ref>/certificate")
@login_required
def certificate(ref):
    req, steps = _load_for_view(ref)
    if req["type_code"] != "bonafide" or req["status"] != "approved":
        abort(404)
    history = workflow.get_history(req["id"])
    approver = next((h for h in reversed(history) if h["action"] == "approved"), None)
    data = pdfgen.bonafide_pdf(req, approver["actor_name"] if approver else "Authorised signatory",
                               current_app.config["COLLEGE_NAME"])
    audit("certificate_downloaded", "request", req["request_uid"])
    return _pdf_response(data, "Bonafide-%s.pdf" % req["request_uid"])


def _pdf_response(data, name):
    resp = make_response(data)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["Content-Disposition"] = 'inline; filename="%s"' % name
    return resp


@bp.route("/attachments/<int:aid>")
@login_required
def attachment(aid):
    att = get_db().one("SELECT * FROM attachments WHERE id = %s", (aid,)) or abort(404)
    req, steps = _load_for_view(att["request_id"])
    if not workflow.can_see_confidential(g.user, req, steps):
        audit("access_denied", "attachment", aid, "confidential")
        abort(403)
    audit("attachment_downloaded", "attachment", aid, att["original_name"])
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], att["stored_name"], as_attachment=True,
                               download_name=att["original_name"], mimetype=att["mime_type"])
