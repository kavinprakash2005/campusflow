"""Reference + demo data. Everything goes through the real workflow engine, so the
demo requests carry genuine approval history, notifications and audit entries."""
import datetime as dt
import json
import os
import random

from flask import g
from werkzeug.security import generate_password_hash

import db as database
import forms
import workflow
from db import now_str

DEMO_PASSWORD = "Campus@123"

ROLES = [
    ("student", "Student", "Raises requests and tracks their progress."),
    ("faculty", "Faculty", "Raises personal requests; reviews student requests when assigned."),
    ("hod", "HOD", "Reviews department requests; approves, rejects, forwards or returns."),
    ("principal", "Principal", "College-wide oversight; decides requests routed to the Principal."),
    ("admin", "Admin", "Maintains users, departments, permissions and workflow rules. No approval authority."),
]

PERMISSIONS = [
    ("request.create", "Submit personal requests"),
    ("request.review", "Act on requests waiting at this role's approval stage"),
    ("request.return", "Return a request to the requester for clarification"),
    ("request.forward", "Forward a request to the Principal (HOD stage)"),
    ("request.assign", "Assign a faculty reviewer to department requests"),
    ("request.view_department", "View all requests of own department"),
    ("request.view_all", "View every request in the college (metadata only if confidential)"),
    ("confidential.view", "See reason, description and attachments of confidential requests"),
    ("reports.department", "Department reports and CSV export"),
    ("reports.college", "College-wide reports and statistics"),
    ("audit.decisions", "Decision audit history of approvals"),
    ("audit.system", "System activity / audit log"),
    ("users.manage", "Manage users"),
    ("departments.manage", "Manage departments"),
    ("roles.manage", "Manage role permissions"),
    ("workflow.manage", "Configure approval workflows"),
]

ROLE_PERMS = {
    "student": ["request.create"],
    "faculty": ["request.create", "request.review", "request.return"],
    "hod": ["request.create", "request.review", "request.return", "request.forward", "request.assign",
            "request.view_department", "reports.department"],
    "principal": ["request.review", "request.return", "reports.college", "audit.decisions"],
    "admin": ["request.view_all", "reports.college", "audit.system", "users.manage",
              "departments.manage", "roles.manage", "workflow.manage"],
}

DEPARTMENTS = [("CSE", "Computer Science & Engineering"), ("ECE", "Electronics & Communication Engineering"),
               ("MECH", "Mechanical Engineering")]

REQUEST_TYPES = [
    ("leave", "Leave", "Casual, medical or personal leave", "bi-calendar2-x"),
    ("od", "On-Duty (OD)", "Events, competitions and official college duty", "bi-briefcase"),
    ("bonafide", "Bonafide Certificate", "Certificate of enrolment for external use", "bi-patch-check"),
    ("permission", "Permission", "Short permission for a few hours", "bi-clock-history"),
    ("internship", "Internship", "Approval to undertake an internship", "bi-building"),
    ("other", "Other", "Any request not listed above", "bi-chat-square-text"),
]

# (type, requester_role) -> [(step_role, min_days)]   (order = pipeline order)
RULES = {
    ("leave", "student"): [("faculty", 0), ("hod", 0), ("principal", 10)],
    ("od", "student"): [("faculty", 0), ("hod", 0)],
    ("bonafide", "student"): [("hod", 0)],
    ("permission", "student"): [("faculty", 0)],
    ("internship", "student"): [("faculty", 0), ("hod", 0), ("principal", 0)],
    ("other", "student"): [("faculty", 0), ("hod", 0)],
    ("leave", "faculty"): [("hod", 0), ("principal", 8)],
    ("od", "faculty"): [("hod", 0)],
    ("permission", "faculty"): [("hod", 0)],
    ("other", "faculty"): [("hod", 0)],
    ("leave", "hod"): [("principal", 0)],
    ("od", "hod"): [("principal", 0)],
    ("permission", "hod"): [("principal", 0)],
    ("other", "hod"): [("principal", 0)],
}

