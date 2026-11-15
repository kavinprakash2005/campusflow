"""Configurable approval workflow engine.

Pipeline per request = ordered steps taken from `workflow_rules` for
(request type, requester role). A request is snapshotted into `request_steps`
at submission time. Nothing is ever auto-approved: every step needs a human
reviewer who holds the `request.review` permission AND matches the step role.
Every decision is written to `approval_history` and `audit_logs`.
"""
import json

from core import ROLE_LABEL, STEP_LABEL, audit, notify
from db import get_db, now_str

REQ_SELECT = (
    "SELECT r.*, t.code AS type_code, t.name AS type_name, t.icon AS type_icon, "
    "u.full_name AS requester_name, u.college_id AS requester_cid, u.email AS requester_email, "
    "ur.code AS requester_role, d.name AS dept_name, d.code AS dept_code, "
    "(SELECT s.step_role FROM request_steps s WHERE s.request_id = r.id "
    " AND s.status IN ('pending','returned') ORDER BY s.step_order LIMIT 1) AS current_role "
    "FROM requests r JOIN request_types t ON t.id = r.request_type_id "
    "JOIN users u ON u.id = r.requester_id JOIN roles ur ON ur.id = u.role_id "
    "LEFT JOIN departments d ON d.id = r.department_id "
)


class WorkflowError(Exception):
    pass


def _scrub(req, text, limit):
    """Remarks on confidential requests never leak into the (wider-readable) audit log."""
    return "(remark hidden: confidential request)" if req["is_confidential"] else (text or "")[:limit]


def get_request(request_ref):
    """Fetch by numeric id or public request UID."""
    db = get_db()
    if isinstance(request_ref, int) or str(request_ref).isdigit():
        return db.one(REQ_SELECT + "WHERE r.id = %s", (int(request_ref),))
    return db.one(REQ_SELECT + "WHERE r.request_uid = %s", (str(request_ref),))


def get_steps(request_id):
    return get_db().query(
        "SELECT s.*, u.full_name AS assigned_name FROM request_steps s "
        "LEFT JOIN users u ON u.id = s.assigned_user_id "
        "WHERE s.request_id = %s ORDER BY s.step_order", (request_id,))


def get_history(request_id):
    return get_db().query(
        "SELECT h.*, u.full_name AS actor_name, r.code AS actor_role, s.step_role "
        "FROM approval_history h JOIN users u ON u.id = h.actor_id JOIN roles r ON r.id = u.role_id "
        "LEFT JOIN request_steps s ON s.id = h.step_id "
        "WHERE h.request_id = %s ORDER BY h.id", (request_id,))


def status_text(req):
    if req["status"] == "pending":
        return "Pending · %s review" % STEP_LABEL.get(req.get("current_role"), "Awaiting")
    return {"approved": "Approved", "rejected": "Rejected",
            "returned": "Returned for clarification", "cancelled": "Cancelled"}[req["status"]]


# ------------------------------------------------------------- pipeline build
def _find_hod(dept_id):
    if not dept_id:
        return None
    return get_db().one(
        "SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id "
        "WHERE r.code = 'hod' AND u.department_id = %s AND u.is_active = 1 ORDER BY u.id LIMIT 1",
        (dept_id,))


def build_pipeline(requester, type_id, days):
    rules = get_db().query(
        "SELECT step_role, min_days FROM workflow_rules "
        "WHERE request_type_id = %s AND requester_role = %s ORDER BY step_order",
        (type_id, requester["role"]))
    steps = []
    for rule in rules:
        if rule["min_days"] and days < rule["min_days"]:
            continue                                   # principal only "when required"
        role = rule["step_role"]
        if role == "faculty":
            if requester["role"] != "student" or not requester.get("advisor_id"):
                continue                               # nobody assigned -> step not applicable
            steps.append((role, requester["advisor_id"]))
        elif role == "hod":
            if requester["role"] == "hod":
                continue                               # nobody approves their own request
            hod = _find_hod(requester.get("department_id"))
            steps.append((role, hod["id"] if hod else None))
        elif role == "principal":
            steps.append((role, None))
    if not steps:                                      # never leave a request with no reviewer
        steps.append(("principal", None) if requester["role"] == "hod" else
                     ("hod", (_find_hod(requester.get("department_id")) or {}).get("id")))
    return steps


def _step_reviewers(step_role, assigned_user_id, dept_id):
    db = get_db()
    if step_role == "faculty":
        return [assigned_user_id] if assigned_user_id else []
    base = ("SELECT u.id FROM users u JOIN roles r ON r.id = u.role_id "
            "WHERE r.code = %s AND u.is_active = 1")
    if step_role == "hod":
        rows = db.query(base + " AND u.department_id = %s", ("hod", dept_id))
    else:
        rows = db.query(base, (step_role,))
    return [r["id"] for r in rows]


