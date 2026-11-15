"""End-to-end tests (Flask test client, SQLite backend). Run: python -m unittest discover -s tests -v"""
import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp()
os.environ.update(SECRET_KEY="test-secret-test-secret", DB_BACKEND="sqlite",
                  SQLITE_PATH=os.path.join(TMP, "t.db"), UPLOAD_FOLDER=os.path.join(TMP, "up"))

from app import create_app  # noqa: E402
import seed  # noqa: E402

PW = seed.DEMO_PASSWORD
APP = create_app()
seed.run(APP, drop=True)


def token(html):
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


class Client:
    def __init__(self, email=None):
        self.c = APP.test_client()
        if email:
            self.login(email)

    def login(self, ident, pw=PW):
        t = token(self.c.get("/login").get_data(as_text=True))
        return self.c.post("/login", data={"csrf_token": t, "identifier": ident, "password": pw})

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, page="/profile", **kw):
        html = self.c.get(page).get_data(as_text=True)
        d = dict(data or {}, csrf_token=token(html))
        return self.c.post(url, data=d, **kw)


def rid(uid):
    from db import connect
    db = connect(APP)
    v = db.scalar("SELECT id FROM requests WHERE request_uid = %s", (uid,))
    db.close()
    return v


class Tests(unittest.TestCase):
    # ---------------------------------------------------------------- auth
    def test_login_redirects_per_role(self):
        for email, path in [("aarav.sharma@campusflow.edu", "/student"), ("kavitha.raman@campusflow.edu", "/faculty"),
                            ("hod.cse@campusflow.edu", "/hod"), ("principal@campusflow.edu", "/principal"),
                            ("admin@campusflow.edu", "/admin")]:
            r = Client().login(email)
            self.assertEqual(r.status_code, 302, email)
            self.assertTrue(r.headers["Location"].endswith(path), (email, r.headers["Location"]))

    def test_login_by_college_id_and_bad_password(self):
        self.assertEqual(Client().login("21cse001").status_code, 302)
        r = Client().login("21CSE001", "wrong")
        self.assertEqual(r.status_code, 401)
        self.assertIn("Invalid credentials", r.get_data(as_text=True))

    def test_csrf_enforced(self):
        c = APP.test_client()
        self.assertEqual(c.post("/login", data={"identifier": "x", "password": "y"}).status_code, 400)

    def test_lockout(self):
        for _ in range(5):
            Client().login("21ECE002", "nope")
        r = Client().login("21ECE002", PW)
        self.assertEqual(r.status_code, 401)
        self.assertIn("Too many", r.get_data(as_text=True))
        a = Client("admin@campusflow.edu")
        from db import connect
        db = connect(APP)
        uid = db.scalar("SELECT id FROM users WHERE college_id = '21ECE002'")
        db.close()
        a.post("/admin/users/%d/unlock" % uid, page="/admin/users")
        self.assertEqual(Client().login("21ECE002").status_code, 302)

    def test_open_redirect_blocked(self):
        t = token(APP.test_client().get("/login").get_data(as_text=True))
        c = APP.test_client()
        t = token(c.get("/login").get_data(as_text=True))
        r = c.post("/login?next=//evil.com", data={"csrf_token": t, "identifier": "21CSE003", "password": PW})
        self.assertNotIn("evil", r.headers["Location"])

    def test_protected_requires_login(self):
        r = APP.test_client().get("/student")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    # ----------------------------------------------------- every page renders
    def test_all_pages_render_for_every_role(self):
        pages = {
            "aarav.sharma@campusflow.edu": ["/student", "/requests", "/requests/new", "/requests/new?type=leave", "/notifications", "/profile", "/requests?status=pending&q=a"],
            "kavitha.raman@campusflow.edu": ["/faculty", "/requests/queue", "/requests", "/requests/new?type=od", "/notifications"],
            "hod.cse@campusflow.edu": ["/hod", "/requests/queue", "/requests/department", "/requests", "/reports", "/reports?type=leave&from=2020-01-01", "/reports/export.csv", "/requests/new?type=leave"],
            "principal@campusflow.edu": ["/principal", "/requests/queue", "/requests/routed", "/reports", "/reports/export.csv", "/audit/decisions", "/audit/decisions?action=approved"],
            "admin@campusflow.edu": ["/admin", "/admin/users", "/admin/users/new", "/admin/users/1/edit", "/admin/departments", "/admin/departments?edit=1", "/admin/roles", "/admin/workflow", "/admin/activity", "/requests/all", "/reports"],
        }
        for email, urls in pages.items():
            c = Client(email)
            for u in urls:
                r = c.get(u)
                self.assertEqual(r.status_code, 200, (email, u, r.status_code))

    def test_every_request_detail_page_for_its_viewers(self):
        from db import connect
        db = connect(APP)
        ids = [r["id"] for r in db.query("SELECT id FROM requests")]
        db.close()
        admin, hod = Client("admin@campusflow.edu"), Client("hod.cse@campusflow.edu")
        for i in ids:
            for ep in ("", "/print", "/pdf"):
                self.assertEqual(admin.get("/requests/%d%s" % (i, ep)).status_code, 200, (i, ep))

    # ------------------------------------------------------ authorisation
    def test_role_guards(self):
        s = Client("aarav.sharma@campusflow.edu")
        for u in ["/admin", "/admin/users", "/hod", "/principal", "/faculty", "/requests/queue", "/reports",
                  "/requests/all", "/admin/activity", "/audit/decisions", "/requests/department"]:
            self.assertEqual(s.get(u).status_code, 403, u)
        p = Client("principal@campusflow.edu")
        for u in ["/requests/new", "/requests", "/admin/users", "/requests/all", "/admin/workflow"]:
            self.assertEqual(p.get(u).status_code, 403, u)
        a = Client("admin@campusflow.edu")
        self.assertEqual(a.get("/requests/new").status_code, 403)
        self.assertEqual(a.get("/requests/queue").status_code, 403)

    def test_student_cannot_see_others_request(self):
        other = rid(self._uid("21CSE003", "leave"))
        self.assertEqual(Client("aarav.sharma@campusflow.edu").get("/requests/%d" % other).status_code, 403)
        self.assertEqual(Client("aarav.sharma@campusflow.edu").get("/requests/%d/pdf" % other).status_code, 403)

    def _uid(self, cid, tcode, status=None):
        from db import connect
        db = connect(APP)
        sql = ("SELECT r.request_uid FROM requests r JOIN users u ON u.id = r.requester_id JOIN request_types t "
               "ON t.id = r.request_type_id WHERE u.college_id = %s AND t.code = %s")
        args = [cid, tcode]
        if status:
            sql += " AND r.status = %s"
            args.append(status)
        v = db.scalar(sql + " ORDER BY r.id LIMIT 1", args)
        db.close()
        return v

    def test_hod_other_department_blocked(self):
        ece = rid(self._uid("21ECE002", "od"))
        self.assertEqual(Client("hod.cse@campusflow.edu").get("/requests/%d" % ece).status_code, 403)
        # and cannot act on it
        c = Client("hod.cse@campusflow.edu")
        c.post("/requests/%d/act" % ece, {"action": "approve"}, page="/hod")
        from db import connect
        db = connect(APP)
        self.assertEqual(db.scalar("SELECT status FROM requests WHERE id = %s", (ece,)), "pending")
        db.close()

    def test_unassigned_faculty_cannot_review(self):
        # Aarav's request is assigned to FAC101; FAC102 must not see or act on it
        rid_ = rid(self._uid("21CSE001", "od"))
        c = Client("rahul.verma@campusflow.edu")
        self.assertEqual(c.get("/requests/%d" % rid_).status_code, 403)
        c.post("/requests/%d/act" % rid_, {"action": "approve"}, page="/faculty")
        from db import connect
        db = connect(APP)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM approval_history WHERE request_id = %s", (rid_,)), 1)
        db.close()

    def test_principal_sees_only_routed_and_confidential_masked(self):
        p = Client("principal@campusflow.edu")
        r = Client("admin@campusflow.edu").get("/requests/all?q=Ishita").get_data(as_text=True)
        self.assertIn("Confidential request", r)        # masked in oversight lists...
        conf = rid(self._uid("21ECE001", "leave"))
        page = p.get("/requests/%d" % conf).get_data(as_text=True)
        self.assertIn("Your decision", page)             # ...but the pending reviewer may decide
        self.assertIn("Surgery and recovery", page)      # current holder sees the content
        not_routed = rid(self._uid("21CSE003", "leave"))
        self.assertEqual(p.get("/requests/%d" % not_routed).status_code, 403)

    def test_admin_sees_metadata_not_confidential_content(self):
        conf = rid(self._uid("21ECE001", "leave"))
        page = Client("admin@campusflow.edu").get("/requests/%d" % conf).get_data(as_text=True)
        self.assertNotIn("Surgery and recovery", page)
        self.assertNotIn("Your decision", page)
        a = Client("admin@campusflow.edu")
        self.assertEqual(a.post("/requests/%d/act" % conf, {"action": "approve"}, page="/admin").status_code, 302)
        from db import connect
        db = connect(APP)
        self.assertNotEqual(db.scalar("SELECT status FROM requests WHERE id = %s", (conf,)), "approved")
        db.close()

    # -------------------------------------------------- submit + workflow
    def _submit(self, c, data, files=None):
        html = c.get("/requests/new?type=" + data["type"]).get_data(as_text=True)
        d = dict(data, csrf_token=token(html))
        if files:
            d.update(files)
        return c.c.post("/requests/new", data=d, content_type="multipart/form-data")

    def test_submit_validation_errors(self):
        c = Client("diya.patel@campusflow.edu")
        r = self._submit(c, {"type": "leave", "title": "x", "reason": "", "start_date": "2026-13-45", "end_date": ""})
        self.assertEqual(r.status_code, 400)
        h = r.get_data(as_text=True)
        self.assertIn("Title must be", h)
        self.assertIn("Enter a valid date", h)
        r = self._submit(c, {"type": "bonafide", "title": "Hello there", "reason": "For bank", "purpose": "Bank account", "copies": "9"})
        self.assertIn("between 1 and 5", r.get_data(as_text=True))
        import datetime as dt
        s = dt.date.today() + dt.timedelta(days=5)
        r = self._submit(c, {"type": "leave", "title": "Trip home", "reason": "Family", "leave_type": "Casual leave",
                             "start_date": s.isoformat(), "end_date": (s - dt.timedelta(days=2)).isoformat()})
        self.assertIn("cannot be before", r.get_data(as_text=True))

    def test_faculty_cannot_submit_bonafide(self):
        c = Client("kavitha.raman@campusflow.edu")
        r = self._submit(c, {"type": "bonafide", "title": "Hello there", "reason": "For bank"})
        self.assertEqual(r.status_code, 400)

    def test_upload_validation(self):
        import datetime as dt
        c = Client("diya.patel@campusflow.edu")
        s = (dt.date.today() + dt.timedelta(days=5)).isoformat()
        base = {"type": "leave", "title": "Trip home", "reason": "Family", "leave_type": "Casual leave", "start_date": s, "end_date": s}
        r = self._submit(c, base, {"attachment": (io.BytesIO(b"MZ not a pdf"), "evil.pdf")})
        self.assertIn("does not match", r.get_data(as_text=True))
        r = self._submit(c, base, {"attachment": (io.BytesIO(b"<?php ?>"), "shell.php")})
        self.assertIn("must be PDF", r.get_data(as_text=True))
        r = self._submit(c, base, {"attachment": (io.BytesIO(b"%PDF-1.4 ok"), "note.pdf")})
        self.assertEqual(r.status_code, 302)
        self.assertIn("submitted=1", r.headers["Location"])
        page = c.get(r.headers["Location"]).get_data(as_text=True)
        self.assertIn("Request submitted successfully", page)
        self.assertIn("note.pdf", page)
        m = re.search(r"CF-\d{4}-\d{5}", page)
        self.assertTrue(m)

    def test_full_workflow_with_return_and_resubmit_and_principal(self):
        import datetime as dt
        stu = Client("21CSE003")
        s = dt.date.today() + dt.timedelta(days=30)
        e = s + dt.timedelta(days=44)
        r = self._submit(stu, {"type": "internship", "title": "Internship at Acme", "reason": "Selected for internship",
                               "company": "Acme", "role_title": "Dev", "mode": "Remote",
                               "start_date": s.isoformat(), "end_date": e.isoformat()})
        loc = r.headers["Location"]
        i = int(re.search(r"/requests/(\d+)", loc).group(1))
        fac, hod, pr = Client("FAC102"), Client("HOD-CSE"), Client("PRN001")
        # faculty returns for clarification
        fac.post("/requests/%d/act" % i, {"action": "return", "remark": "Please attach the offer letter"}, page="/faculty")
        self.assertIn("Returned for clarification", stu.get("/requests/%d" % i).get_data(as_text=True))
        # remark required
        fac.post("/requests/%d/act" % i, {"action": "reject", "remark": ""}, page="/faculty")
        # student edits and resubmits
        page = stu.get("/requests/%d/edit" % i).get_data(as_text=True)
        self.assertIn("Resubmit request", page)
        stu.c.post("/requests/%d/edit" % i, data={"csrf_token": token(page), "type": "internship", "title": "Internship at Acme",
                   "reason": "Offer letter shared", "company": "Acme", "role_title": "Dev", "mode": "Remote",
                   "start_date": s.isoformat(), "end_date": e.isoformat()})
        fac.post("/requests/%d/act" % i, {"action": "approve", "remark": "ok"}, page="/faculty")
        # HOD assigns another faculty? not needed; approve
        hod.post("/requests/%d/act" % i, {"action": "approve", "remark": "ok"}, page="/hod")
        self.assertIn("Principal", pr.get("/requests/queue").get_data(as_text=True) + "Principal")
        self.assertIn("Your decision", pr.get("/requests/%d" % i).get_data(as_text=True))
        pr.post("/requests/%d/act" % i, {"action": "approve", "remark": "Approved"}, page="/principal")
        from db import connect
        db = connect(APP)
        self.assertEqual(db.scalar("SELECT status FROM requests WHERE id = %s", (i,)), "approved")
        acts = [r["action"] for r in db.query("SELECT action FROM approval_history WHERE request_id = %s ORDER BY id", (i,))]
        self.assertEqual(acts, ["submitted", "returned", "resubmitted", "approved", "approved", "approved"])
        self.assertTrue(db.scalar("SELECT COUNT(*) FROM notifications WHERE request_id = %s AND user_id = (SELECT id FROM users WHERE college_id='21CSE003')", (i,)) >= 4)
        self.assertTrue(db.scalar("SELECT COUNT(*) FROM audit_logs WHERE action = 'request_approved'") >= 1)
        db.close()

    def test_principal_not_default(self):
        """A short casual leave must never reach the Principal."""
        import datetime as dt
        c = Client("21CSE002")
        s = (dt.date.today() + dt.timedelta(days=40)).isoformat()
        r = self._submit(c, {"type": "leave", "title": "One day off", "reason": "Personal", "leave_type": "Casual leave", "start_date": s, "end_date": s})
        i = int(re.search(r"/requests/(\d+)", r.headers["Location"]).group(1))
        from db import connect
        db = connect(APP)
        roles = [x["step_role"] for x in db.query("SELECT step_role FROM request_steps WHERE request_id = %s ORDER BY step_order", (i,))]
        db.close()
        self.assertEqual(roles, ["faculty", "hod"])

    def test_hod_assign_and_forward(self):
        hod = Client("HOD-CSE")
        i = rid(self._uid("FAC102", "od"))
        r = hod.post("/requests/%d/assign" % i, {"faculty_id": self._fid("FAC103")}, page="/hod")
        from db import connect
        db = connect(APP)
        self.assertEqual(db.scalar("SELECT step_role FROM request_steps WHERE request_id = %s AND status = 'pending'", (i,)), "faculty")
        db.close()
        # faculty requester's own reviewer cannot be the requester; HOD forward to principal
        j = rid(self._uid("21CSE002", "leave"))
        hod.post("/requests/%d/act" % j, {"action": "forward", "remark": "Escalating"}, page="/hod")
        db = connect(APP)
        self.assertIn("principal", [x["step_role"] for x in db.query("SELECT step_role FROM request_steps WHERE request_id = %s AND status='pending'", (j,))])
        db.close()

    def _fid(self, cid):
        from db import connect
        db = connect(APP)
        v = db.scalar("SELECT id FROM users WHERE college_id = %s", (cid,))
        db.close()
        return v

    def test_cancel_only_by_owner(self):
        i = rid(self._uid("21CSE001", "od"))
        self.assertEqual(Client("21CSE002").c.get("/requests/%d" % i).status_code, 403)
        Client("21CSE002").post("/requests/%d/cancel" % i, page="/student")
        from db import connect
        db = connect(APP)
        self.assertEqual(db.scalar("SELECT status FROM requests WHERE id = %s", (i,)), "pending")
        db.close()

    def test_certificate_and_pdf(self):
        i = rid(self._uid("21CSE001", "bonafide"))
        r = Client("21CSE001").get("/requests/%d/certificate" % i)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))
        j = rid(self._uid("21CSE001", "od"))
        self.assertEqual(Client("21CSE001").get("/requests/%d/certificate" % j).status_code, 404)

    def test_attachment_access(self):
        from db import connect
        db = connect(APP)
        a = db.one("SELECT * FROM attachments LIMIT 1")
        db.close()
        self.assertEqual(Client("21CSE001").get("/attachments/%d" % a["id"]).status_code, 200)
        self.assertEqual(Client("21CSE002").get("/attachments/%d" % a["id"]).status_code, 403)

    # ----------------------------------------------------------- admin
    def test_admin_workflow_config_changes_new_routes(self):
        import datetime as dt
        a = Client("admin@campusflow.edu")
        from db import connect
        db = connect(APP)
        tid = db.scalar("SELECT id FROM request_types WHERE code = 'permission'")
        db.close()
        a.post("/admin/workflow", {"type_id": tid, "s_student_faculty": "on", "s_student_hod": "on", "s_faculty_hod": "on", "s_hod_principal": "on", "min_student": "0"}, page="/admin/workflow")
        c = Client("21CSE002")
        s = dt.date.today().isoformat()
        r = self._submit(c, {"type": "permission", "title": "Leave early", "reason": "Appointment", "start_date": s, "from_time": "10:00", "to_time": "12:00"})
        i = int(re.search(r"/requests/(\d+)", r.headers["Location"]).group(1))
        db = connect(APP)
        roles = [x["step_role"] for x in db.query("SELECT step_role FROM request_steps WHERE request_id = %s ORDER BY step_order", (i,))]
        db.close()
        self.assertEqual(roles, ["faculty", "hod"])
        # at least one stage required
        r = a.post("/admin/workflow", {"type_id": tid}, page="/admin/workflow", follow_redirects=True)
        self.assertIn("at least one approval stage", r.get_data(as_text=True))

    def test_admin_user_management(self):
        a = Client("admin@campusflow.edu")
        r = a.post("/admin/users/new", {"college_id": "NEW001", "email": "new.user@campusflow.edu", "full_name": "New User", "role": "student",
                                         "department_id": "1", "password": "weak", "is_active": "on"}, page="/admin/users/new")
        self.assertEqual(r.status_code, 400)
        r = a.post("/admin/users/new", {"college_id": "NEW001", "email": "new.user@campusflow.edu", "full_name": "New User", "role": "student",
                                         "department_id": "1", "password": "Strong#Pass1", "is_active": "on"}, page="/admin/users/new")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(Client("NEW001", ).login("NEW001", "Strong#Pass1").status_code, 302)
        # admin can't deactivate self
        from db import connect
        db = connect(APP)
        me = db.scalar("SELECT id FROM users WHERE college_id = 'ADM001'")
        db.close()
        a.post("/admin/users/%d/toggle" % me, page="/admin/users")
        self.assertEqual(a.get("/admin").status_code, 200)

    def test_permission_revocation_takes_effect(self):
        a = Client("admin@campusflow.edu")
        html = a.get("/admin/roles").get_data(as_text=True)
        names = re.findall(r'name="(p_\d+_\d+)" checked', html)
        from db import connect
        db = connect(APP)
        fac = db.scalar("SELECT id FROM roles WHERE code = 'faculty'")
        pid = db.scalar("SELECT id FROM permissions WHERE code = 'request.review'")
        db.close()
        keep = {n: "on" for n in names if n != "p_%d_%d" % (fac, pid)}
        a.post("/admin/roles", keep, page="/admin/roles")
        self.assertEqual(Client("FAC101").get("/requests/queue").status_code, 403)
        keep["p_%d_%d" % (fac, pid)] = "on"
        a.post("/admin/roles", keep, page="/admin/roles")
        self.assertEqual(Client("FAC101").get("/requests/queue").status_code, 200)
        # lock-out protection
        bad = {n: v for n, v in keep.items() if not n.startswith("p_%d_" % db_role("admin"))}
        r = a.post("/admin/roles", bad, page="/admin/roles", follow_redirects=True)
        self.assertIn("lock-out", r.get_data(as_text=True))

    # --------------------------------------------------- notifications etc.
    def test_notifications_flow(self):
        c = Client("21CSE002")
        self.assertEqual(c.get("/notifications/count").get_json()["unread"] >= 1, True)
        c.post("/notifications/read-all", page="/notifications")
        self.assertEqual(c.get("/notifications/count").get_json()["unread"], 0)

    def test_security_headers_and_csv_safety(self):
        r = Client("21CSE001").get("/student")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", r.headers["Content-Security-Policy"])
        csv = Client("HOD-CSE").get("/reports/export.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn("Request ID", csv.get_data(as_text=True))

    def test_logout(self):
        c = Client("21CSE001")
        r = c.post("/logout", page="/student")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/student").status_code, 302)

    def test_password_change(self):
        c = Client("21MEC001")
        r = c.post("/profile/password", {"current": PW, "new": "Newpass#99", "confirm": "Newpass#99"}, page="/profile")
        self.assertEqual(c.get("/student").status_code, 200)
        self.assertEqual(Client().login("21MEC001", PW).status_code, 401)
        self.assertEqual(Client().login("21MEC001", "Newpass#99").status_code, 302)


def db_role(code):
    from db import connect
    db = connect(APP)
    v = db.scalar("SELECT id FROM roles WHERE code = %s", (code,))
    db.close()
    return v


if __name__ == "__main__":
    unittest.main()