# college_id, email, name, role, dept, designation, advisor college_id
USERS = [
    ("ADM001", "admin@campusflow.edu", "Anita Rao", "admin", None, "System Administrator", None),
    ("PRN001", "principal@campusflow.edu", "Dr. Meera Krishnan", "principal", None, "Principal", None),
    ("HOD-CSE", "hod.cse@campusflow.edu", "Dr. Suresh Iyer", "hod", "CSE", "Head of Department", None),
    ("HOD-ECE", "hod.ece@campusflow.edu", "Dr. Lakshmi Narayanan", "hod", "ECE", "Head of Department", None),
    ("HOD-MECH", "hod.mech@campusflow.edu", "Dr. Arjun Menon", "hod", "MECH", "Head of Department", None),
    ("FAC101", "kavitha.raman@campusflow.edu", "Prof. Kavitha Raman", "faculty", "CSE", "Associate Professor", None),
    ("FAC102", "rahul.verma@campusflow.edu", "Prof. Rahul Verma", "faculty", "CSE", "Assistant Professor", None),
    ("FAC103", "deepa.nair@campusflow.edu", "Prof. Deepa Nair", "faculty", "CSE", "Assistant Professor", None),
    ("FAC201", "vikram.singh@campusflow.edu", "Prof. Vikram Singh", "faculty", "ECE", "Associate Professor", None),
    ("FAC202", "priya.das@campusflow.edu", "Prof. Priya Das", "faculty", "ECE", "Assistant Professor", None),
    ("FAC301", "naveen.kumar@campusflow.edu", "Prof. Naveen Kumar", "faculty", "MECH", "Assistant Professor", None),
    ("21CSE001", "aarav.sharma@campusflow.edu", "Aarav Sharma", "student", "CSE", "B.E. CSE – Year 3", "FAC101"),
    ("21CSE002", "diya.patel@campusflow.edu", "Diya Patel", "student", "CSE", "B.E. CSE – Year 3", "FAC101"),
    ("21CSE003", "karthik.reddy@campusflow.edu", "Karthik Reddy", "student", "CSE", "B.E. CSE – Year 3", "FAC102"),
    ("21ECE001", "ishita.banerjee@campusflow.edu", "Ishita Banerjee", "student", "ECE", "B.E. ECE – Year 3", "FAC201"),
    ("21ECE002", "rohan.gupta@campusflow.edu", "Rohan Gupta", "student", "ECE", "B.E. ECE – Year 2", "FAC202"),
    ("21MEC001", "sneha.pillai@campusflow.edu", "Sneha Pillai", "student", "MECH", "B.E. MECH – Year 4", "FAC301"),
]

DEMO_LOGINS = [  # shown on the login screen when SHOW_DEMO_ACCOUNTS=1
    ("Student", "aarav.sharma@campusflow.edu"), ("Faculty", "kavitha.raman@campusflow.edu"),
    ("HOD", "hod.cse@campusflow.edu"), ("Principal", "principal@campusflow.edu"),
    ("Admin", "admin@campusflow.edu"),
]