def _notify_step(req, step_role, assigned_user_id, title=None):
    for uid in _step_reviewers(step_role, assigned_user_id, req["department_id"]):
        notify(uid, title or "New request awaiting your review",
               "%s – %s (%s)" % (req["request_uid"], req["title"], req["requester_name"]), req["id"])


def _history(request_id, step_id, actor_id, action, remark, frm, to):
    get_db().execute(
        "INSERT INTO approval_history (request_id, step_id, actor_id, action, remark, from_status, "
        "to_status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (request_id, step_id, actor_id, action, remark, frm, to, now_str()))


# ------------------------------------------------------------------ submit
def create_request(user, type_row, cleaned):
    db = get_db()
    ts = now_str()
    rid = db.execute(
        "INSERT INTO requests (request_uid, requester_id, department_id, request_type_id, title, reason, "
        "description, start_date, end_date, days, extra, is_confidential, status, current_step, "
        "created_at, updated_at) VALUES (NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',1,%s,%s)",
        (user["id"], user["department_id"], type_row["id"], cleaned["title"], cleaned["reason"],
         cleaned["description"], cleaned["start_date"], cleaned["end_date"], cleaned["days"],
         cleaned["extra"], cleaned["is_confidential"], ts, ts))
    uid_text = "CF-%s-%05d" % (ts[:4], rid)
    db.execute("UPDATE requests SET request_uid = %s WHERE id = %s", (uid_text, rid))

    pipeline = build_pipeline(user, type_row["id"], cleaned["days"])
    first_step_id = None
    for order, (role, assigned) in enumerate(pipeline, start=1):
        sid = db.execute(
            "INSERT INTO request_steps (request_id, step_order, step_role, assigned_user_id, status) "
            "VALUES (%s,%s,%s,%s,%s)", (rid, order, role, assigned, "pending" if order == 1 else "waiting"))
        first_step_id = first_step_id or sid
    _history(rid, first_step_id, user["id"], "submitted", None, None, "pending")
    req = get_request(rid)
    notify(user["id"], "Request submitted", "%s – %s was submitted successfully." % (uid_text, req["title"]), rid)
    _notify_step(req, pipeline[0][0], pipeline[0][1])
    audit("request_submitted", "request", uid_text, "%s, %d day(s), route: %s" % (
        type_row["name"], cleaned["days"], " > ".join(p[0] for p in pipeline)))
    return req


# ------------------------------------------------------------ authorisation
def is_step_reviewer(user, step, req):
    """Does this user have authority to act on this step? (role + permission + not own request)"""
    if "request.review" not in user["perms"] or req["requester_id"] == user["id"]:
        return False
    role = step["step_role"]
    if role == "faculty":
        return user["role"] == "faculty" and step["assigned_user_id"] == user["id"]
    if role == "hod":
        return user["role"] == "hod" and user["department_id"] == req["department_id"]
    if role == "principal":
        return user["role"] == "principal"
    return False


def pending_step_for(user, req, steps=None):
    steps = steps if steps is not None else get_steps(req["id"])
    if req["status"] != "pending":
        return None
    for s in steps:
        if s["status"] == "pending":
            return s if is_step_reviewer(user, s, req) else None
    return None


def can_view(user, req, steps=None):
    if req["requester_id"] == user["id"]:
        return True
    perms = user["perms"]
    if "request.view_all" in perms:
        return True
    if "request.view_department" in perms and user["department_id"] == req["department_id"]:
        return True
    steps = steps if steps is not None else get_steps(req["id"])
    for s in steps:
        if s["status"] != "waiting" and is_step_reviewer(user, s, req):
            return True
        if s["step_role"] == "faculty" and s["assigned_user_id"] == user["id"] \
                and "request.review" in perms:
            return True
    return False


def can_see_confidential(user, req, steps=None):
    """Confidential content: owner, the reviewer currently holding it, or explicit permission."""
    if not req["is_confidential"]:
        return True
    if req["requester_id"] == user["id"] or "confidential.view" in user["perms"]:
        return True
    return pending_step_for(user, req, steps) is not None


def review_scope_sql(user):
    """SQL fragment + args selecting requests currently waiting on this user (needs alias s, r)."""
    if "request.review" not in user["perms"]:
        return "1 = 0", []
    if user["role"] == "faculty":
        return "(s.step_role = 'faculty' AND s.assigned_user_id = %s)", [user["id"]]
    if user["role"] == "hod":
        return "(s.step_role = 'hod' AND r.department_id = %s)", [user["department_id"]]
    if user["role"] == "principal":
        return "(s.step_role = 'principal')", []
    return "1 = 0", []


# -------------------------------------------------------------------- act
def act(user, req, action, remark):
    db = get_db()
    steps = get_steps(req["id"])
    step = pending_step_for(user, req, steps)
    if not step:
        audit("review_denied", "request", req["request_uid"], "action=%s" % action)
        raise WorkflowError("You are not authorised to act on this request at its current stage.")
    remark = (remark or "").strip()
    if len(remark) > 1000:
        raise WorkflowError("Remark is too long (max 1000 characters).")
    needs_remark = action in ("reject", "return", "remark")
    if needs_remark and len(remark) < 5:
        raise WorkflowError("Please add a remark of at least 5 characters.")
    if action == "return" and "request.return" not in user["perms"]:
        raise WorkflowError("You are not permitted to return requests for clarification.")
    if action == "forward" and not ("request.forward" in user["perms"] and step["step_role"] == "hod"):
        raise WorkflowError("Only the HOD can forward a request to the Principal.")
    if action not in ("approve", "reject", "return", "forward", "remark"):
        raise WorkflowError("Unknown action.")

    ts = now_str()
    title = req["title"]
    uid = req["request_uid"]

    if action == "remark":
        _history(req["id"], step["id"], user["id"], "remark", remark, req["status"], req["status"])
        notify(req["requester_id"], "New remark on your request", "%s: %s" % (uid, remark[:150]), req["id"])
        audit("request_remark", "request", uid, _scrub(req, remark, 200))
        return "Remark added."

    if action in ("approve", "forward"):
        db.execute("UPDATE request_steps SET status = 'approved' WHERE id = %s", (step["id"],))
        later = [s for s in steps if s["step_order"] > step["step_order"] and s["status"] == "waiting"]
        if action == "forward" and not any(s["step_role"] == "principal" for s in later):
            nxt_order = max(s["step_order"] for s in steps) + 1
            sid = db.execute(
                "INSERT INTO request_steps (request_id, step_order, step_role, assigned_user_id, status) "
                "VALUES (%s,%s,'principal',NULL,'waiting')", (req["id"], nxt_order))
            later.append({"id": sid, "step_order": nxt_order, "step_role": "principal",
                          "assigned_user_id": None, "status": "waiting"})
        if later:
            nxt = min(later, key=lambda s: s["step_order"])
            db.execute("UPDATE request_steps SET status = 'pending' WHERE id = %s", (nxt["id"],))
            db.execute("UPDATE requests SET current_step = %s, updated_at = %s WHERE id = %s",
                       (nxt["step_order"], ts, req["id"]))
            _history(req["id"], step["id"], user["id"], "forwarded" if action == "forward" else "approved",
                     remark or None, "pending", "pending")
            _notify_step(req, nxt["step_role"], nxt["assigned_user_id"])
            notify(req["requester_id"], "Request moved to %s" % STEP_LABEL[nxt["step_role"]],
                   "%s cleared %s review and is now awaiting %s." % (uid, STEP_LABEL[step["step_role"]],
                                                                    STEP_LABEL[nxt["step_role"]]), req["id"])
            audit("request_" + ("forwarded" if action == "forward" else "approved"), "request", uid,
                  "step %s -> %s; %s" % (step["step_role"], nxt["step_role"], _scrub(req, remark, 150)))
            return "Approved and forwarded to the %s." % STEP_LABEL[nxt["step_role"]]
        db.execute("UPDATE requests SET status = 'approved', current_step = 0, updated_at = %s, "
                   "completed_at = %s WHERE id = %s", (ts, ts, req["id"]))
        _history(req["id"], step["id"], user["id"], "approved", remark or None, "pending", "approved")
        notify(req["requester_id"], "Request approved", "%s – %s has been approved." % (uid, title), req["id"])
        audit("request_approved", "request", uid, "final approval by %s" % step["step_role"])
        return "Request approved. The requester has been notified."

    new_status = "rejected" if action == "reject" else "returned"
    db.execute("UPDATE request_steps SET status = %s WHERE id = %s",
               ("rejected" if action == "reject" else "returned", step["id"]))
    if action == "reject":
        db.execute("UPDATE request_steps SET status = 'skipped' WHERE request_id = %s AND status = 'waiting'",
                   (req["id"],))
    db.execute("UPDATE requests SET status = %s, updated_at = %s, completed_at = %s WHERE id = %s",
               (new_status, ts, ts if action == "reject" else None, req["id"]))
    _history(req["id"], step["id"], user["id"], "rejected" if action == "reject" else "returned",
             remark, "pending", new_status)
    notify(req["requester_id"],
           "Request rejected" if action == "reject" else "Clarification requested",
           "%s: %s" % (uid, remark[:180]), req["id"])
    audit("request_" + new_status, "request", uid, _scrub(req, remark, 200))
    return "Request rejected." if action == "reject" else "Returned to the requester for clarification."


def assign_reviewer(user, req, faculty_id, remark=""):
    """HOD routes a pending department request to a specific faculty reviewer."""
    if user["role"] != "hod" or "request.assign" not in user["perms"] \
            or user["department_id"] != req["department_id"] or req["requester_id"] == user["id"]:
        audit("assign_denied", "request", req["request_uid"])
        raise WorkflowError("You are not authorised to assign reviewers for this request.")
    if req["status"] != "pending":
        raise WorkflowError("Only pending requests can be assigned.")
    db = get_db()
    fac = db.one("SELECT u.id, u.full_name FROM users u JOIN roles r ON r.id = u.role_id "
                 "WHERE u.id = %s AND r.code = 'faculty' AND u.is_active = 1 AND u.department_id = %s",
                 (faculty_id, req["department_id"]))
    if not fac or fac["id"] == req["requester_id"]:
        raise WorkflowError("Choose an active faculty member from this department.")
    steps = get_steps(req["id"])
    current = next((s for s in steps if s["status"] == "pending"), None)
    fac_step = next((s for s in steps if s["step_role"] == "faculty" and s["status"] in ("pending", "waiting")), None)
    ts = now_str()
    if fac_step:
        db.execute("UPDATE request_steps SET assigned_user_id = %s WHERE id = %s", (fac["id"], fac_step["id"]))
        sid, now_pending = fac_step["id"], fac_step["status"] == "pending"
    elif current and current["step_role"] == "hod":
        db.execute("UPDATE request_steps SET step_order = step_order + 1 WHERE request_id = %s "
                   "AND step_order >= %s", (req["id"], current["step_order"]))
        sid = db.execute("INSERT INTO request_steps (request_id, step_order, step_role, assigned_user_id, status) "
                         "VALUES (%s,%s,'faculty',%s,'pending')", (req["id"], current["step_order"], fac["id"]))
        db.execute("UPDATE request_steps SET status = 'waiting' WHERE id = %s", (current["id"],))
        db.execute("UPDATE requests SET current_step = %s, updated_at = %s WHERE id = %s",
                   (current["step_order"], ts, req["id"]))
        now_pending = True
    else:
        raise WorkflowError("This request is no longer at a stage where a faculty reviewer can be assigned.")
    _history(req["id"], sid, user["id"], "assigned", ("Assigned to %s. %s" % (fac["full_name"], remark or "")).strip(),
             "pending", "pending")
    if now_pending:
        notify(fac["id"], "Request assigned to you for review",
               "%s – %s (%s)" % (req["request_uid"], req["title"], req["requester_name"]), req["id"])
    notify(req["requester_id"], "Faculty reviewer assigned", "%s has been assigned to %s for review." % (
        req["request_uid"], fac["full_name"]), req["id"])
    audit("request_assigned", "request", req["request_uid"], "reviewer=%s" % fac["full_name"])
    return "Assigned to %s." % fac["full_name"]


def resubmit(user, req, cleaned):
    if req["requester_id"] != user["id"] or req["status"] != "returned":
        raise WorkflowError("This request cannot be resubmitted.")
    db = get_db()
    ts = now_str()
    db.execute(
        "UPDATE requests SET title=%s, reason=%s, description=%s, start_date=%s, end_date=%s, days=%s, "
        "extra=%s, is_confidential=%s, status='pending', updated_at=%s WHERE id=%s",
        (cleaned["title"], cleaned["reason"], cleaned["description"], cleaned["start_date"],
         cleaned["end_date"], cleaned["days"], cleaned["extra"], cleaned["is_confidential"], ts, req["id"]))
    step = db.one("SELECT * FROM request_steps WHERE request_id = %s AND status = 'returned' "
                  "ORDER BY step_order LIMIT 1", (req["id"],))
    db.execute("UPDATE request_steps SET status = 'pending' WHERE id = %s", (step["id"],))
    _history(req["id"], step["id"], user["id"], "resubmitted", None, "returned", "pending")
    _notify_step(req, step["step_role"], step["assigned_user_id"], "Request resubmitted for review")
    audit("request_resubmitted", "request", req["request_uid"])


def cancel(user, req):
    if req["requester_id"] != user["id"] or req["status"] not in ("pending", "returned"):
        raise WorkflowError("This request cannot be cancelled.")
    db = get_db()
    ts = now_str()
    db.execute("UPDATE request_steps SET status = 'skipped' WHERE request_id = %s "
               "AND status IN ('waiting','pending','returned')", (req["id"],))
    db.execute("UPDATE requests SET status='cancelled', updated_at=%s, completed_at=%s WHERE id=%s",
               (ts, ts, req["id"]))
    _history(req["id"], None, user["id"], "cancelled", None, req["status"], "cancelled")
    audit("request_cancelled", "request", req["request_uid"])


def extra_of(req):
    try:
        return json.loads(req.get("extra") or "{}")
    except ValueError:
        return {}