def reset_reference_data(db):
    ts = now_str()
    for code, name, desc in ROLES:
        db.execute("INSERT INTO roles (code, name, description) VALUES (%s,%s,%s)", (code, name, desc))
    for code, desc in PERMISSIONS:
        db.execute("INSERT INTO permissions (code, description) VALUES (%s,%s)", (code, desc))
    for role, perms in ROLE_PERMS.items():
        rid = db.scalar("SELECT id FROM roles WHERE code = %s", (role,))
        for p in perms:
            pid = db.scalar("SELECT id FROM permissions WHERE code = %s", (p,))
            db.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES (%s,%s)", (rid, pid))
    for code, name in DEPARTMENTS:
        db.execute("INSERT INTO departments (code, name) VALUES (%s,%s)", (code, name))
    for code, name, desc, icon in REQUEST_TYPES:
        db.execute("INSERT INTO request_types (code, name, description, icon) VALUES (%s,%s,%s,%s)",
                   (code, name, desc, icon))
    for (tcode, rrole), steps in RULES.items():
        tid = db.scalar("SELECT id FROM request_types WHERE code = %s", (tcode,))
        for order, (srole, min_days) in enumerate(steps, start=1):
            db.execute("INSERT INTO workflow_rules (request_type_id, requester_role, step_order, step_role, "
                       "min_days) VALUES (%s,%s,%s,%s,%s)", (tid, rrole, order, srole, min_days))
    pw = generate_password_hash(DEMO_PASSWORD)
    for cid, email, name, role, dept, desig, _adv in USERS:
        db.execute(
            "INSERT INTO users (college_id, email, full_name, password_hash, role_id, department_id, designation, "
            "phone, is_active, failed_attempts, created_at) VALUES (%s,%s,%s,%s,"
            "(SELECT id FROM roles WHERE code = %s),(SELECT id FROM departments WHERE code = %s),%s,%s,1,0,%s)",
            (cid, email, name, pw, role, dept, desig, "98%08d" % random.Random(cid).randint(0, 99999999), ts))
    for u in USERS:
        if u[6]:  # advisor (derived-table form works on both MySQL and SQLite)
            db.execute("UPDATE users SET advisor_id = (SELECT id FROM (SELECT id FROM users WHERE college_id = %s) x) "
                       "WHERE college_id = %s", (u[6], u[0]))


# ----------------------------------------------------------------- demo requests
def _load(db, cid):
    user = db.one("SELECT u.*, r.code AS role, r.name AS role_name, d.name AS dept_name, d.code AS dept_code "
                  "FROM users u JOIN roles r ON r.id = u.role_id "
                  "LEFT JOIN departments d ON d.id = u.department_id WHERE u.college_id = %s", (cid,))
    rows = db.query("SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
                    "WHERE rp.role_id = %s", (user["role_id"],))
    user["perms"] = {r["code"] for r in rows}
    return user


def _d(offset):
    return (dt.date.today() + dt.timedelta(days=offset)).isoformat()


# (requester, type, age_days, form fields, [(actor, action, remark)], confidential)
def _scenarios():
    return [
        ("21CSE001", "leave", 24, dict(title="Fever and doctor's advice", reason="Viral fever, advised rest by doctor",
         description="Consulted the college medical officer; three days of bed rest recommended.",
         start_date=_d(-22), end_date=_d(-20), leave_type="Medical leave"),
         [("FAC101", "approve", "Medical slip verified."), ("HOD-CSE", "approve", "Approved.")], 0),
        ("21CSE001", "od", 1, dict(title="Smart India Hackathon – regional round", reason="Representing the college team",
         description="Team of four selected for the regional round.", start_date=_d(6), end_date=_d(7),
         event_name="Smart India Hackathon", venue="NIT Trichy", od_category="Technical event"), [], 0),
        ("21CSE001", "bonafide", 9, dict(title="Bonafide for passport application", reason="Required for passport renewal",
         description="", purpose="Passport", copies="2"),
         [("HOD-CSE", "approve", "Certificate may be issued.")], 0),
        ("21CSE001", "other", 3, dict(title="Elective change request", reason="Wish to switch from Cloud to AI elective",
         description="My project aligns better with the AI elective.", category="Academic"),
         [("FAC101", "return", "Please attach the elective-change form signed by the course coordinator.")], 0),
        ("21CSE001", "leave", 70, dict(title="Family function out of town", reason="Cousin's wedding in Madurai",
         description="", start_date=_d(-66), end_date=_d(-64), leave_type="Casual leave"),
         [("FAC101", "approve", "OK"), ("HOD-CSE", "reject", "Internal assessments are scheduled in that week.")], 0),
        ("21CSE002", "leave", 2, dict(title="Casual leave for a family visit", reason="Visiting grandparents",
         description="", start_date=_d(3), end_date=_d(4), leave_type="Casual leave"),
         [("FAC101", "approve", "Recommended.")], 0),
        ("21CSE002", "internship", 6, dict(title="Summer internship at Zoho", reason="Selected for a 6-week internship",
         description="Offer letter attached with the application.", start_date=_d(20), end_date=_d(64),
         company="Zoho Corporation", role_title="Software Developer Intern", mode="On-site"),
         [("FAC101", "approve", "Good opportunity."), ("HOD-CSE", "approve", "Approved; forwarding to Principal.")], 0),
        ("21CSE003", "permission", 5, dict(title="Permission to leave early", reason="Dental appointment at 3 PM",
         description="", start_date=_d(-4), end_date=_d(-4), from_time="14:30", to_time="16:30"),
         [("FAC102", "approve", "Granted.")], 0),
        ("21CSE003", "leave", 12, dict(title="Emergency leave", reason="Family emergency at home",
         description="", start_date=_d(-10), end_date=_d(-9), leave_type="Emergency leave"),
         [("FAC102", "reject", "Lab record submission is pending; please resolve it first.")], 0),
        ("21ECE001", "leave", 4, dict(title="Surgery and recovery", reason="Planned minor surgery and recovery period",
         description="Medical documents will be shared with the reviewing authority.", start_date=_d(10),
         end_date=_d(21), leave_type="Medical leave"),
         [("FAC201", "approve", "Documents seen."), ("HOD-ECE", "approve", "Forwarded as it exceeds 10 days.")], 1),
        ("21ECE002", "od", 0, dict(title="IEEE paper presentation", reason="Presenting our accepted paper",
         description="", start_date=_d(12), end_date=_d(13), event_name="IEEE Student Conference",
         venue="IIT Madras", od_category="Technical event"), [], 0),
        ("21MEC001", "internship", 40, dict(title="Internship at L&T Heavy Engineering", reason="Four-week industrial training",
         description="", start_date=_d(-30), end_date=_d(-2), company="L&T", role_title="Design Trainee",
         mode="On-site"),
         [("FAC301", "approve", "Fine."), ("HOD-MECH", "approve", "Approved."),
          ("PRN001", "approve", "Approved. Best wishes.")], 0),
        ("21MEC001", "leave", 0, dict(title="Medical check-up", reason="Annual health check-up",
         description="", start_date=_d(2), end_date=_d(2), leave_type="Medical leave"), [], 0),
        ("FAC101", "leave", 30, dict(title="Casual leave", reason="Personal work", description="",
         start_date=_d(-27), end_date=_d(-26), leave_type="Casual leave"),
         [("HOD-CSE", "approve", "Approved.")], 0),
        ("FAC102", "od", 2, dict(title="FDP on Cloud-Native Systems", reason="Faculty development programme",
         description="", start_date=_d(14), end_date=_d(18), event_name="AICTE FDP", venue="IIT Bombay (online)",
         od_category="Workshop / Seminar"), [], 0),
        ("FAC202", "leave", 5, dict(title="Extended leave for family matters", reason="Family commitments",
         description="", start_date=_d(9), end_date=_d(17), leave_type="Casual leave"),
         [("HOD-ECE", "approve", "Approved; exceeds 8 days so Principal approval is required.")], 0),
        ("HOD-CSE", "od", 1, dict(title="Board of Studies meeting", reason="Official university meeting",
         description="", start_date=_d(8), end_date=_d(8), event_name="Anna University BoS",
         venue="Chennai", od_category="Official college duty"), [], 0),
        ("HOD-ECE", "leave", 33, dict(title="Annual leave", reason="Annual family trip", description="",
         start_date=_d(-28), end_date=_d(-24), leave_type="Casual leave"),
         [("PRN001", "approve", "Approved.")], 0),
        ("21ECE002", "leave", 100, dict(title="Sports meet travel", reason="Inter-college sports meet",
         description="", start_date=_d(-97), end_date=_d(-95), leave_type="Academic leave"),
         [("FAC202", "approve", "OK"), ("HOD-ECE", "approve", "Approved")], 0),
        ("21CSE003", "od", 130, dict(title="Code-a-thon participation", reason="Inter-college coding contest",
         description="", start_date=_d(-128), end_date=_d(-127), event_name="CodeStorm", venue="VIT Vellore",
         od_category="Technical event"), [("FAC102", "approve", "OK"), ("HOD-CSE", "approve", "OK")], 0),
        ("21MEC001", "other", 80, dict(title="Hostel late-entry request", reason="Project lab work until late evening",
         description="", category="Hostel"), [("FAC301", "approve", "OK"), ("HOD-MECH", "approve", "OK")], 0),
    ]


def _backdate(db, rid, age_days, rng):
    created = dt.datetime.now() - dt.timedelta(days=age_days, hours=rng.randint(0, 6), minutes=rng.randint(0, 59))
    fmt = lambda x: x.strftime("%Y-%m-%d %H:%M:%S")
    hist = db.query("SELECT id FROM approval_history WHERE request_id = %s ORDER BY id", (rid,))
    last = created
    for i, h in enumerate(hist):
        last = created + dt.timedelta(hours=i * rng.randint(3, 20))
        db.execute("UPDATE approval_history SET created_at = %s WHERE id = %s", (fmt(last), h["id"]))
    req = db.one("SELECT status FROM requests WHERE id = %s", (rid,))
    done = fmt(last) if req["status"] in ("approved", "rejected") else None
    db.execute("UPDATE requests SET created_at = %s, updated_at = %s, completed_at = %s WHERE id = %s",
               (fmt(created), fmt(last), done, rid))
    db.execute("UPDATE notifications SET created_at = %s WHERE request_id = %s", (fmt(last), rid))


def _sample_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(path, pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, 760, "Medical slip (demo attachment)")
    c.setFont("Helvetica", 11)
    c.drawString(72, 735, "Patient advised 3 days of rest. This file is sample data for the CampusFlow demo.")
    c.save()


def seed_demo_requests(app, db):
    rng = random.Random(7)
    folder = app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    first = True
    for requester, tcode, age, fields, actions, conf in _scenarios():
        user = _load(db, requester)
        type_row = db.one("SELECT * FROM request_types WHERE code = %s", (tcode,))
        form = dict(fields)
        if conf:
            form["is_confidential"] = "on"
        cleaned, errors = forms.validate_request_form(form, tcode, allow_past_days=400)
        if errors:
            raise SystemExit("Seed scenario invalid (%s %s): %s" % (requester, tcode, errors))
        g.user = user
        req = workflow.create_request(user, type_row, cleaned)
        if first:
            stored = "demo_medical_slip.pdf"
            _sample_pdf(os.path.join(folder, stored))
            db.execute("INSERT INTO attachments (request_id, original_name, stored_name, mime_type, size_bytes, "
                       "uploaded_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                       (req["id"], "medical_slip.pdf", stored, "application/pdf",
                        os.path.getsize(os.path.join(folder, stored)), user["id"], now_str()))
            first = False
        for actor_cid, action, remark in actions:
            actor = _load(db, actor_cid)
            g.user = actor
            workflow.act(actor, workflow.get_request(req["id"]), action, remark)
        _backdate(db, req["id"], age, rng)
    g.user = None


def run(app, drop=False, with_demo=True):
    database.init_schema(app, drop_existing=drop)
    db = database.connect(app)
    try:
        if db.scalar("SELECT COUNT(*) FROM roles"):
            raise SystemExit("Database already contains data. Re-run with --drop to rebuild it.")
        reset_reference_data(db)
        db.commit()
        if with_demo:
            from flask import g as _g  # noqa: F401
            with app.test_request_context("/"):
                _g.db = db
                seed_demo_requests(app, db)
                db.commit()
    finally:
        try:
            db.close()
        except Exception:      # already closed by the request teardown
            pass
